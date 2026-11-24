"""Short pieces between consecutive pacing/truth boundaries: flash vs real montage shot.

For each piece [a, b) of <= 8 frames: mean luma of the piece vs the frames just before a and at
b, luma std (structure), and whether the picture after the piece matches the one before it.
Usage: python flash_pieces.py   -> flash-pieces.txt
"""
import json, math, sys, urllib.request
from pathlib import Path
import numpy as np

HERE = Path(__file__).parent
EV = HERE.parent
sys.path.insert(0, str(EV.parent / "backend"))
import camera_motion as m  # noqa: E402

refs = {
    "printed (truth montage)": (EV / "motion/rotation/frames-printed.npy",
                                json.load(open(EV / "shots/truth-printed.json"))["cuts"]),
    "handwritten (truth)": (EV / "motion/rotation/frames-handwritten.npy",
                            json.load(open(EV / "shots/truth-handwritten.json"))["cuts"]),
    "third (app pacing cuts, main)": (HERE / "media/frames.npy",
                                      [round(t * 30) for t in json.load(urllib.request.urlopen(
                                          "http://127.0.0.1:8000/api/projects/14470f12-93a3-4031-a00b-85f00194ce20/pacing"))["blueprint"]["candidate_cut_timestamps"]]),
}
out = []
for name, (path, cuts) in refs.items():
    fr = np.load(path).astype(np.float32)
    f = m.cut_features(np, fr, math.inf)
    means, stds = f[0], f[1]
    edges = [0, *cuts, len(fr)]
    out.append(f"== {name}")
    for a, b in zip(edges, edges[1:]):
        if b - a > 8 or a == 0 or b >= len(fr):
            continue
        piece = slice(a, b)
        h, e = m.cut_difference(np, f, a - 1, b)
        out.append(f"  piece {a}-{b} ({b - a} f): mean {means[piece].mean():6.1f} (before {means[a - 1]:6.1f}, "
                   f"after {means[b]:6.1f}) std {stds[piece].mean():5.1f} (before {stds[a - 1]:5.1f}, after {stds[b]:5.1f}) "
                   f"| before vs after: hist {h:.2f} edge {e:.2f}")
(HERE / "flash-pieces.txt").write_text("\n".join(out) + "\n")
print("\n".join(out))
