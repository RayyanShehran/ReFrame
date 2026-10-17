import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "temporal", Path(__file__).resolve().parents[2] / "tools" / "caption_temporal_eval.py"
)
temporal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(temporal)


def test_interval_errors_count_gaps_fragments_and_extra_words():
    expected = [dict(start=0, end=3, text="Two words")]
    actual = [
        dict(start=0, end=1, text="Two words"),
        dict(start=2, end=3, text="Two words"),
        dict(start=3, end=4, text="Extra"),
    ]
    score = temporal.compare(expected, actual)
    assert score["word_seconds"] == dict(correct=4, incorrect=0, missing=2, extra=1)
    assert score["fragmented_cues"] == 1
    assert score["intervals"][0]["gaps"] == [[1, 2]]
    assert score["extra_or_incorrect_cues"] == 1
    assert temporal.compare(expected, expected)["word_seconds"] == dict(
        correct=6, incorrect=0, missing=0, extra=0
    )


def test_interval_evaluation_rejects_nonfinite_and_reversed_boundaries():
    for start, end in [(0, float("nan")), (2, 1)]:
        with pytest.raises(ValueError):
            temporal.compare([dict(start=start, end=end, text="x")], [])
