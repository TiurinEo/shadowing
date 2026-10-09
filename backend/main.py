import io
import os
import re
import threading
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from youtube_transcript_api import YouTubeTranscriptApi

from diffcheck import compare
from subtitles import merge_to_sentences
from vocametrix_routes import router as vocametrix_router

app = FastAPI()
app.include_router(vocametrix_router)

ID_RE = re.compile(r"(?:v=|youtu\.be/|embed/|shorts/)([\w-]{11})")


def extract_id(url: str) -> str:
    if re.fullmatch(r"[\w-]{11}", url.strip()):
        return url.strip()
    m = ID_RE.search(url)
    if not m:
        raise HTTPException(400, "Не похоже на ссылку YouTube")
    return m.group(1)


def fetch_title(video_id: str) -> str:
    try:
        import json
        import urllib.request

        u = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
        with urllib.request.urlopen(u, timeout=5) as r:
            return json.load(r).get("title", "")
    except Exception:
        return ""


@lru_cache(maxsize=256)
def load(video_id: str, lang: str):
    api = YouTubeTranscriptApi()
    tl = api.list(video_id)
    try:
        t = tl.find_manually_created_transcript([lang])
    except Exception:
        t = tl.find_generated_transcript([lang])
    fetched = t.fetch()
    snippets = [{"text": x.text, "start": x.start, "duration": x.duration} for x in fetched]
    return {
        "videoId": video_id,
        "title": fetch_title(video_id),
        "generated": t.is_generated,
        "sentences": merge_to_sentences(snippets),
    }


@app.get("/api/transcript")
def transcript(url: str, lang: str = "en"):
    vid = extract_id(url)
    try:
        return load(vid, lang)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(404, f"Не удалось получить субтитры ({lang}): {type(e).__name__}")


# На слабом сервере считаем по одной записи за раз, остальные ждут в очереди
_whisper_lock = threading.Lock()


@lru_cache(maxsize=1)
def get_model():
    from faster_whisper import WhisperModel  # модель скачается при первом запуске
    return WhisperModel(os.getenv("WHISPER_MODEL", "base"), device="cpu", compute_type="int8")


@app.post("/api/check")
def check_pronunciation(audio: UploadFile = File(...), text: str = Form(...), lang: str = Form("en")):
    data = audio.file.read()
    if len(data) < 1000:
        raise HTTPException(400, "Запись слишком короткая")
    try:
        with _whisper_lock:
            segs, _ = get_model().transcribe(
                io.BytesIO(data), language=lang or None, beam_size=1, condition_on_previous_text=False
            )
            heard = " ".join(x.text.strip() for x in segs).strip()
    except Exception as e:
        raise HTTPException(500, f"Whisper: {type(e).__name__}: {str(e)[:200]}")
    return {"heard": heard, **compare(text, heard)}


# Раздаём собранный фронтенд, если он есть
DIST = Path(__file__).parent.parent / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        f = DIST / path
        return FileResponse(f if f.is_file() else DIST / "index.html")
