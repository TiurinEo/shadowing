"""Приведение ответов Vocametrix к виду, удобному для интерфейса.

Документация Vocametrix описывает ответ pronunciation-assessment в формате Azure
(NBest[0].PronunciationAssessment / Words[] / Phonemes[]), а OpenAPI-схема — «плоскими» полями
accuracyScore, fluencyScore… Поэтому разбор терпим к обоим вариантам и к регистру ключей.
Если поле не найдено, возвращается None, а не выдуманное значение.
"""
import difflib
import re

from vocametrix import VocametrixError

TICKS = 1e7  # Offset/Duration в ответе — единицы по 100 нс


def _get(d, *names, default=None):
    if not isinstance(d, dict):
        return default
    low = {k.lower(): v for k, v in d.items()}
    for n in names:
        if n.lower() in low and low[n.lower()] is not None:
            return low[n.lower()]
    return default


def num(v):
    """Число из числа или строки вида '12.3', '85%', '4.2 syl/s'."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        m = re.search(r"-?\d+(?:[.,]\d+)?", v)
        if m:
            return float(m.group(0).replace(",", "."))
    return None


def norm(tok: str) -> str:
    return re.sub(r"[^\w']", "", tok.lower().replace("’", "'")).strip("'")


def _band(score):
    # Пороги из рекомендаций Azure для окраски: <60 — плохо, 60–79 — средне. Это ориентир, а не вердикт.
    if score is None:
        return "unknown"
    return "good" if score >= 80 else "fair" if score >= 60 else "poor"


def _phonemes(w):
    out = []
    for p in _get(w, "Phonemes", default=[]) or []:
        pa = _get(p, "PronunciationAssessment", default={})
        out.append({"p": _get(p, "Phoneme", default="?"), "s": num(_get(pa, "AccuracyScore", default=_get(p, "accuracyScore")))})
    return out


def _words(best: dict, flat: dict):
    raw = _get(best, "Words")
    if raw is None:
        raw = _get(flat, "words", default=[])
    words = []
    for w in raw or []:
        pa = _get(w, "PronunciationAssessment", default={})
        words.append({
            "word": str(_get(w, "Word", default="")),
            "score": num(_get(pa, "AccuracyScore", default=_get(w, "accuracyScore"))),
            "errorType": str(_get(pa, "ErrorType", default=_get(w, "errorType", default="None"))),
            "phonemes": _phonemes(w),
            "offset": (num(_get(w, "Offset")) or 0) / TICKS if _get(w, "Offset") is not None else None,
            "duration": (num(_get(w, "Duration")) or 0) / TICKS if _get(w, "Duration") is not None else None,
        })
    return words


def pronunciation_view(resp: dict, text: str, locale: str) -> dict:
    status = _get(resp, "RecognitionStatus")
    if _get(resp, "error") and not _get(resp, "NBest"):
        raise VocametrixError("rejected", f"Vocametrix: {str(_get(resp, 'error'))[:200]}", 422, retryable=False)
    if status and status != "Success":
        msg = {
            "NoMatch": "Речь не распознана. Запишите фразу ещё раз, говорите чётче и ближе к микрофону.",
            "InitialSilenceTimeout": "В начале записи только тишина. Начните говорить сразу после нажатия Record.",
        }.get(status, f"Речь не распознана (статус {status}).")
        raise VocametrixError("no_speech", msg, 422, retryable=True)

    nbest = _get(resp, "NBest")
    best = nbest[0] if isinstance(nbest, list) and nbest else {}
    scores_src = _get(best, "PronunciationAssessment") or resp
    scores = {
        "accuracy": num(_get(scores_src, "AccuracyScore")),
        "fluency": num(_get(scores_src, "FluencyScore")),
        "completeness": num(_get(scores_src, "CompletenessScore")),
        "prosody": num(_get(scores_src, "ProsodyScore")),
        "pron": num(_get(scores_src, "PronScore")),
    }
    aw = _words(best, resp)
    if all(v is None for v in scores.values()) and not aw:
        raise VocametrixError("bad_response", "Vocametrix вернул ответ без оценок. Попробуйте ещё раз.", 502)

    inserted = [(i, w) for i, w in enumerate(aw) if w["errorType"] == "Insertion"]
    ref = [w for w in aw if w["errorType"] != "Insertion"]

    # Слова ответа -> слова оригинального текста (как их видит пользователь, с пунктуацией)
    toks = text.split()
    dn = [norm(t) for t in toks]
    an = [norm(w["word"]) for w in ref]
    mapping: dict[int, dict] = {}  # индекс слова текста -> слово ответа
    for op, a1, a2, b1, b2 in difflib.SequenceMatcher(None, dn, an, autojunk=False).get_opcodes():
        if op == "equal" or (op == "replace" and a2 - a1 == b2 - b1):
            for k in range(a2 - a1):
                mapping[a1 + k] = ref[b1 + k]
    out_words, unmatched = [], 0
    for i, t in enumerate(toks):
        w = mapping.get(i)
        if not dn[i]:
            out_words.append({"t": t, "status": "skip"})
        elif w is None:
            unmatched += 1
            out_words.append({"t": t, "status": "unknown"})
        else:
            et = w["errorType"]
            status = "omission" if et == "Omission" else "mispronunciation" if et == "Mispronunciation" else _band(w["score"])
            out_words.append({"t": t, "status": status, "score": w["score"], "errorType": et, "phonemes": w["phonemes"]})

    # Вставки (лишние слова) привязываем к предыдущему слову текста
    tok_of = {id(w): i for i, w in mapping.items()}
    insertions = []
    for idx, w in inserted:
        prev = next((x for x in reversed(aw[:idx]) if x["errorType"] != "Insertion"), None)
        insertions.append({"word": w["word"], "afterWord": tok_of.get(id(prev), -1) if prev is not None else -1})

    omitted = [w["t"] for w in out_words if w["status"] == "omission"]
    spoken = [w["score"] for w in out_words if w.get("score") is not None and w["status"] != "omission"]
    return {
        "locale": locale,
        "scores": scores,
        "words": out_words,
        "insertions": insertions,
        "omitted": omitted,
        "mispronounced": [w["t"] for w in out_words if w["status"] == "mispronunciation"],
        # Считает сайт, не Vocametrix: средняя оценка слов, которые пользователь действительно произнёс
        "spokenWordsAccuracy": round(sum(spoken) / len(spoken)) if omitted and spoken else None,
        "hasPhonemes": any(w.get("phonemes") for w in out_words),
        "unmatchedWords": unmatched,
        "recognized": _get(best, "Display") or _get(resp, "DisplayText"),
    }


def _pct(v):
    return None if v is None else round(max(0.0, min(100.0, v)), 1)


def prosody_view(resp: dict) -> dict:
    pitch = num(_get(resp, "PITCH_SCORE"))
    rhythm = num(_get(resp, "RHYTHM_SCORE"))
    intensity = num(_get(resp, "INTENSITY_SCORE"))
    rate = num(_get(resp, "SPEECH_RATE_SIMILARITY"))
    # В схеме Vocametrix шкала темпа названа «0–1 или 0–100 в зависимости от метрики»: значения ≤ 1 считаем долей
    if rate is not None and rate <= 1.0:
        rate *= 100
    if all(v is None for v in (pitch, rhythm, intensity, rate)):
        raise VocametrixError("bad_response", "Vocametrix вернул ответ без оценок просодии. Попробуйте ещё раз.", 502)
    return {
        "similarity": {"pitch": _pct(pitch), "rhythm": _pct(rhythm), "intensity": _pct(intensity), "speechRate": _pct(rate)},
        "details": {
            "modelRate": num(_get(resp, "MODEL_SPEECH_RATE")), "userRate": num(_get(resp, "USER_SPEECH_RATE")),
            "modelDuration": num(_get(resp, "MODEL_DURATION")), "userDuration": num(_get(resp, "USER_DURATION")),
            "modelF0": num(_get(resp, "MODEL_F0_MEAN")), "userF0": num(_get(resp, "USER_F0_MEAN")),
        },
        "vocametrixOverall": _pct(num(_get(resp, "OVERALL_SCORE"))),
        "level": _get(resp, "PERFORMANCE_LEVEL"),
        "bestMatch": _get(resp, "BEST_MATCH"),
        "needsWork": _get(resp, "NEEDS_WORK"),
        "algorithm": _get(resp, "ALGORITHM_VERSION"),
    }
