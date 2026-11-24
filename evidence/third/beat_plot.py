"""Beat evidence for the third reference, using the app's own beats.py (e2e backend).

Writes rhythm-windows.txt (per-window tempo and clarity, whole-track autocorrelation, onset
strength at truth cuts vs random times) and wave.png (4 rows of ~10 s: waveform, onset envelope,
truth cuts in red). Needs media/audio.f32 (ffmpeg -i media/reference.mp4 -ac 1 -ar 22050 -f f32le).
"""
import json, sys
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parents[1] / "backend"))
import beats  # noqa: E402

y = np.fromfile(HERE / "media" / "audio.f32", np.float32)
env = beats.onset_envelope(np, y)
R = beats.RATE
cuts = np.array(json.load(open(HERE / "truth.json"))["cuts"]) / 30
lines = []
n, size, hop = len(env), int(R * beats.WINDOW_SECONDS), int(R * beats.WINDOW_HOP_SECONDS)
starts = list(range(0, max(1, n - size + 1), hop))
if starts[-1] + size < n:
    starts.append(max(0, n - size))
for a in starts:
    p, c = beats.window_tempo(np, env[a:a + size])
    lines.append(f"window {a / R:5.1f}-{min(n, a + size) / R:5.1f} s  {60 * R / p:6.1f} BPM  clarity {c:.3f}"
                 f"  {'clear' if c >= beats.CLEAR else 'not clear'}")
r = beats.analyze(np, y)
lines.append(f"beats.analyze: tempo {r.tempo_bpm} BPM, strength {r.strength}, clear {r.clear}, "
             f"{len(r.beats)} beats (threshold CLEAR={beats.CLEAR})")
e = env - env.mean()
lags = np.arange(int(R * 60 / 200), int(R * 60 / 50) + 1)
ac = np.array([e[:-l] @ e[l:] for l in lags]) / (e @ e)
top = np.argsort(ac)[::-1][:3]
lines.append("whole-track autocorrelation peaks: " + ", ".join(f"{60 * R / lags[i]:.1f} BPM {ac[i]:.3f}" for i in top))
at = lambda t: env[max(0, int(t * R) - 3):int(t * R) + 4].max()
rng = np.random.default_rng(0)
rand = [at(t) for t in rng.uniform(0.5, len(y) / beats.SR - 0.5, 2000)]
cut_on = [at(t) for t in cuts]
lines.append(f"onset envelope max within +-35 ms: truth cuts median {np.median(cut_on):.2f}, random median "
             f"{np.median(rand):.2f}; cuts above the random 75th percentile: "
             f"{sum(c > np.percentile(rand, 75) for c in cut_on)}/{len(cut_on)}")
(HERE / "rhythm-windows.txt").write_text("\n".join(lines) + "\n")
print("\n".join(lines))

W, RH = 1600, 230
dur = len(y) / beats.SR
img = Image.new("RGB", (W, 4 * RH), "white")
d = ImageDraw.Draw(img)
span = dur / 4
for row in range(4):
    t0, top_y = row * span, row * RH
    x = lambda t: (t - t0) / span * W
    seg = np.abs(y[int(t0 * beats.SR):int((t0 + span) * beats.SR)])
    peak = max(1e-6, np.abs(y).max())
    for i, c in enumerate(np.array_split(seg, W)):
        h = (c.max() if len(c) else 0) / peak * 55
        d.line([(i, top_y + 60 - h), (i, top_y + 60 + h)], fill=(130, 130, 130))
    seg_env = env[int(t0 * R):int((t0 + span) * R)]
    d.line([(x(t0 + i / R), top_y + 215 - min(1, v / 6) * 95) for i, v in enumerate(seg_env)], fill="black", width=2)
    for c in cuts:
        if t0 <= c < t0 + span:
            d.line([(x(c), top_y), (x(c), top_y + 220)], fill="red", width=2)
    for s in range(int(t0), int(t0 + span) + 1):
        d.text((x(s) + 2, top_y + 2), f"{s}s", fill="blue")
img.save(HERE / "wave.png")
