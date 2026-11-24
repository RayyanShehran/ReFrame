"""Half-open word-second scorer for the 11 checked phases (CAPTION_BASELINE_PROVENANCE.md).

Usage: python score_captions.py cues.json [label]
cues.json: list of {"start", "end", "text"} in output/reference seconds.
"""

import json
import re
import sys
from collections import Counter

PHASES = [
    (1.600, 5.100, "You know I want you to know\nyou can talk to me about anything"),
    (5.100, 7.000, "You know I want you to know\nyou can talk to me about anything\nAs you get older you know"),
    (9.250, 10.450, "Whatever parties you go to"),
    (16.000, 17.500, "Drugs you take"),
    (17.500, 19.000, "No I'm serious Soph"),
    (20.970, 21.970, "I've done it all and you can too"),
    (21.970, 24.530, "I've done it all and you can too\nI just want you to promise me that\nyou'll talk to me about it okay"),
    (26.500, 27.000, "Okay But I'm never gonna"),
    (27.000, 29.000, "Okay But I'm never gonna\ndo any of that anyway"),
    (29.000, 30.000, "Okay But I'm never gonna\ndo any of that anyway\nWell that's okay too"),
    (32.000, 39.533, "But if you do remember okay"),
]
MANUAL = (14.0, 15.533333)  # excluded, as is the separate title (outside all phases)


def words(text):
    text = text.lower().replace("’", "'").replace("‘", "'")
    return Counter(w.strip("'") for w in re.sub(r"[^\w']+", " ", text).split() if w.strip("'"))


def shown(cues, t):
    out = Counter()
    for c in cues:
        if c["start"] <= t < c["end"]:  # half-open
            out += words(c["text"])
    return out


def score(cues):
    rows, totals = [], [0.0] * 4
    for start, end, text in PHASES:
        expected = words(text)
        cuts = sorted({start, end} | {x for c in cues for x in (c["start"], c["end"]) if start < x < end})
        cuts = [x for x in cuts if not (MANUAL[0] <= x < MANUAL[1])]
        r = [0.0] * 4
        for a, b in zip(cuts, cuts[1:]):
            mid, dt = (a + b) / 2, b - a
            if MANUAL[0] <= mid < MANUAL[1]:
                continue
            d = shown(cues, mid)
            correct = sum((expected & d).values())
            miss, extra = sum((expected - d).values()), sum((d - expected).values())
            wrong = min(miss, extra)
            for i, v in enumerate((correct, wrong, miss - wrong, extra - wrong)):
                r[i] += v * dt
        rows.append((start, end, text.split("\n")[-1], r, timing(cues, start, end, expected)))
        totals = [x + y for x, y in zip(totals, r)]
    return rows, totals


def timing(cues, start, end, expected, step=1 / 300):
    """Start/end error of the displayed run that best overlaps the phase.

    Prefer an exact match (same normalized words), then a complete superset, then any overlap.
    """
    n = int(round((end - start + 2) / step))
    grid = [start - 1 + i * step for i in range(n)]
    for kind, ok in (
        ("exact", lambda d: d == expected),
        ("complete", lambda d: not (expected - d)),
        ("partial", lambda d: bool(expected & d)),
    ):
        flags = [ok(shown(cues, t)) for t in grid]
        runs, s = [], None
        for i, f in enumerate(flags + [False]):
            if f and s is None:
                s = i
            elif not f and s is not None:
                runs.append((grid[s], grid[i - 1] + step))
                s = None
        best = max(runs, key=lambda r: min(r[1], end) - max(r[0], start), default=None)
        if best and min(best[1], end) - max(best[0], start) > 0:
            return (kind, best[0] - start, best[1] - end)
    return None


if __name__ == "__main__":
    cues = json.load(open(sys.argv[1], encoding="utf-8"))
    label = sys.argv[2] if len(sys.argv) > 2 else sys.argv[1]
    rows, totals = score(cues)
    print(f"== {label}")
    print(f"{'phase':<14} {'last line':<36} {'C':>7} {'W':>6} {'M':>7} {'E':>6}  timing (start err / end err)")
    within = total_b = 0
    for start, end, last, r, t in rows:
        if t:
            kind, ds, de = t
            within += (abs(ds) <= 0.2) + (abs(de) <= 0.2)
            total_b += 2
            tt = f"{kind}: {ds:+.3f} / {de:+.3f}"
        else:
            total_b += 2
            tt = "MISSING"
        print(f"{start:6.3f}-{end:6.3f} {last[:36]:<36} {r[0]:7.3f} {r[1]:6.3f} {r[2]:7.3f} {r[3]:6.3f}  {tt}")
    print(f"TOTAL correct/wrong/missing/extra = {totals[0]:.4f} / {totals[1]:.4f} / {totals[2]:.4f} / {totals[3]:.4f}")
    print(f"boundaries within 0.2 s: {within}/{total_b}")
