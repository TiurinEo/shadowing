import json

import httpx
import pytest
from fastapi.testclient import TestClient

import audio
import vocametrix as vm
from analysis import prosody_view, pronunciation_view
from conftest import make_audio

TEXT = "Hello, my name is Alex."

AZURE = {
    "RecognitionStatus": "Success",
    "DisplayText": "Hello my name Alex.",
    "NBest": [{
        "Display": "Hello my name Alex.",
        "PronunciationAssessment": {"AccuracyScore": 71, "FluencyScore": 89, "CompletenessScore": 80, "ProsodyScore": 70, "PronScore": 66},
        "Words": [
            {"Word": "hello", "Offset": 5000000, "Duration": 4000000, "PronunciationAssessment": {"AccuracyScore": 95, "ErrorType": "None"},
             "Phonemes": [{"Phoneme": "h", "PronunciationAssessment": {"AccuracyScore": 100}}, {"Phoneme": "ə", "PronunciationAssessment": {"AccuracyScore": 40}}]},
            {"Word": "my", "PronunciationAssessment": {"AccuracyScore": 88, "ErrorType": "None"}},
            {"Word": "name", "PronunciationAssessment": {"AccuracyScore": 70, "ErrorType": "None"}},
            {"Word": "is", "PronunciationAssessment": {"AccuracyScore": 0, "ErrorType": "Omission"}},
            {"Word": "um", "PronunciationAssessment": {"AccuracyScore": 0, "ErrorType": "Insertion"}},
            {"Word": "alex", "PronunciationAssessment": {"AccuracyScore": 45, "ErrorType": "Mispronunciation"}},
        ],
    }],
}

PROSODY = {"OVERALL_SCORE": 71.2, "PITCH_SCORE": 64.5, "RHYTHM_SCORE": 80, "INTENSITY_SCORE": "55.0",
           "SPEECH_RATE_SIMILARITY": 0.92, "MODEL_SPEECH_RATE": "4.1", "USER_SPEECH_RATE": "3.8",
           "MODEL_DURATION": "3.0", "USER_DURATION": "3.4", "PERFORMANCE_LEVEL": "Good", "NEEDS_WORK": "intensity"}


# ---------- нормализация ----------
def test_pronunciation_azure_format():
    v = pronunciation_view(AZURE, TEXT, "en-US")
    assert v["scores"] == {"accuracy": 71, "fluency": 89, "completeness": 80, "prosody": 70, "pron": 66}
    assert [(w["t"], w["status"]) for w in v["words"]] == [
        ("Hello,", "good"), ("my", "good"), ("name", "fair"), ("is", "omission"), ("Alex.", "mispronunciation")]
    assert v["omitted"] == ["is"] and v["mispronounced"] == ["Alex."]
    assert v["insertions"] == [{"word": "um", "afterWord": 3}]
    assert v["words"][0]["phonemes"][1] == {"p": "ə", "s": 40}
    # среднее по произнесённым: (95+88+70+45)/4 = 74.5 -> 74 или 75
    assert v["spokenWordsAccuracy"] in (74, 75)
    assert v["unmatchedWords"] == 0 and v["hasPhonemes"]


def test_pronunciation_flat_format_and_unknown_words():
    flat = {"accuracyScore": 90, "fluencyScore": 80, "completenessScore": 100, "pronScore": 88,
            "words": [{"word": "hello", "accuracyScore": 90, "errorType": "None"}]}
    v = pronunciation_view(flat, "Hello there", "en-US")
    assert v["scores"]["accuracy"] == 90 and v["scores"]["prosody"] is None
    # «there» не найдено в ответе — не должно маркироваться как верное
    assert [w["status"] for w in v["words"]] == ["good", "unknown"] and v["unmatchedWords"] == 1


def test_pronunciation_no_speech_and_empty():
    with pytest.raises(vm.VocametrixError) as e:
        pronunciation_view({"RecognitionStatus": "NoMatch"}, TEXT, "en-US")
    assert e.value.code == "no_speech" and e.value.status == 422
    with pytest.raises(vm.VocametrixError) as e:
        pronunciation_view({}, TEXT, "en-US")
    assert e.value.code == "bad_response"


def test_prosody_view():
    v = prosody_view(PROSODY)
    assert v["similarity"] == {"pitch": 64.5, "rhythm": 80.0, "intensity": 55.0, "speechRate": 92.0}
    assert v["details"]["userDuration"] == 3.4 and v["level"] == "Good"
    with pytest.raises(vm.VocametrixError):
        prosody_view({"ANALYSIS_TYPE": "x"})


