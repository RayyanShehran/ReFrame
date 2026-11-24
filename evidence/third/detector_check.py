"""cut_frames of a given backend checkout on the three references vs truth.

Usage: python detector_check.py <backend dir> [label]   -> appends to detector-check.txt
"""
import json, sys, time
from pathlib import Path

sys.path.insert(0, sys.argv[1])
import numpy as np  # noqa: E402
import camera_motion as m  # noqa: E402

HERE = Path(__file__).parent
EV = HERE.parent


def score(found, truth):
    """One-to-one within 2 frames; flash windows and uncertain frames as in score_cuts.py."""
    flash = {int(k): v["new_shot_from"] for k, v in truth.get("flash_transitions", {}).items()}
    uncertain = [int(k) for k in truth.get("uncertain", {})]
    pool = [f for f in found if not any(abs(f - u) <= 2 for u in uncertain)]
    used, hit = set(), []
    for t in truth["cuts"]:
        options = [f for f in pool if t - 2 <= f <= flash.get(t, t) + 2 and f not in used]
        if options:
            used.add(min(options, key=lambda f: abs(f - t)))
            hit.append(t)
    return len(hit), len(truth["cuts"]), sorted(set(truth["cuts"]) - set(hit)), [f for f in pool if f not in used]


refs = {
    "handwritten": (EV / "motion/rotation/frames-handwritten.npy", EV / "shots/truth-handwritten.json"),
    "printed": (EV / "motion/rotation/frames-printed.npy", EV / "shots/truth-printed.json"),
    "third": (HERE / "media/frames.npy", HERE / "truth.json"),
}
lines = [f"== {sys.argv[2] if len(sys.argv) > 2 else sys.argv[1]}"]
for n, (p, t) in refs.items():
    fr = np.load(p).astype(np.float32)
    began = time.monotonic()
    found = m.cut_frames(np, fr)
    hit, total, missed, false = score(found, json.load(open(t)))
    lines.append(f"{n}: {hit}/{total} missed {missed} false {false} found {found} "
                 f"({time.monotonic() - began:.1f} s)")
print("\n".join(lines))
with open(HERE / "detector-check.txt", "a") as f:
    f.write("\n".join(lines) + "\n")
