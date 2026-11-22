"""Reference music rhythm (NumPy only) and snapping slot cuts to the output's music.

Onsets: log-band spectral flux. Tempo: autocorrelation per 8 s window with a log-normal prior
around 120 BPM. Beats: Ellis (2007) dynamic programming with the local tempo. Only beats in
windows with a clear periodic pulse are kept; see docs/BEAT_SYNC.md.
"""

import math
from typing import Annotated

from pydantic import Field

import color_analysis as color

SR = 22050
HOP = 256
NFFT = 1024  # 46 ms: short enough to place a click within a hop or two.
RATE = SR / HOP  # Onset-envelope frames per second (about 86).
WINDOW_SECONDS, WINDOW_HOP_SECONDS = 8.0, 4.0
LOW_BPM, HIGH_BPM = 60, 200
CLEAR = 0.5  # Normalized autocorrelation: click tracks reach 0.9, the references 0.09-0.36.
TIGHTNESS = 100
TOLERANCE_FRAMES = 2
MIN_SLOT_FRAMES = 3
MAX_BEATS = 1000
Seconds = Annotated[float, Field(ge=0, le=120, allow_inf_nan=False)]


class Rhythm(color.Schema):
    tempo_bpm: float = Field(ge=0, le=HIGH_BPM, allow_inf_nan=False)
    strength: float = Field(ge=-1, le=1, allow_inf_nan=False)
    clear: bool
    beats: list[Seconds] = Field(default_factory=list, max_length=MAX_BEATS)


def onset_envelope(np, samples):
    """Positive log-band spectral flux per hop, centred frames, normalized to unit std."""
    y = np.concatenate([np.zeros(NFFT // 2), np.asarray(samples, np.float32), np.zeros(NFFT // 2)])
    count = 1 + (len(y) - NFFT) // HOP
    if count < 2:
        return np.zeros(max(count, 0))
    frames = np.lib.stride_tricks.as_strided(y, (count, NFFT), (y.strides[0] * HOP, y.strides[0]))
    spectrum = np.abs(np.fft.rfft(frames * np.hanning(NFFT).astype(np.float32), axis=1))
    freqs = np.fft.rfftfreq(NFFT, 1 / SR)
    edges = np.geomspace(30, 11000, 65)
    bands = np.stack(
        [spectrum[:, (freqs >= lo) & (freqs < hi)].sum(1) for lo, hi in zip(edges, edges[1:])], 1
    )
    flux = np.concatenate([[0.0], np.maximum(0, np.diff(np.log1p(100 * bands), axis=0)).mean(1)])
    k = max(1, int(RATE * 0.5))
    envelope = np.maximum(0, flux - np.convolve(flux, np.ones(k) / k, mode="same"))
    return envelope / (envelope.std() + 1e-9)


def window_tempo(np, envelope):
    """(period in envelope frames, normalized autocorrelation at that period)."""
    e = envelope - envelope.mean()
    energy = float(np.dot(e, e))
    lags = np.arange(int(RATE * 60 / HIGH_BPM), int(RATE * 60 / LOW_BPM) + 1)
    lags = lags[lags < len(e) // 2]
    if not len(lags) or energy <= 0:
        return RATE * 0.5, 0.0
    ac = np.array([np.dot(e[:-lag], e[lag:]) for lag in lags]) / energy
    bpm = 60 * RATE / lags
    prior = np.exp(-0.5 * np.log2(bpm / 120) ** 2)
    best = int(np.argmax(ac * prior))
    return float(lags[best]), float(ac[best])


def analyze(np, samples):
    """Rhythm of a mono SR signal: local tempo, clarity, and the clear beats in seconds."""
    envelope = onset_envelope(np, samples)
    n = len(envelope)
    if n < RATE * 2:
        return Rhythm(tempo_bpm=0, strength=0, clear=False)
    size, hop = int(RATE * WINDOW_SECONDS), int(RATE * WINDOW_HOP_SECONDS)
    starts = list(range(0, max(1, n - size + 1), hop))
    if starts[-1] + size < n:
        starts.append(max(0, n - size))
    windows = [(s, *window_tempo(np, envelope[s : s + size])) for s in starts]
    centres = np.array([s + min(size, n - s) / 2 for s, _, _ in windows])
    frame = np.arange(n)
    period = np.interp(frame, centres, [p for _, p, _ in windows])
    nearest = np.abs(frame[:, None] - centres[None, :]).argmin(1)
    clear = np.array([windows[i][2] >= CLEAR for i in nearest])
    # Dynamic programming: each beat is preceded by one about a local period earlier.
    score = envelope.astype(float).copy()
    back = np.full(n, -1)
    for i in range(n):
        lo, hi = i - int(2 * period[i]), i - int(period[i] / 2)
        if hi <= 0:
            continue
        previous = np.arange(max(0, lo), hi)
        candidates = score[previous] - TIGHTNESS * np.log((i - previous) / period[i]) ** 2
        j = int(np.argmax(candidates))
        if candidates[j] > 0:
            score[i] += candidates[j]
            back[i] = previous[j]
    tail = np.arange(max(0, n - int(period[-1])), n)
    i = int(tail[np.argmax(score[tail])])
    found = []
    while i >= 0:
        found.append(i)
        i = back[i]
    # The tracker keeps its grid going through silence; beats belong where the music plays.
    strong = np.nonzero(envelope >= 2)[0]
    first, last = (strong[0], strong[-1]) if len(strong) else (n, -1)
    margin = 0.1 * RATE
    beats = [
        round(f / RATE, 4)
        for f in reversed(found)
        if clear[f] and first - margin <= f <= last + margin
    ][:MAX_BEATS]
    periods = [p for _, p, s in windows if s >= CLEAR] or [p for _, p, _ in windows]
    tempo_period = float(np.median(periods))
    return Rhythm(
        tempo_bpm=round(60 * RATE / tempo_period, 2),
        strength=round(max(s for _, _, s in windows), 4),
        clear=bool(beats),
        beats=beats,
    )


def nearest(times, t):
    return min(times, key=lambda b: abs(b - t))


def snap(cuts, reference_beats, output_beats, total_frames):
    """Move on-beat cuts onto the output music's beats, keeping their phase against the beat.

    `cuts` are slot boundaries in 30 fps frames on the reference timeline. A cut within 2 frames
    of a reference beat moves to the nearest output beat plus the same phase, when that moves
    it by at most 2 frames and keeps every slot at least 3 frames long. Returns (cuts, moved).
    """
    result, moved = list(cuts), 0
    if not reference_beats or not output_beats:
        return result, moved
    tolerance = TOLERANCE_FRAMES / 30 + 1e-9
    for index, cut in enumerate(cuts):
        t = cut / 30
        beat = nearest(reference_beats, t)
        if abs(t - beat) > tolerance:
            continue  # Off-beat in the reference: keep it off-beat.
        frame = math.floor((nearest(output_beats, t) + t - beat) * 30 + 0.5)
        if frame == cut or abs(frame - cut) > TOLERANCE_FRAMES:
            continue
        before = result[index - 1] if index else 0
        after = cuts[index + 1] if index + 1 < len(cuts) else total_frames
        if frame - before < MIN_SLOT_FRAMES or after - frame < MIN_SLOT_FRAMES:
            continue
        result[index], moved = frame, moved + 1
    return result, moved