# ---------- аудио ----------
@pytest.mark.parametrize("fmt", ["webm", "mp4", "wav"])
def test_prepare_recording_formats(fmt):
    wav, dur = audio.prepare_recording(make_audio(fmt, 3.0))
    assert wav[:4] == b"RIFF" and abs(dur - 3.0) < 0.1


def test_audio_limits():
    with pytest.raises(audio.AudioError) as e:
        audio.prepare_recording(make_audio("webm", 0.4))
    assert e.value.code in ("too_short",)
    with pytest.raises(audio.AudioError) as e:
        audio.prepare_recording(make_audio("webm", 35))
    assert e.value.code == "too_long" and e.value.status == 413
    with pytest.raises(audio.AudioError) as e:
        audio.prepare_recording(b"not audio at all" * 200)
    assert e.value.code == "bad_audio"
    with pytest.raises(audio.AudioError) as e:
        audio.prepare_recording(b"x" * (audio.MAX_UPLOAD_BYTES + 1))
    assert e.value.code == "too_large"


@pytest.mark.parametrize("fmt", ["wav", "mp4"])
def test_original_fragment_from_cache(tmp_path, monkeypatch, fmt):
    monkeypatch.setattr(audio, "CACHE_DIR", tmp_path)
    (tmp_path / f"abcdefghijk.{'m4a' if fmt == 'mp4' else 'wav'}").write_bytes(make_audio(fmt, 20))
    wav, dur = audio.original_fragment("abcdefghijk", 5.0, 9.0)
    assert abs(dur - 4.0) < 0.1
    with pytest.raises(audio.AudioError) as e:
        audio.original_fragment("abcdefghijk", 9.0, 5.0)
    assert e.value.status == 400
    with pytest.raises(audio.AudioError) as e:
        audio.original_fragment("abcdefghijk", 100.0, 104.0)  # за пределами дорожки
    assert e.value.status == 400
    with pytest.raises(audio.AudioError) as e:
        audio.original_fragment("../etc/passwd", 0, 4)
    assert e.value.status == 400


# ---------- клиент и эндпоинты на фейковом Vocametrix ----------
class Fake:
    def __init__(self, pron=AZURE, prosody=PROSODY, fail=None):
        self.calls, self.pron, self.prosody, self.fail = [], pron, prosody, fail or {}

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        self.calls.append((req.method, path, dict(req.headers), req.url.params))
        if path in self.fail:
            return self.fail[path]()
        if path == "/api/get-blob-url":
            return httpx.Response(200, json={"uploadURL": "https://blob.test/up?sig=1", "blobURL": "https://blob.test/b?sig=1"})
        if req.method == "PUT":
            return httpx.Response(201)
        if path == "/api/assignFileId":
            return httpx.Response(200, json={"fileId": f"f{len([c for c in self.calls if c[1] == path])}"})
        if path == "/api/pronunciation-assessment":
            body = json.loads(req.content)
            assert body["referenceText"] and body["locale"] == "en-US" and body["blobURL"].startswith("https://blob")
            return httpx.Response(200, json=self.pron)
        if path == "/api/calculate-prosody-similarity":
            return httpx.Response(200, json=self.prosody)
        return httpx.Response(404)


@pytest.fixture
def env(tmp_path, monkeypatch):
    import main
    monkeypatch.setenv("VOCAMETRIX_API_KEY", "secret-key-123")
    monkeypatch.setattr(audio, "CACHE_DIR", tmp_path)
    (tmp_path / "abcdefghijk.wav").write_bytes(make_audio("wav", 12))
    monkeypatch.setattr(vm, "guard", vm.RateGuard(100, 900))
    monkeypatch.setattr(vm.time, "sleep", lambda s: None)
    fake = Fake()

    orig = vm.Vocametrix.__init__
    def init(self, key, base_url=vm.BASE_URL, transport=None):
        orig(self, key, base_url, fake_holder["t"])
    fake_holder = {"t": httpx.MockTransport(fake)}
    monkeypatch.setattr(vm.Vocametrix, "__init__", init)
    fake.holder = fake_holder
    return TestClient(main.app), fake


def post_pron(c, data, lang="en"):
    return c.post("/api/vocametrix/pronunciation", files={"audio": ("rec.webm", data, "audio/webm")}, data={"text": TEXT, "lang": lang})


