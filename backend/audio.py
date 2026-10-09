"""Подготовка аудио для Vocametrix: запись пользователя (mp4/webm) и фрагмент оригинала -> WAV 16 кГц mono."""
import io
import os
import re
import threading
import time
import wave
from pathlib import Path

import av

TARGET_RATE = 16000
MIN_SEC = 1.0            # Vocametrix: для связной речи нужно 5–30 с, но короткие фразы тоже бывают
MAX_SEC = 30.0           # верхняя граница из документации Vocametrix
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", 10 * 1024 * 1024))

CACHE_DIR = Path(os.getenv("ORIGINAL_AUDIO_DIR", "/tmp/shadowing-audio"))
CACHE_MAX_BYTES = int(os.getenv("ORIGINAL_AUDIO_CACHE_MB", "500")) * 1024 * 1024
DOWNLOAD_MAX_BYTES = int(os.getenv("ORIGINAL_AUDIO_MAX_MB", "150")) * 1024 * 1024
VIDEO_ID_RE = re.compile(r"[\w-]{11}")


class AudioError(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _decode(source, start: float = 0.0, end: float | None = None) -> tuple[bytes, float]:
    """Декодирует аудио в PCM s16 mono 16 кГц. Если задан end — читает только окно [start, end]."""
    try:
        container = av.open(source)
    except Exception:
        raise AudioError("bad_audio", "Не удалось прочитать аудио: неподдерживаемый или повреждённый файл")
    try:
        stream = next((s for s in container.streams if s.type == "audio"), None)
        if stream is None:
            raise AudioError("bad_audio", "В файле нет аудиодорожки")
        if start > 0:
            container.seek(int(start * av.time_base), any_frame=False, backward=True)
        resampler = av.AudioResampler(format="s16", layout="mono", rate=TARGET_RATE)
        chunks: list[bytes] = []
        first_t = None
        total = 0

        def take(frames):
            nonlocal total
            for f in frames:
                chunks.append(bytes(f.planes[0])[: f.samples * 2])
                total += f.samples

        try:
            for frame in container.decode(stream):
                t = frame.time if frame.time is not None else 0.0
                if first_t is None:
                    first_t = t
                if end is not None and t > end + 0.2:
                    break
                take(resampler.resample(frame))
                if end is None and total / TARGET_RATE > MAX_SEC + 5:
                    break  # слишком длинная запись: дальше не читаем, длительность проверит вызывающий
            take(resampler.resample(None))
        except AudioError:
            raise
        except Exception:
            if not chunks:
                raise AudioError("bad_audio", "Не удалось декодировать аудио")
    finally:
        container.close()

    pcm = b"".join(chunks)
    offset = max(0.0, start - (first_t or 0.0)) if end is not None else 0.0
    a = int(offset * TARGET_RATE) * 2
    b = a + int((end - start) * TARGET_RATE) * 2 if end is not None else len(pcm)
    pcm = pcm[a:b]
    return pcm, len(pcm) / 2 / TARGET_RATE


def to_wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(TARGET_RATE)
        w.writeframes(pcm)
    return buf.getvalue()


def prepare_recording(data: bytes) -> tuple[bytes, float]:
    """Запись пользователя -> (wav, длительность). Проверяет размер и длительность."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise AudioError("too_large", f"Запись слишком большая (максимум {MAX_UPLOAD_BYTES // 1024 // 1024} МБ)", 413)
    if len(data) < 1000:
        raise AudioError("too_short", "Запись слишком короткая")
    pcm, dur = _decode(io.BytesIO(data))
    check_duration(dur, "Запись")
    return to_wav(pcm), dur


def check_duration(dur: float, what: str):
    if dur < MIN_SEC:
        raise AudioError("too_short", f"{what} слишком короткая ({dur:.1f} с, минимум {MIN_SEC:g} с)")
    if dur > MAX_SEC + 0.5:
        raise AudioError("too_long", f"{what} слишком длинная ({dur:.0f} с, максимум {MAX_SEC:g} с)", 413)


# ---------- оригинал ----------
# На странице оригинал играет YouTube-плеер, поэтому звук недоступен браузеру. Backend берёт его сам:
# 1) файл CACHE_DIR/<videoId>.<ext>, если он уже есть (скачан раньше или положен вручную);
# 2) иначе скачивает аудиодорожку через yt-dlp.
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _find_source(video_id: str) -> Path | None:
    if not CACHE_DIR.exists():
        return None
    for p in CACHE_DIR.glob(f"{video_id}.*"):
        if p.suffix not in (".part", ".ytdl", ".tmp") and p.is_file() and p.stat().st_size > 0:
            return p
    return None


def _evict(keep: Path):
    files = [p for p in CACHE_DIR.iterdir() if p.is_file() and p != keep]
    total = sum(p.stat().st_size for p in files) + keep.stat().st_size
    for p in sorted(files, key=lambda p: p.stat().st_mtime):
        if total <= CACHE_MAX_BYTES:
            break
        total -= p.stat().st_size
        p.unlink(missing_ok=True)


def _download(video_id: str) -> Path:
    try:
        import yt_dlp
    except ImportError:
        raise AudioError("original_unavailable", "yt-dlp не установлен на сервере", 502)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    opts = {
        "format": "bestaudio[ext=m4a]/bestaudio",
        "outtmpl": str(CACHE_DIR / "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 2,
        "socket_timeout": 20,
        "max_filesize": DOWNLOAD_MAX_BYTES,
    }
    if os.getenv("YTDLP_COOKIES"):
        opts["cookiefile"] = os.environ["YTDLP_COOKIES"]
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
    except Exception as e:
        raise AudioError("original_unavailable", f"Не удалось получить аудио оригинала ({type(e).__name__})", 502)
    p = _find_source(video_id)
    if not p:
        raise AudioError("original_unavailable", "Не удалось получить аудио оригинала (файл не скачан или превышен лимит размера)", 502)
    _evict(p)
    return p


def original_fragment(video_id: str, start: float, end: float) -> tuple[bytes, float]:
    """WAV-фрагмент оригинала [start, end] секунд."""
    if not VIDEO_ID_RE.fullmatch(video_id):
        raise AudioError("bad_request", "Некорректный идентификатор видео", 400)
    if not (0 <= start < end):
        raise AudioError("bad_request", "Некорректные границы фрагмента", 400)
    check_duration(end - start, "Фрагмент оригинала")
    with _locks_guard:
        lock = _locks.setdefault(video_id, threading.Lock())
    with lock:
        src = _find_source(video_id) or _download(video_id)
        os.utime(src, (time.time(), time.time()))
    pcm, dur = _decode(str(src), start, end)
    if dur < MIN_SEC:
        raise AudioError("bad_request", "Фрагмент оригинала вне длительности аудио", 400)
    return to_wav(pcm), dur
