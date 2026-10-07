import difflib
import re


def norm(tok: str) -> str:
    return re.sub(r"[^\w']", "", tok.lower().replace("’", "'")).strip("'")


def compare(expected: str, heard: str):
    """Сравнивает текст предложения с тем, что услышал Whisper.
    Возвращает слова оригинала со статусом ok / close / miss и общий балл."""
    toks = expected.split()
    e = [norm(t) for t in toks]
    h = [x for x in (norm(t) for t in heard.split()) if x]
    status = ["ok" if not x else "miss" for x in e]
    idx = [i for i, x in enumerate(e) if x]
    ew = [e[i] for i in idx]

    for op, a1, a2, b1, b2 in difflib.SequenceMatcher(None, ew, h, autojunk=False).get_opcodes():
        if op == "equal":
            for k in range(a1, a2):
                status[idx[k]] = "ok"
        elif op == "replace":
            for k in range(min(a2 - a1, b2 - b1)):
                if difflib.SequenceMatcher(None, ew[a1 + k], h[b1 + k]).ratio() >= 0.8:
                    status[idx[a1 + k]] = "close"

    n = len(ew)
    ok = sum(status[i] == "ok" for i in idx)
    close = sum(status[i] == "close" for i in idx)
    score = 100 if n == 0 else round(100 * (ok + 0.5 * close) / n)
    return {"score": score, "words": [{"t": t, "s": s} for t, s in zip(toks, status)]}
