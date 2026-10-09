from subtitles import merge_to_sentences, split_sentences


def sn(text, start, dur):
    return {"text": text, "start": start, "duration": dur}


def test_reported_case():
    s = merge_to_sentences([
        sn("- Plenty of courage I see.\\nNot a bad mind either. There's talent. Oh yes. And a".replace("\\n", "\n"), 10.0, 5.0),
        sn("- thirst to prove yourself.", 15.0, 2.0),
    ])
    assert [x["text"] for x in s] == [
        "Plenty of courage I see.", "Not a bad mind either.", "There's talent.", "Oh yes.", "And a thirst to prove yourself."]
    assert all(a["end"] <= b["start"] for a, b in zip(s, s[1:])) and s[0]["start"] == 10.0 and s[-1]["end"] == 17.0


def test_dialog_dash_without_punct_and_abbrev():
    assert split_sentences("- Hi there - Hello you") == ["Hi there", "Hello you"]
    assert split_sentences("I think - no, I know.") == ["I think - no, I know."]
    assert split_sentences("Mr. Smith paid 3.5 dollars. Fine?") == ["Mr. Smith paid 3.5 dollars.", "Fine?"]
    assert split_sentences('He said "Go." Then left.') == ['He said "Go."', "Then left."]
    assert split_sentences(">> Welcome back. >> Thanks") == ["Welcome back.", "Thanks"]


def test_sentence_spanning_cues_and_gap():
    s = merge_to_sentences([sn("This is a long", 0, 1), sn("sentence indeed.", 1, 1), sn("Next", 5, 1)])
    assert [x["text"] for x in s] == ["This is a long sentence indeed.", "Next"]
