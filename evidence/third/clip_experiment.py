"""Experiment: cap each frame's gradient magnitude at its q-th percentile before the edge-structure
correlation, so a small very sharp overlay (burned-in caption text on a dark, soft picture) cannot
dominate it. Runs the production cut_frames with that one change on the three references and
scores the detector alone against truth (one-to-one within 2 frames; third: flash windows and
uncertain frames as in score_cuts.py).

Usage: python clip_experiment.py   -> clip-experiment.txt
"""
import json, math, sys, time
from pathlib import Path
import numpy as np

HERE = Path(__file__).parent
EV = HERE.parent
sys.path.insert(0, str(EV.parent / "backend"))
import camera_motion as m  # noqa: E402

original = m.cut_features


def capped(q):
    def features(np, frames, deadline):
        means, stds, hists, edges = [], [], [], []
        for frame in frames:
            mean, std = float(frame.mean()), float(frame.std())
            normalized = (frame - mean) / max(std, 4.0)
            gy, gx = np.gradient(frame)
            gradient = np.hypot(gx, gy).ravel()
            if q is not None:
                gradient = np.minimum(gradient, np.percentile(gradient, q))
            means.append(mean)
            stds.append(std)
            hists.append(np.histogram(normalized, m.CUT_BINS, (-3, 3))[0] / frame.size)
            edges.append((gradient - gradient.mean()) / (gradient.std() + 1e-6))
        return np.array(means), np.array(stds), hists, edges
    return features


def score(found, truth):
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
frames = {n: np.load(p).astype(np.float32) for n, (p, _) in refs.items()}
truths = {n: json.load(open(t)) for n, (_, t) in refs.items()}
out = []
for q in [None, 99.5, 99, 98, 95, 90]:
    m.cut_features = capped(q)
    began = time.monotonic()
    row = []
    for n in refs:
        hit, total, missed, false = score(m.cut_frames(np, frames[n]), truths[n])
        row.append(f"{n} {hit}/{total} missed {missed} false {false}")
    out.append(f"cap {'none (production)' if q is None else f'p{q}'} ({time.monotonic() - began:.1f} s): " + " | ".join(row))
    print(out[-1], flush=True)
m.cut_features = original
(HERE / "clip-experiment.txt").write_text("\n".join(out) + "\n")
