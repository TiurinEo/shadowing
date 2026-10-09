import io
import math
import struct
import sys
from pathlib import Path

import av
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def make_audio(fmt: str, seconds: float, rate: int = 48000) -> bytes:
    """Синтетический «голос»: синус с амплитудной модуляцией, закодированный в нужный контейнер."""
    codec, ext = {"webm": ("libopus", "webm"), "mp4": ("aac", "mp4"), "wav": ("pcm_s16le", "wav")}[fmt]
    buf = io.BytesIO()
    out = av.open(buf, "w", format=ext)
    st = out.add_stream(codec, rate=rate)
    st.layout = "mono"
    n = int(seconds * rate)
    samples = [int(9000 * math.sin(2 * math.pi * 220 * i / rate) * (0.6 + 0.4 * math.sin(i / rate * 6))) for i in range(n)]
    step = 960
    for i in range(0, n, step):
        chunk = samples[i:i + step]
        chunk += [0] * (step - len(chunk))
        f = av.AudioFrame.from_ndarray(__import__("numpy").array([chunk], dtype="int16"), format="s16", layout="mono")
        f.sample_rate = rate
        f.pts = i
        for p in st.encode(f):
            out.mux(p)
    for p in st.encode(None):
        out.mux(p)
    out.close()
    return buf.getvalue()


@pytest.fixture(scope="session")
def webm3():
    return make_audio("webm", 3.0)


@pytest.fixture(scope="session")
def mp4_3():
    return make_audio("mp4", 3.0)
