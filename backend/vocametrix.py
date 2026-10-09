"""Клиент Vocametrix API. Ключ берётся из окружения и никогда не уходит во frontend."""
import logging
import os
import threading
import time
from collections import deque

import httpx

log = logging.getLogger("vocametrix")

BASE_URL = os.getenv("VOCAMETRIX_BASE_URL", "https://platform.vocametrix.com").rstrip("/")
# Раньше SDK отправлял этот email в assignFileId, поле в OpenAPI всё ещё помечено обязательным
ACCOUNT_EMAIL = os.getenv("VOCAMETRIX_ACCOUNT_EMAIL", "info@vocametrix.com")
MAX_RETRIES = 2
MAX_WAIT = 15.0  # дольше этого Retry-After пользователь ждать не будет — отдаём ошибку с подсказкой


class VocametrixError(Exception):
    def __init__(self, code: str, message: str, status: int = 502, retry_after: int | None = None, retryable: bool = True):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status
        self.retry_after, self.retryable = retry_after, retryable


class RateGuard:
    """Локальный счётчик: у ключа лимит 100 запросов / 15 минут, не доводим до 429 заранее."""

    def __init__(self, limit: int, window: float):
        self.limit, self.window = limit, window
        self.calls: deque[float] = deque()
        self.lock = threading.Lock()

    def _trim(self, now: float):
        while self.calls and now - self.calls[0] > self.window:
            self.calls.popleft()

    def remaining(self) -> int:
        with self.lock:
            self._trim(time.monotonic())
            return max(0, self.limit - len(self.calls))

    def check(self, need: int):
        with self.lock:
            now = time.monotonic()
            self._trim(now)
            if len(self.calls) + need > self.limit:
                wait = int(self.window - (now - self.calls[0])) + 1 if self.calls else int(self.window)
                raise VocametrixError("rate_limited", "Исчерпан лимит запросов к Vocametrix. Повторите чуть позже.", 429, wait)

    def record(self):
        with self.lock:
            self.calls.append(time.monotonic())


guard = RateGuard(int(os.getenv("VOCAMETRIX_RATE_LIMIT", "100")), float(os.getenv("VOCAMETRIX_RATE_WINDOW", "900")))
ANALYSIS_COST = {"pronunciation": 2, "prosody": 3}  # число запросов к API на один анализ


def api_key() -> str | None:
    return os.getenv("VOCAMETRIX_API_KEY") or None


def enabled() -> bool:
    return api_key() is not None


def _upstream_text(resp: httpx.Response) -> str:
    try:
        j = resp.json()
        return str(j.get("details") or j.get("error") or j.get("message") or "")[:200]
    except Exception:
        return resp.text[:200]


def _raise_for(resp: httpx.Response):
    s = resp.status_code
    if s in (401, 403):
        # Подробности скрываем: это ошибка конфигурации, а не пользователя
        log.error("Vocametrix отклонил ключ: %s %s", s, _upstream_text(resp))
        raise VocametrixError("auth", "Сервис анализа не настроен: ключ Vocametrix отклонён", 503, retryable=False)
    if s == 429:
        ra = resp.headers.get("Retry-After")
        raise VocametrixError("rate_limited", "Vocametrix: слишком много запросов. Повторите чуть позже.", 429,
                              int(ra) if ra and ra.isdigit() else 60)
    if s in (400, 404, 413, 415, 422):
        raise VocametrixError("rejected", f"Vocametrix не принял запрос: {_upstream_text(resp) or s}", 422, retryable=False)
    raise VocametrixError("upstream", f"Сервис Vocametrix временно недоступен ({s})", 502)


