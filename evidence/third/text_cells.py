"""Root cause of caption-driven cut errors: is the frame-pair change local (overlay text) or
spread over the picture (a cut)? Measures candidate statistics at truth cuts, at the current
detector's false cuts and over all other frames, for the three references.

Usage: python text_cells.py   -> text-cells.txt
"""
import json, math, sys
from pathlib import Path
import numpy as np

HERE = Path(__file__).parent
EV = HERE.parent
sys.path.insert(0, str(EV.parent / "backend"))
import camera_motion as m  # noqa: E402

ROWS, COLS = 6, 8


def cells(x):
    h, w = x.shape[0] // ROWS * ROWS, x.shape[1] // COLS * COLS
    return x[:h, :w].reshape(ROWS, h // ROWS, COLS, w // COLS).swapaxes(1, 2).reshape(ROWS * COLS, -1)


def stats(a, b):
    """(share of cells whose structure changed, median per-cell edge change)."""
    ga, gb = (np.hypot(*np.gradient(f)) for f in (a, b))
    ca, cb = cells(ga), cells(gb)
    ca = (ca - ca.mean(1, keepdims=True)) / (ca.std(1, keepdims=True) + 1e-6)
    cb = (cb - cb.mean(1, keepdims=True)) / (cb.std(1, keepdims=True) + 1e-6)
    change = 1 - (ca * cb).mean(1)
    return float((change > m.CUT_EDGE).mean()), float(np.median(change))


refs = {
    "handwritten": (EV / "motion/rotation/frames-handwritten.npy", json.load(open(EV / "shots/truth-handwritten.json"))),
    "printed": (EV / "motion/rotation/frames-printed.npy", json.load(open(EV / "shots/truth-printed.json"))),
    "third": (HERE / "media/frames.npy", json.load(open(HERE / "truth.json"))),
}
out = []
for name, (path, truth) in refs.items():
    fr = np.load(path).astype(np.float32)
    cuts = set(truth["cuts"])
    near = {c + d for c in cuts for d in range(-2, 3)}
    for c, v in truth.get("flash_transitions", {}).items():
        near |= set(range(int(c) - 2, v["new_shot_from"] + 3))
    for u in truth.get("uncertain", {}):
        near |= set(range(int(u) - 2, int(u) + 3))
    found = m.cut_frames(np, fr)
    s = [stats(fr[i - 1], fr[i]) if i else (0.0, 0.0) for i in range(len(fr))]
    area, med = np.array([x[0] for x in s]), np.array([x[1] for x in s])
    out.append(f"== {name}: {len(cuts)} truth cuts, detector found {len(found)}")
    out.append("truth cuts (frame: area share, median cell edge change, detected?):")
    out.append("  " + ", ".join(f"{c}: {area[c]:.2f}/{med[c]:.2f}{'' if c in found else ' MISSED'}" for c in sorted(cuts)))
    other = [i for i in range(1, len(fr)) if i not in near]
    false = [f for f in found if f not in near]
    out.append(f"  min over truth cuts: area {min(area[c] for c in cuts):.2f}, median {min(med[c] for c in cuts):.2f}")
    out.append(f"detector false cuts: " + ", ".join(f"{f}: {area[f]:.2f}/{med[f]:.2f}" for f in false))
    out.append(f"other frames: area p99 {np.percentile(area[other], 99):.2f} max {area[other].max():.2f} "
               f"(frame {other[int(area[other].argmax())]}); median p99 {np.percentile(med[other], 99):.2f} "
               f"max {med[other].max():.2f} (frame {other[int(med[other].argmax())]})")
(HERE / "text-cells.txt").write_text("\n".join(out) + "\n")
print("\n".join(out))