def test_pronunciation_endpoint(env, webm3):
    c, fake = env
    r = post_pron(c, webm3)
    assert r.status_code == 200, r.text
    assert r.json()["omitted"] == ["is"]
    # ключ уходит только в Vocametrix и не попадает ни в Azure Blob, ни в ответ клиенту
    assert "secret-key-123" not in r.text
    put = [x for x in fake.calls if x[0] == "PUT"][0]
    assert "x-api-key" not in put[2] and put[2]["x-ms-blob-type"] == "BlockBlob"
    assert all(x[2].get("x-api-key") == "secret-key-123" for x in fake.calls if x[0] != "PUT")


def test_prosody_endpoint(env, webm3):
    c, fake = env
    r = c.post("/api/vocametrix/prosody", files={"audio": ("rec.webm", webm3, "audio/webm")},
               data={"video_id": "abcdefghijk", "start": "1.0", "end": "4.0"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["similarity"]["rhythm"] == 80.0 and j["fragment"]["originalSeconds"] == pytest.approx(3.0, abs=0.1)
    params = [x[3] for x in fake.calls if x[1].endswith("prosody-similarity")][0]
    assert params["svFileId"] == params["modelFileId"] and params["csFileId"] == params["userFileId"] and params["svFileId"] != params["csFileId"]


def test_status_and_not_configured(env, monkeypatch, webm3):
    c, _ = env
    assert c.get("/api/vocametrix/status").json()["enabled"] is True
    monkeypatch.delenv("VOCAMETRIX_API_KEY")
    assert c.get("/api/vocametrix/status").json()["enabled"] is False
    r = post_pron(c, webm3)
    assert r.status_code == 503 and r.json()["detail"]["code"] == "not_configured"


def test_validation_errors(env, webm3):
    c, _ = env
    assert post_pron(c, b"junk" * 500).json()["detail"]["code"] == "bad_audio"
    assert post_pron(c, webm3, lang="en;rm").status_code == 400
    r = c.post("/api/vocametrix/prosody", files={"audio": ("r.webm", webm3, "audio/webm")}, data={"video_id": "abcdefghijk", "start": "0", "end": "60"})
    assert r.status_code == 413 and r.json()["detail"]["code"] == "too_long"
    big = b"\0" * (audio.MAX_UPLOAD_BYTES + 10)
    assert post_pron(c, big).status_code == 413


def test_upstream_errors_and_retry(env, webm3, monkeypatch):
    c, fake = env
    state = {"n": 0}

    def flaky():
        state["n"] += 1
        return httpx.Response(503) if state["n"] < 3 else httpx.Response(200, json=AZURE)
    fake.holder["t"] = httpx.MockTransport(Fake(fail={"/api/pronunciation-assessment": flaky}))
    assert post_pron(c, webm3).status_code == 200 and state["n"] == 3  # два ретрая

    fake.holder["t"] = httpx.MockTransport(Fake(fail={"/api/pronunciation-assessment": lambda: httpx.Response(401, json={"error": "bad key"})}))
    r = post_pron(c, webm3)
    assert r.status_code == 503 and r.json()["detail"]["code"] == "auth" and not r.json()["detail"]["retryable"]

    fake.holder["t"] = httpx.MockTransport(Fake(fail={"/api/pronunciation-assessment": lambda: httpx.Response(429, headers={"Retry-After": "120"})}))
    r = post_pron(c, webm3)
    assert r.status_code == 429 and r.json()["detail"]["retryAfter"] == 120

    fake.holder["t"] = httpx.MockTransport(Fake(fail={"/api/pronunciation-assessment": lambda: httpx.Response(400, json={"details": "audio too short"})}))
    r = post_pron(c, webm3)
    assert r.status_code == 422 and "audio too short" in r.json()["detail"]["message"]

    fake.holder["t"] = httpx.MockTransport(Fake(pron={"RecognitionStatus": "NoMatch"}))
    assert post_pron(c, webm3).json()["detail"]["code"] == "no_speech"

    fake.holder["t"] = httpx.MockTransport(Fake(fail={"/api/pronunciation-assessment": lambda: httpx.Response(200, text="<html>")}))
    assert post_pron(c, webm3).json()["detail"]["code"] == "bad_response"


def test_local_rate_guard(env, webm3, monkeypatch):
    c, _ = env
    monkeypatch.setattr(vm, "guard", vm.RateGuard(4, 900))
    assert post_pron(c, webm3).status_code == 200      # 2 запроса
    assert post_pron(c, webm3).status_code == 200      # ещё 2
    r = post_pron(c, webm3)
    assert r.status_code == 429 and r.json()["detail"]["code"] == "rate_limited" and r.json()["detail"]["retryAfter"] > 0