def _request(client: httpx.Client, method: str, url: str, **kw) -> httpx.Response:
    for attempt in range(MAX_RETRIES + 1):
        guard.record()
        try:
            resp = client.request(method, url, **kw)
        except httpx.TimeoutException:
            if attempt == MAX_RETRIES:
                raise VocametrixError("timeout", "Vocametrix не ответил вовремя", 504)
            time.sleep(1.5 * (attempt + 1))
            continue
        except httpx.TransportError:
            if attempt == MAX_RETRIES:
                raise VocametrixError("upstream", "Нет соединения с Vocametrix", 502)
            time.sleep(1.5 * (attempt + 1))
            continue
        if resp.is_success:
            return resp
        retry_after = None
        if resp.status_code == 429:
            ra = resp.headers.get("Retry-After")
            retry_after = int(ra) if ra and ra.isdigit() else None
        transient = resp.status_code in (429, 500, 502, 503, 504)
        wait = retry_after if retry_after is not None else 2.0 * (attempt + 1)
        if not transient or attempt == MAX_RETRIES or wait > MAX_WAIT:
            _raise_for(resp)
        time.sleep(wait)
    raise VocametrixError("upstream", "Vocametrix недоступен", 502)  # недостижимо


def _json(resp: httpx.Response) -> dict:
    try:
        j = resp.json()
    except Exception:
        raise VocametrixError("bad_response", "Vocametrix вернул ответ не в формате JSON", 502)
    if not isinstance(j, dict):
        raise VocametrixError("bad_response", "Неожиданный формат ответа Vocametrix", 502)
    return j


class Vocametrix:
    def __init__(self, key: str, base_url: str = BASE_URL, transport: httpx.BaseTransport | None = None):
        self.base = base_url
        self._transport = transport
        self.c = httpx.Client(
            headers={"X-API-Key": key},
            timeout=httpx.Timeout(60.0, connect=10.0),
            transport=transport,
        )

    def close(self):
        self.c.close()

    def _upload_blob(self, wav: bytes) -> str:
        """get-blob-url -> PUT в Azure Blob (без ключа Vocametrix) -> blobURL."""
        j = _json(_request(self.c, "POST", f"{self.base}/api/get-blob-url"))
        upload, blob = j.get("uploadURL"), j.get("blobURL")
        if not upload or not blob:
            raise VocametrixError("bad_response", "Vocametrix не выдал адрес для загрузки аудио", 502)
        with httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0), transport=self._transport) as bare:
            for attempt in range(MAX_RETRIES + 1):
                try:
                    r = bare.put(upload, content=wav, headers={"x-ms-blob-type": "BlockBlob", "Content-Type": "audio/wav"})
                except httpx.TransportError:
                    if attempt == MAX_RETRIES:
                        raise VocametrixError("upstream", "Не удалось загрузить аудио в хранилище Vocametrix", 502)
                    time.sleep(1.5 * (attempt + 1))
                    continue
                if r.is_success:
                    return blob
                if (r.status_code != 429 and r.status_code < 500) or attempt == MAX_RETRIES:
                    raise VocametrixError("upstream", f"Хранилище Vocametrix отклонило загрузку ({r.status_code})", 502)
                time.sleep(1.5 * (attempt + 1))
        raise VocametrixError("upstream", "Не удалось загрузить аудио", 502)

    def _upload_file_id(self, wav: bytes) -> str:
        r = _request(self.c, "POST", f"{self.base}/api/assignFileId",
                     files={"audio": ("audio.wav", wav, "audio/wav")}, data={"email": ACCOUNT_EMAIL})
        fid = _json(r).get("fileId")
        if not fid:
            raise VocametrixError("bad_response", "Vocametrix не вернул fileId", 502)
        return str(fid)

    def assess_pronunciation(self, wav: bytes, text: str, locale: str) -> dict:
        guard.check(ANALYSIS_COST["pronunciation"])
        blob = self._upload_blob(wav)
        r = _request(self.c, "POST", f"{self.base}/api/pronunciation-assessment",
                     json={"blobURL": blob, "referenceText": text, "locale": locale})
        return _json(r)

    def prosody_similarity(self, model_wav: bytes, user_wav: bytes) -> dict:
        guard.check(ANALYSIS_COST["prosody"])
        model_id = self._upload_file_id(model_wav)
        user_id = self._upload_file_id(user_wav)
        # SDK и MCP-сервер Vocametrix передают svFileId/csFileId, а в OpenAPI указаны modelFileId/userFileId.
        # Живой ответ без ключа проверить нельзя, поэтому отправляем оба варианта — лишние параметры сервер игнорирует.
        params = {"svFileId": model_id, "csFileId": user_id, "modelFileId": model_id, "userFileId": user_id}
        return _json(_request(self.c, "GET", f"{self.base}/api/calculate-prosody-similarity", params=params))
