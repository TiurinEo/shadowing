"""Эндпоинты анализа через Vocametrix. Два независимых запроса: сбой одного не мешает другому."""
import logging
import re
import threading

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

import audio
import vocametrix as vm
from analysis import prosody_view, pronunciation_view

log = logging.getLogger("vocametrix")
router = APIRouter(prefix="/api/vocametrix")

# Конвертация аудио — единственное тяжёлое место на слабом сервере (mem_limit 700m)
_convert = threading.BoundedSemaphore(2)

LOCALES = {
    "en": "en-US", "fr": "fr-FR", "de": "de-DE", "es": "es-ES", "it": "it-IT", "pt": "pt-BR",
    "ru": "ru-RU", "ja": "ja-JP", "ko": "ko-KR", "zh": "zh-CN", "nl": "nl-NL", "pl": "pl-PL",
    "tr": "tr-TR", "ar": "ar-SA", "hi": "hi-IN", "sv": "sv-SE", "uk": "uk-UA",
}
LOCALE_RE = re.compile(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?")
MAX_TEXT = 1000


def to_locale(lang: str) -> str:
    lang = (lang or "en").strip()
    if not LOCALE_RE.fullmatch(lang):
        raise HTTPException(400, detail=_err("bad_request", "Некорректный язык", False))
    if "-" in lang:
        base, region = lang.split("-", 1)
        return f"{base.lower()}-{region.upper() if len(region) == 2 else region}"
    return LOCALES.get(lang.lower(), f"{lang.lower()}-{lang.upper()}")


def _err(code: str, message: str, retryable: bool = True, retry_after: int | None = None) -> dict:
    return {"code": code, "message": message, "retryable": retryable, "retryAfter": retry_after}


def _client() -> vm.Vocametrix:
    key = vm.api_key()
    if not key:
        raise HTTPException(503, detail=_err("not_configured", "Анализ Vocametrix не настроен на сервере (нет VOCAMETRIX_API_KEY)", False))
    return vm.Vocametrix(key)


def _guard_size(request: Request):
    n = request.headers.get("content-length")
    if n and n.isdigit() and int(n) > audio.MAX_UPLOAD_BYTES + 64 * 1024:
        raise HTTPException(413, detail=_err("too_large", f"Запись слишком большая (максимум {audio.MAX_UPLOAD_BYTES // 1024 // 1024} МБ)", False))


def _read_upload(upload: UploadFile) -> bytes:
    data = upload.file.read(audio.MAX_UPLOAD_BYTES + 1)
    if len(data) > audio.MAX_UPLOAD_BYTES:
        raise HTTPException(413, detail=_err("too_large", f"Запись слишком большая (максимум {audio.MAX_UPLOAD_BYTES // 1024 // 1024} МБ)", False))
    return data


def _run(fn):
    try:
        return fn()
    except audio.AudioError as e:
        raise HTTPException(e.status, detail=_err(e.code, e.message, e.code in ("original_unavailable",)))
    except vm.VocametrixError as e:
        raise HTTPException(e.status, detail=_err(e.code, e.message, e.retryable, e.retry_after))
    except HTTPException:
        raise
    except Exception:
        log.exception("Необработанная ошибка анализа")
        raise HTTPException(500, detail=_err("internal", "Внутренняя ошибка анализа. Попробуйте ещё раз."))


@router.get("/status")
def status():
    return {
        "enabled": vm.enabled(),
        "minSeconds": audio.MIN_SEC,
        "maxSeconds": audio.MAX_SEC,
        "maxUploadMB": audio.MAX_UPLOAD_BYTES // 1024 // 1024,
        "requestsLeft": vm.guard.remaining(),
        "requestsPerAnalysis": vm.ANALYSIS_COST,
    }


@router.post("/pronunciation")
def pronunciation(request: Request, audio_file: UploadFile = File(..., alias="audio"), text: str = Form(...), lang: str = Form("en")):
    _guard_size(request)
    text = re.sub(r"\s+", " ", text).strip()
    if not text or len(text) > MAX_TEXT:
        raise HTTPException(400, detail=_err("bad_request", "Пустой или слишком длинный текст фрагмента", False))
    locale = to_locale(lang)
    data = _read_upload(audio_file)

    def work():
        client = _client()
        try:
            vm.guard.check(vm.ANALYSIS_COST["pronunciation"])
            with _convert:
                wav, _ = audio.prepare_recording(data)
            resp = client.assess_pronunciation(wav, text, locale)
            log.info("pronunciation ответ: ключи=%s", sorted(resp)[:12])
            return pronunciation_view(resp, text, locale)
        finally:
            client.close()

    return _run(work)


@router.post("/prosody")
def prosody(
    request: Request,
    audio_file: UploadFile = File(..., alias="audio"),
    video_id: str = Form(...),
    start: float = Form(...),
    end: float = Form(...),
):
    _guard_size(request)
    data = _read_upload(audio_file)

    def work():
        client = _client()
        try:
            vm.guard.check(vm.ANALYSIS_COST["prosody"])
            model_wav, model_dur = audio.original_fragment(video_id, start, end)  # может скачивать аудио — без семафора
            with _convert:
                user_wav, user_dur = audio.prepare_recording(data)
            resp = client.prosody_similarity(model_wav, user_wav)
            log.info("prosody ответ: ключи=%s", sorted(resp)[:12])
            view = prosody_view(resp)
            view["fragment"] = {"start": start, "end": end, "originalSeconds": round(model_dur, 2), "recordingSeconds": round(user_dur, 2)}
            return view
        finally:
            client.close()

    return _run(work)
