"""Склейка и разбиение субтитров YouTube на отдельные предложения."""
import re

END_PUNCT = (".", "?", "!", "…", "。", "？", "！")
CLOSERS = "\"'”’)]»"
MAX_CHARS = 140   # для автосубтитров без пунктуации
MAX_SEC = 8.0
GAP_SEC = 1.0
ABBREV = {"mr", "mrs", "ms", "dr", "st", "vs", "prof", "jr", "sr", "mt", "etc", "no", "inc", "ltd", "u.s", "e.g", "i.e"}

# Конец предложения: . ? ! … (+ закрывающие кавычки/скобки) и пробел за ними
_BOUNDARY = re.compile(r"([.?!…。？！]+[" + re.escape(CLOSERS) + r"]*)\s+")
_DASH = re.compile(r"(?:^|\s)[-–—]\s+(?=\S)")   # «- реплика» у смены говорящего
_MARK = re.compile(r">>+")                          # «>>» в автосубтитрах


def split_sentences(text: str) -> list[str]:
    """Режет текст по концам предложений, не трогая сокращения (Mr. Smith) и числа (3.5)."""
    text = _MARK.sub(" ", text)
    if re.match(r"\s*[-–—]\s", text):             # реплики диалога: дефис в начале — значит, дальше тоже смена говорящего
        text = _DASH.sub(" ¦ ", " " + text)       # ¦ — граница реплик
    parts, last = [], 0
    for m in _BOUNDARY.finditer(text):
        word = re.split(r"\s+", text[last:m.start()].strip())[-1].lower().rstrip(".") if text[last:m.start()].strip() else ""
        if m.group(1).startswith(".") and (word in ABBREV or (len(word) == 1 and word.isalpha())):
            continue
        parts.append(text[last:m.end()])
        last = m.end()
    parts.append(text[last:])
    out = []
    for p in parts:
        for q in p.split("¦"):
            q = re.sub(r"\s+", " ", q).strip()
            if q:
                out.append(q)
    return out


def _pieces(snippets):
    """Каждая реплика субтитров -> куски по предложениям; время делится пропорционально длине текста."""
    for s in snippets:
        text = s["text"].replace("\n", " ").strip()
        if not text or text.startswith("[") and text.endswith("]"):  # [Music] и т.п.
            continue
        parts = split_sentences(text)
        total = sum(len(p) for p in parts) or 1
        t = s["start"]
        for i, p in enumerate(parts):
            d = s["duration"] * len(p) / total
            yield p, t, t + d, i == len(parts) - 1
            t += d


def _ends(text: str) -> bool:
    return text.rstrip(CLOSERS).endswith(END_PUNCT)


def merge_to_sentences(snippets):
    sentences, buf, start, end = [], [], None, None

    def flush():
        nonlocal buf, start, end
        if buf:
            text = re.sub(r"\s+", " ", " ".join(buf)).strip()
            if text:
                sentences.append({"start": round(start, 2), "end": round(end, 2), "text": text})
        buf, start, end = [], None, None

    for text, s_start, s_end, _last in _pieces(snippets):
        if buf and s_start - end > GAP_SEC:
            flush()
        if start is None:
            start = s_start
        buf.append(text)
        end = s_end
        joined = " ".join(buf)
        if _ends(joined) or len(joined) > MAX_CHARS or end - start > MAX_SEC:
            flush()
    flush()

    # конец предложения не должен залезать на начало следующего
    for a, b in zip(sentences, sentences[1:]):
        a["end"] = round(min(a["end"], b["start"]), 2)
    for i, s in enumerate(sentences):
        s["id"] = i
    return sentences
