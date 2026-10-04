import math
import threading
import time

import pytest

import reference_engine as engine
import transcribe_local as adapter


def speech(words):
    return {"segments": [{"start": 0.0, "end": 10.0, "text": "spoken", "words": words}]}


def test_readable_word_cues_punctuation_overlap_and_empty():
    result = adapter.cues_from_words(
        speech(
            [
                {"start": 0.1, "end": 0.5, "word": "Hello"},
                {"start": 0.49, "end": 1, "word": " world."},
                {"start": 1.2, "end": 2, "word": " مرحبا"},
            ]
        ),
        10,
    )
    assert [(c.start, c.end, c.text) for c in result] == [
        (0.1, 1, "Hello world."),
        (1.2, 2, "مرحبا"),
    ]
    assert adapter.cues_from_words({"segments": []}, 10) == []
    words = [{"start": i, "end": i + 0.5, "word": " " + "word" * 8} for i in range(9)]
    split = adapter.cues_from_words(speech(words), 10)
    assert len(split) > 1 and all(len(c.text) <= 80 for c in split)


@pytest.mark.parametrize(
    "word",
    [
        {"start": math.nan, "end": 1, "word": "hello"},
        {"start": 1, "end": 1, "word": "hello"},
        {"start": 0, "end": 11, "word": "hello"},
        {"start": True, "end": 1, "word": "hello"},
        {"start": 0, "end": 1, "word": " "},
        {"start": 0, "end": 1, "word": "x" * 201},
    ],
)
def test_reject_invalid_words(word):
    with pytest.raises(engine.RetrievalFailure):
        adapter.cues_from_words(speech([word]), 10)


def test_limit_and_unusable_segments():
    with pytest.raises(engine.RetrievalFailure):
        adapter.cues_from_words(
            speech([{"start": 0, "end": 1, "word": "a"}, {"start": 0.9, "end": 2, "word": "b"}]), 10
        )
    with pytest.raises(engine.RetrievalFailure):
        adapter.cues_from_words(speech([]), 10)
    data = {
        "segments": [
            {
                "start": i / 2,
                "end": (i + 1) / 2,
                "text": "a.",
                "words": [{"start": i / 2, "end": (i + 1) / 2, "word": "a."}],
            }
            for i in range(201)
        ]
    }
    with pytest.raises(engine.RetrievalFailure, match="200 cues"):
        adapter.cues_from_words(data, 120)


def test_missing_model_never_starts_a_process(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter, "MODEL", tmp_path / "missing")
    monkeypatch.setattr(engine, "run_command", lambda *a, **k: pytest.fail("No automatic download"))
    with pytest.raises(engine.RetrievalFailure, match="setup"):
        adapter.run(tmp_path / "input", 1, "en", tmp_path, threading.Event(), time.monotonic() + 2)
