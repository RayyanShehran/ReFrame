"""Offline interval scoring; evaluation answers never enter the caption reader."""

import argparse
import itertools
import json
import math
import re
from difflib import SequenceMatcher
from pathlib import Path


def words(text):
    return re.findall(r"[^\W_]+(?:['’][^\W_]+)*", text.casefold().replace("’", "'"))


def validate(cues):
    for cue in cues:
        if (
            not isinstance(cue.get("text"), str)
            or not all(
                type(cue.get(k)) in (int, float) and math.isfinite(cue[k])
                for k in ("start", "end")
            )
            or not 0 <= cue["start"] < cue["end"]
        ):
            raise ValueError("Expected finite ordered cue intervals and plain text")


def compare(expected, actual):
    """Integrate word errors over half-open intervals; gaps and fragments count."""
    validate(expected)
    validate(actual)
    times = sorted({c[k] for c in expected + actual for k in ("start", "end")})
    totals = {"correct": 0.0, "incorrect": 0.0, "missing": 0.0, "extra": 0.0}
    for left, right in itertools.pairwise(times):
        e = [
            w
            for c in expected
            if c["start"] <= left < c["end"]
            for w in words(c["text"])
        ]
        a = [
            w for c in actual if c["start"] <= left < c["end"] for w in words(c["text"])
        ]
        for op, i, j, k, stop in SequenceMatcher(
            None, e, a, autojunk=False
        ).get_opcodes():
            counts = {"correct": 0, "incorrect": 0, "missing": 0, "extra": 0}
            if op == "equal":
                counts["correct"] = j - i
            elif op == "delete":
                counts["missing"] = j - i
            elif op == "insert":
                counts["extra"] = stop - k
            else:
                counts["incorrect"] = min(j - i, stop - k)
                counts["missing"] = max(0, j - i - (stop - k))
                counts["extra"] = max(0, stop - k - (j - i))
            for key, count in counts.items():
                totals[key] += count * (right - left)
    intervals = []
    for cue in expected:
        matches = sorted(
            (c["start"], c["end"])
            for c in actual
            if words(c["text"]) == words(cue["text"])
            and c["start"] < cue["end"]
            and c["end"] > cue["start"]
        )
        matching = [
            (max(cue["start"], start), min(cue["end"], end)) for start, end in matches
        ]
        cursor, gaps = cue["start"], []
        for start, end in matching:
            if start > cursor:
                gaps.append([cursor, start])
            cursor = max(cursor, end)
        if cursor < cue["end"]:
            gaps.append([cursor, cue["end"]])
        intervals.append(
            {
                "start": cue["start"],
                "end": cue["end"],
                "text": cue["text"],
                "fragments": len(matching),
                "gaps": gaps,
                "start_error": matches[0][0] - cue["start"] if matching else None,
                "end_error": matches[-1][1] - cue["end"] if matching else None,
            }
        )

    def exact(c):
        return any(
            words(c["text"]) == words(e["text"])
            and c["start"] < e["end"]
            and c["end"] > e["start"]
            for e in expected
        )

    return {
        "word_seconds": {k: round(v, 4) for k, v in totals.items()},
        "missing_cues": sum(not r["fragments"] for r in intervals),
        "fragmented_cues": sum(r["fragments"] > 1 for r in intervals),
        "extra_or_incorrect_cues": sum(not exact(c) for c in actual),
        "overlapping_actual_pairs": sum(
            a["start"] < b["end"] and b["start"] < a["end"]
            for i, a in enumerate(actual)
            for b in actual[i + 1 :]
        ),
        "intervals": intervals,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("expected", type=Path)
    parser.add_argument("actual", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            compare(
                json.loads(args.expected.read_text(encoding="utf8")),
                json.loads(args.actual.read_text(encoding="utf8")),
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
