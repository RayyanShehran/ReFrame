"""Reference camera motion: per-shot zoom/pan/shake curves and their zoompan application."""

import itertools
import logging
import math
import shutil
import time
from typing import Literal

from pydantic import Field

import color_analysis as color
import framing
import projects
import reference_jobs as jobs
from references import ReferenceError

logger = logging.getLogger(__name__)

METHOD = "phase-similarity-v1"
RENDERER_VERSION = "sdr-eq-mp4-v11"
LONG_EDGE = 192
WINDOW, STEP = 32, 16
MIN_WINDOWS = 12
TEXTURED = 0.35  # Share of the grid that must carry texture: one bright light is not a scene.
INLIERS = 0.5
DECODE_SECONDS, ESTIMATE_SECONDS, TOTAL_SECONDS = 30, 30, 60
MAX_RAW_BYTES = 80 * 1024 * 1024
SMOOTH_FRAMES = 19  # 0.6 s at 30 fps: intentional moves are slower than shake.
PUNCH_IN = math.log(1.04)
MAX_KEYFRAMES = 12
TOLERANCE = 0.003
SHAKE_RMS = 0.0015
MAX_BASE_ZOOM = 1.35
MAX_FILTER_POINTS = 40
MAX_SHOTS = 60
# Shot cuts (tuned on two hand-labelled references; see docs/CAMERA_MOTION.md).
CUT_BINS = 24
CUT_STD = 9.0  # Below this luma std a frame is near-black: compare brightness, not structure.
CUT_HIST = 0.12  # Brightness-normalized histogram change.
CUT_EDGE = 0.35  # Edge-structure change (1 - gradient correlation).
CUT_STRONG = 0.75  # Edge change that is a cut even between same-looking histograms.
CUT_LOCAL = 2.0  # A cut stands this far above the motion on both sides.
CUT_JUMP = 20.0  # Mean-luma jump that cuts into or out of near-black.
CUT_FLASH = 3  # A picture that returns within this many frames was a flash.
DISSOLVE = 6


class Keyframe(color.Schema):
    frame: int = Field(ge=0, le=3600, strict=True)
    scale: float = Field(gt=0.2, lt=5, allow_inf_nan=False)
    x: float = Field(ge=-2, le=2, allow_inf_nan=False)
    y: float = Field(ge=-2, le=2, allow_inf_nan=False)
    angle: float = Field(ge=-math.pi, le=math.pi, allow_inf_nan=False)


class Wave(color.Schema):
    amplitude: float = Field(ge=0, le=0.5, allow_inf_nan=False)
    frequency: float = Field(gt=0, le=15, allow_inf_nan=False)
    phase: float = Field(ge=-2 * math.pi, le=2 * math.pi, allow_inf_nan=False)


Move = Literal["zoom_in", "zoom_out", "punch_in", "pan", "rotate", "shake"]


class ShotMotion(color.Schema):
    start_frame: int = Field(ge=0, le=3599, strict=True)
    end_frame: int = Field(ge=1, le=3600, strict=True)
    keyframes: list[Keyframe] = Field(min_length=1, max_length=MAX_KEYFRAMES)
    shake_x: list[Wave] = Field(default_factory=list, max_length=2)
    shake_y: list[Wave] = Field(default_factory=list, max_length=2)
    moves: list[Move] = Field(default_factory=list)


class Timings(color.Schema):
    decode_seconds: float = Field(ge=0, allow_inf_nan=False)
    cuts_seconds: float = Field(default=0, ge=0, allow_inf_nan=False)
    estimate_seconds: float = Field(ge=0, allow_inf_nan=False)
    frames: int = Field(ge=0, strict=True)


class Measurement(color.Schema):
    method: Literal["phase-similarity-v1"] = METHOD
    width: int = Field(ge=2, le=LONG_EDGE, strict=True)
    height: int = Field(ge=2, le=LONG_EDGE, strict=True)
    shots: list[ShotMotion] = Field(max_length=MAX_SHOTS)
    timings: Timings


def numpy():
    try:
        import numpy as np
    except ImportError:
        return None
    return np


def available():
    return numpy() is not None


def decode(path, directory, deadline):
    """Gray frames at 30 fps, long edge 192, from one contained FFmpeg decode."""
    import video_render as render  # The renderer imports this module's settings.

    np = numpy()
    video, _, _ = render.probe(path, directory, deadline)
    width, height = framing.display_dimensions(video)
    factor = LONG_EDGE / max(width, height)
    w, h = (max(2, int(v * factor) // 2 * 2) for v in (width, height))
    raw = render.tool(
        [
            shutil.which("ffmpeg") or "ffmpeg",
            "-v",
            "error",
            "-nostdin",
            "-protocol_whitelist",
            "file",
            "-i",
            str(path),
            "-map",
            f"0:{video['index']}",
            "-vf",
            f"setpts=PTS-STARTPTS,fps=30:round=near,scale={w}:{h}:flags=area,format=gray",
            "-f",
            "rawvideo",
            "-",
        ],
        directory,
        "camera-motion-decode",
        deadline,
        DECODE_SECONDS,
        MAX_RAW_BYTES,
        temp_budget=MAX_RAW_BYTES + 16 * 1024 * 1024,
    )
    frames = len(raw) // (w * h)
    if not frames:
        raise ValueError("No reference frames decoded for camera motion")
    return np.frombuffer(raw[: frames * w * h], np.uint8).reshape(frames, h, w).astype(np.float32)


def cut_features(np, frames, deadline):
    """Per-frame mean, std, brightness-normalized histogram and edge-structure map."""
    means, stds, hists, edges = [], [], [], []
    for frame in frames:
        if time.monotonic() > deadline:
            raise TimeoutError("Shot detection exceeded its bound")
        mean, std = float(frame.mean()), float(frame.std())
        normalized = (frame - mean) / max(std, 4.0)
        gy, gx = np.gradient(frame)
        gradient = np.hypot(gx, gy).ravel()
        means.append(mean)
        stds.append(std)
        hists.append(np.histogram(normalized, CUT_BINS, (-3, 3))[0] / frame.size)
        edges.append((gradient - gradient.mean()) / (gradient.std() + 1e-6))
    return np.array(means), np.array(stds), hists, edges


def cut_difference(np, features, a, b):
    """(histogram change 0..1, edge-structure change 0..2) between frames a and b."""
    _, _, hists, edges = features
    correlation = float(np.dot(edges[a], edges[b])) / len(edges[a])
    return 0.5 * float(np.abs(hists[a] - hists[b]).sum()), 1 - correlation


def cut_frames(np, frames, deadline=math.inf):
    """First frames of new shots: hard cuts, cuts into near-black and dissolves; not flashes."""
    features = cut_features(np, frames, deadline)
    means, stds = features[0], features[1]
    count = len(frames)
    pairs = [cut_difference(np, features, i - 1, i) if i else (0.0, 0.0) for i in range(count)]
    hist, edge = np.array([p[0] for p in pairs]), np.array([p[1] for p in pairs])

    def same(a, b):
        if min(stds[a], stds[b]) < CUT_STD:
            return abs(means[a] - means[b]) < CUT_JUMP / 2
        h, e = cut_difference(np, features, a, b)
        return h < CUT_HIST and e < CUT_EDGE * 0.6

    score = np.zeros(count)
    for i in range(1, count):
        if any(same(i - 1, j) for j in range(i + 1, min(count, i + 1 + CUT_FLASH))) or any(
            same(j, i) for j in range(max(0, i - 1 - CUT_FLASH), i - 1)
        ):
            continue  # A flash: the picture returns within a few frames (or this is its return).
        if min(stds[i - 1], stds[i]) < CUT_STD:
            # Near-black frames carry no structure: a cut is a sudden brightness jump.
            jump = abs(means[i] - means[i - 1])
            score[i] = jump / 50 if jump >= CUT_JUMP else 0
            continue
        # Motion level on each side, two frame pairs deep: shots of 3+ frames keep
        # neighbouring cuts out, and continuous motion (whip pans, moving lights) raises both.
        sides = [edge[max(1, i - 2) : i], edge[i + 1 : i + 3]]
        local = min((side.max() for side in sides if len(side)), default=0.0)
        similar_look = hist[i] < CUT_HIST and edge[i] < CUT_STRONG
        if not similar_look and edge[i] >= max(CUT_EDGE, CUT_LOCAL * local):
            score[i] = hist[i] + edge[i]
    # Dissolves: the middle frame is a blend of two different pictures DISSOLVE frames away.
    for i in range(DISSOLVE, count - DISSOLVE):
        a, b = i - DISSOLVE, i + DISSOLVE
        if score[a : b + 1].any() or min(stds[a], stds[b], stds[i]) < CUT_STD:
            continue
        h, e = cut_difference(np, features, a, b)
        if e < 0.6 or (h < 0.15 and e < CUT_STRONG):
            continue
        spread = float(np.abs(frames[a] - frames[b]).mean())
        residual = float(np.abs(frames[i] - (frames[a] + frames[b]) / 2).mean())
        if residual < 0.25 * spread:
            score[i] = -(1 - residual / spread)  # Negative marks a dissolve candidate.
    found = []
    for i in np.nonzero(score)[0]:
        gradual = score[i] < 0 and found and score[found[-1]] < 0
        if found and i - found[-1] < (2 * DISSOLVE if gradual else 2):
            if abs(score[i]) > abs(score[found[-1]]):
                found[-1] = int(i)
            continue
        found.append(int(i))
    return found


def hann(np, shape):
    return np.outer(np.hanning(shape[0]), np.hanning(shape[1])).astype(np.float32)


def correlate(np, a, b):
    """Sub-pixel shift (dx, dy) that moves a's content onto b's, with the peak strength."""
    spectrum = np.fft.fft2(b) * np.conj(np.fft.fft2(a))
    surface = np.fft.ifft2(spectrum / (np.abs(spectrum) + 1e-6)).real
    h, w = surface.shape[-2:]
    flat = surface.reshape(len(surface), -1)
    index = flat.argmax(axis=1)
    rows, cols = index // w, index % w
    n = np.arange(len(surface))
    peak = flat[n, index]

    def refine(left, right):
        # Foroosh et al. (2002): for a phase-correlation peak, the larger neighbour's share
        # C1 / (C1 + C0) is the sub-pixel offset; a parabola locks toward whole pixels.
        side = np.maximum(left, right)
        share = np.clip(side / np.maximum(side + peak, 1e-9), 0.0, 0.5)
        return np.where(right >= left, share, -share)

    dy = rows + refine(surface[n, (rows - 1) % h, cols], surface[n, (rows + 1) % h, cols])
    dx = cols + refine(surface[n, rows, (cols - 1) % w], surface[n, rows, (cols + 1) % w])
    dy = np.where(dy > h / 2, dy - h, dy)
    dx = np.where(dx > w / 2, dx - w, dx)
    return dx, dy, peak


def pair(np, a, b, window):
    """Similarity (scale*R - I, translation) in pixels mapping frame a onto frame b."""
    h, w = a.shape
    full = hann(np, a.shape)
    gx, gy, _ = correlate(np, (a - a.mean())[None] * full, (b - b.mean())[None] * full)
    gx, gy = float(gx[0]), float(gy[0])
    ox, oy = round(gx), round(gy)
    patches_a, patches_b, centres = [], [], []
    grid = len(range(0, h - WINDOW + 1, STEP)) * len(range(0, w - WINDOW + 1, STEP))
    for top in range(0, h - WINDOW + 1, STEP):
        for left in range(0, w - WINDOW + 1, STEP):
            bt, bl = top + oy, left + ox
            if not (0 <= bt <= h - WINDOW and 0 <= bl <= w - WINDOW):
                continue
            pa = a[top : top + WINDOW, left : left + WINDOW]
            if pa.std() < 4:  # Flat windows carry no motion evidence.
                continue
            patches_a.append(pa - pa.mean())
            pb = b[bt : bt + WINDOW, bl : bl + WINDOW]
            patches_b.append(pb - pb.mean())
            centres.append((left + WINDOW / 2 - w / 2, top + WINDOW / 2 - h / 2))
    if len(centres) < max(MIN_WINDOWS, TEXTURED * grid):
        # Too little texture (dark or flat frames): no evidence, not a whole-frame guess
        # that would follow moving lights or subjects.
        return 0.0, 0.0, 0.0, 0.0
    dx, dy, peak = correlate(np, np.array(patches_a) * window, np.array(patches_b) * window)
    dx, dy = dx + ox, dy + oy
    centres = np.array(centres)
    keep = peak > 0.05
    model = (0.0, 0.0, 0.0, 0.0)
    for _ in range(3):
        if keep.sum() < MIN_WINDOWS:
            return 0.0, 0.0, 0.0, 0.0
        x, y = centres[keep, 0], centres[keep, 1]
        weights = np.sqrt(peak[keep])
        rows = (
            np.concatenate(
                [
                    np.stack([x, -y, np.ones_like(x), np.zeros_like(x)], 1),
                    np.stack([y, x, np.zeros_like(x), np.ones_like(x)], 1),
                ]
            )
            * np.concatenate([weights, weights])[:, None]
        )
        target = np.concatenate([dx[keep], dy[keep]]) * np.concatenate([weights, weights])
        model = tuple(float(v) for v in np.linalg.lstsq(rows, target, rcond=None)[0])
        a_, b_, tx, ty = model
        residual = np.hypot(
            dx - (a_ * centres[:, 0] - b_ * centres[:, 1] + tx),
            dy - (b_ * centres[:, 0] + a_ * centres[:, 1] + ty),
        )
        # Static overlays (captions, logos) and mismatched windows drop out as outliers.
        keep = (peak > 0.05) & (residual <= max(0.75, 3 * float(np.median(residual[keep]))))
    # The camera moves the whole frame; when most textured windows disagree, the motion
    # belongs to a subject, not the camera.
    if keep.sum() < INLIERS * len(centres):
        return 0.0, 0.0, 0.0, 0.0
    return model


def smooth(np, values, splits):
    """Local linear fit over 0.6 s within segments split at punch-ins (exact for ramps)."""
    result = np.empty_like(values)
    bounds = [0] + sorted(splits) + [len(values)]
    half = SMOOTH_FRAMES // 2
    for first, last in zip(bounds, bounds[1:]):
        for i in range(first, last):
            lo, hi = max(first, i - half), min(last, i + half + 1)
            t = np.arange(lo, hi) - i
            if hi - lo < 3:
                result[i] = values[lo:hi].mean()
                continue
            slope, intercept = np.polyfit(t, values[lo:hi], 1)
            result[i] = intercept
    return result


def simplify(points, tolerance):
    """Douglas-Peucker over (frame, values...) with per-channel tolerance already applied."""
    keep = {0, len(points) - 1}
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        if last - first < 2:
            continue
        span = points[last][0] - points[first][0]
        worst, index = 0.0, None
        for i in range(first + 1, last):
            share = (points[i][0] - points[first][0]) / span
            error = max(
                abs(
                    points[i][c] - (points[first][c] + share * (points[last][c] - points[first][c]))
                )
                for c in range(1, len(points[i]))
            )
            if error > worst:
                worst, index = error, i
        if worst > tolerance:
            keep.add(index)
            stack += [(first, index), (index, last)]
    return sorted(keep)


def waves(np, residual):
    n = len(residual)
    if n < 8 or float(np.sqrt(np.mean(residual**2))) < SHAKE_RMS:
        return []
    spectrum = np.fft.rfft(residual)
    found = []
    for k in np.argsort(np.abs(spectrum[1:]))[::-1][:2] + 1:
        amplitude = 2 * abs(spectrum[k]) / n
        if amplitude >= 0.001:
            found.append(
                Wave(
                    amplitude=min(0.5, float(amplitude)),
                    frequency=float(k * 30 / n),
                    phase=float(np.angle(spectrum[k]) + math.pi / 2),
                )
            )
    return found


def shot_motion(np, frames, start, end, window, deadline):
    end = min(end, len(frames))
    count = max(1, end - start)
    log_scale, angle, xs, ys = (np.zeros(count) for _ in range(4))
    anchor, anchor_matrix, anchor_offset = 0, np.eye(2), np.zeros(2)
    splits = []
    height = frames.shape[1]
    for i in range(1, count):
        if time.monotonic() > deadline:
            raise TimeoutError("Camera motion estimation exceeded its bound")
        # Measured against an anchor several frames back: sub-pixel shifts between adjacent
        # frames are biased toward whole pixels and would accumulate as drift.
        a_, b_, tx, ty = pair(np, frames[start + anchor], frames[start + i], window)
        step = np.array([[1 + a_, -b_], [b_, 1 + a_]])
        cumulative = step @ anchor_matrix
        offset = step @ anchor_offset + np.array([tx, ty]) / height
        log_scale[i] = math.log(math.sqrt(abs(np.linalg.det(cumulative))))
        if abs(log_scale[i] - log_scale[i - 1]) > PUNCH_IN:
            splits.append(i)
        if (
            i - anchor >= 8
            or abs(math.log(abs(np.linalg.det(step))) / 2) > 0.04
            or (math.hypot(tx, ty) > 6)
        ):
            anchor, anchor_matrix, anchor_offset = i, cumulative, offset
        angle[i] = math.atan2(cumulative[1, 0], cumulative[0, 0])
        xs[i], ys[i] = offset
    smoothed = [smooth(np, series, splits) for series in (log_scale, xs, ys, angle)]
    # A pan or zoom starts from the shot's first frame: keep the curve anchored there.
    smoothed = [series - series[0] for series in smoothed]
    shake_x, shake_y = waves(np, xs - smoothed[1]), waves(np, ys - smoothed[2])
    scale = np.exp(smoothed[0])
    moves = []
    if splits:
        moves.append("punch_in")
    if scale.max() - 1 >= 0.02 and scale[-1] >= scale[0]:
        moves.append("zoom_in")
    if 1 - scale.min() >= 0.02 and scale[-1] < scale[0]:
        moves.append("zoom_out")
    if max(np.abs(smoothed[1]).max(), np.abs(smoothed[2]).max()) >= 0.015:
        moves.append("pan")
    if np.abs(smoothed[3]).max() >= 0.01:
        moves.append("rotate")
    if shake_x or shake_y:
        moves.append("shake")
    intentional = [m for m in moves if m != "shake"]
    if intentional:
        points = [
            (i, *(s[i] / TOLERANCE for s in (scale, smoothed[1], smoothed[2], smoothed[3])))
            for i in range(count)
        ]
        tolerance = 1.0
        kept = simplify(points, tolerance)
        while len(kept) > MAX_KEYFRAMES:
            tolerance *= 1.5
            kept = simplify(points, tolerance)
    else:
        kept = [0]
    keyframes = [
        Keyframe(
            frame=start + i,
            scale=float(scale[i]) if intentional else 1.0,
            x=float(smoothed[1][i]) if intentional else 0.0,
            y=float(smoothed[2][i]) if intentional else 0.0,
            angle=float(smoothed[3][i]) if intentional else 0.0,
        )
        for i in kept
    ]
    return ShotMotion(
        start_frame=start,
        end_frame=start + count,
        keyframes=keyframes,
        shake_x=shake_x,
        shake_y=shake_y,
        moves=moves,
    )


def measure(path, shots, directory, stop, deadline):
    """Per-shot curves for `shots` [(start_frame, end_frame)] on the 30 fps grid."""
    np = numpy()
    if np is None:
        raise RuntimeError("Camera motion needs the optional visual extra (NumPy)")
    directory.mkdir(parents=True, exist_ok=True)
    deadline = min(deadline, time.monotonic() + TOTAL_SECONDS)
    began = time.monotonic()
    frames = decode(path, directory, deadline)
    decoded = time.monotonic()
    color.guard(stop, deadline)
    estimate_deadline = min(deadline, decoded + ESTIMATE_SECONDS)
    # Saved shots can miss cuts; a curve must never run across one. A detected cut within 2
    # frames of a saved boundary is that same cut.
    cuts = cut_frames(np, frames, estimate_deadline)
    shots = [
        (a, b)
        for start, end in shots
        for a, b in itertools.pairwise([start, *(c for c in cuts if start + 2 < c < end - 2), end])
    ][:MAX_SHOTS]
    cut = time.monotonic()
    color.guard(stop, deadline)
    window = hann(np, (WINDOW, WINDOW))
    result = []
    for start, end in shots:
        color.guard(stop, deadline)
        if start >= len(frames):
            break
        result.append(shot_motion(np, frames, start, end, window, estimate_deadline))
    timings = Timings(
        decode_seconds=round(decoded - began, 3),
        cuts_seconds=round(cut - decoded, 3),
        estimate_seconds=round(time.monotonic() - cut, 3),
        frames=len(frames),
    )
    logger.info(
        "Camera motion: %d frames, %d detected cuts, %d shots; decode %.2f s, cuts %.2f s, "
        "estimate %.2f s",
        timings.frames,
        len(cuts),
        len(result),
        timings.decode_seconds,
        timings.cuts_seconds,
        timings.estimate_seconds,
    )
    return Measurement(width=frames.shape[2], height=frames.shape[1], shots=result, timings=timings)


def sample(shot, frame, shake=True):
    """(scale, x, y, angle) at an absolute reference frame; identity at the shot's start."""
    keys = shot.keyframes
    if frame <= keys[0].frame or len(keys) == 1:
        key = keys[0]
        values = [key.scale, key.x, key.y, key.angle]
    elif frame >= keys[-1].frame:
        key = keys[-1]
        values = [key.scale, key.x, key.y, key.angle]
    else:
        after = next(k for k in keys if k.frame >= frame)
        before = keys[keys.index(after) - 1]
        share = (frame - before.frame) / (after.frame - before.frame)
        values = [
            getattr(before, f) + share * (getattr(after, f) - getattr(before, f))
            for f in ("scale", "x", "y", "angle")
        ]
    if shake:
        t = (frame - shot.start_frame) / 30
        for index, axis in ((1, shot.shake_x), (2, shot.shake_y)):
            values[index] += sum(
                w.amplitude * math.sin(2 * math.pi * w.frequency * t + w.phase) for w in axis
            )
    return tuple(values)


def required_zoom(samples, width, height):
    """Base zoom z0 so that output = z0 * scale * p + (x, y) never leaves the source frame.

    The crop centre moves (x, y) / (z0 * scale) source heights and must stay inside the frame:
    z0 * scale >= 1 + 2|x| * height / width and >= 1 + 2|y|."""
    zoom = 1.0
    for scale, x, y, *_ in samples:  # Rotation is measured but not applied.
        zoom = max(zoom, (1 + 2 * abs(x) * height / width) / scale, (1 + 2 * abs(y)) / scale)
    return zoom * 1.0005  # Covers crop rounding at the frame edge.


def scaled(samples, share):
    return [(scale**share, x * share, y * share) for scale, x, y, *_ in samples]


def base_zoom(samples, width, height):
    """Smallest static zoom keeping every moved crop inside the frame, capped."""
    return min(MAX_BASE_ZOOM, required_zoom(samples, width, height))


def piecewise(points, frame="on"):
    """Balanced if-tree over the output frame for linear pieces (logarithmic nesting depth)."""

    def piece(i):
        (f0, v0), (f1, v1) = points[i], points[i + 1]
        slope = (v1 - v0) / (f1 - f0)
        return f"({v0:.6f}+{slope:.8f}*({frame}-{f0}))"

    def tree(first, last):
        if first == last:
            return piece(first)
        middle = (first + last) // 2
        split = points[middle + 1][0]
        return f"if(lt({frame},{split}),{tree(first, middle)},{tree(middle + 1, last)})"

    if len(points) == 1:
        return f"{points[0][1]:.6f}"
    return tree(0, len(points) - 2)


def motion_filter(samples, width, height, offset=0):
    """zoompan filter applying per-frame (scale, x, y) content motion; never leaves the frame.

    `offset` starts a partial (preview) segment at that slot frame with the slot's base zoom."""
    notice = None
    share = 1.0
    if required_zoom(samples, width, height) > MAX_BASE_ZOOM:
        low, high = 0.0, 1.0
        for _ in range(30):
            middle = (low + high) / 2
            if required_zoom(scaled(samples, middle), width, height) <= MAX_BASE_ZOOM:
                low = middle
            else:
                high = middle
        share = low
        notice = f"Camera move reduced to {round(share * 100)}% to avoid empty edges."
    moved = scaled(samples, share)
    z0 = required_zoom(moved, width, height)
    rows = []
    for frame, (scale, x, y) in enumerate(moved):
        zoom = z0 * scale
        crop_w, crop_h = width / zoom, height / zoom
        # The move is applied after the base zoom, so it keeps its size on screen.
        left = width / 2 - x / zoom * height - crop_w / 2
        top = height / 2 - y / zoom * height - crop_h / 2
        rows.append((frame, zoom, left, top))
    # Breakpoints within a quarter pixel (and 0.00025 zoom), below visible rounding.
    tolerance = 0.25
    while True:
        points = [
            (f, z * 1000 / tolerance * 0.25, lx / tolerance, ty / tolerance)
            for f, z, lx, ty in rows
        ]
        kept = simplify(points, 1.0)
        if len(kept) <= MAX_FILTER_POINTS:
            break
        tolerance *= 1.5
    frame = f"(on+{offset})" if offset else "on"
    z = piecewise([(rows[i][0], rows[i][1]) for i in kept], frame)
    x = piecewise([(rows[i][0], rows[i][2]) for i in kept], frame)
    y = piecewise([(rows[i][0], rows[i][3]) for i in kept], frame)
    graph = (
        f"zoompan=z='{z}':x='clip({x},0,iw-iw/zoom)':y='clip({y},0,ih-ih/zoom)'"
        f":d=1:s={width}x{height}:fps=30"
    )
    return graph, notice


class Settings(color.Schema):
    """Saved, revisioned motion copied from a measurement; what rendering consumes."""

    schema_version: Literal[1] = 1
    revision: int = Field(default=0, ge=0, strict=True)
    enabled: bool = True
    method: Literal["phase-similarity-v1"] | None = None
    reference_operation_id: str | None = None
    reference_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    shots: list[ShotMotion] = Field(default_factory=list, max_length=MAX_SHOTS)
    timings: Timings | None = None
    created_at: str | None = None
    updated_at: str | None = None


class Result(color.Schema):
    status: Literal["default", "ready", "stale"]
    settings: Settings


class SaveRequest(color.Schema):
    expected_revision: int = Field(ge=0, strict=True)
    enabled: bool = Field(strict=True)


def record(project_id, connection):
    projects.identifier(project_id)
    if projects.row_project(connection, project_id)["status"] != "active":
        raise ReferenceError(409, "project_deleting", "Retry project deletion first.")
    return connection.execute(
        "SELECT * FROM camera_motion_settings WHERE project_id=?", (project_id,)
    ).fetchone()


def read(project_id):
    with projects.database() as connection:
        row = record(project_id, connection)
    if not row:
        return Result(status="default", settings=Settings())
    settings = Settings.model_validate_json(row["settings"])
    reference = jobs.get_operation(project_id)
    current = reference.status == "ready" and (
        str(reference.operation_id),
        reference.media.sha256,
    ) == (settings.reference_operation_id, settings.reference_sha256)
    return Result(status="ready" if current or not settings.shots else "stale", settings=settings)


def store(project_id, expected_revision, **changes):
    with projects.database() as connection:
        row = record(project_id, connection)
        previous = Settings.model_validate_json(row["settings"]) if row else Settings()
        if previous.revision != expected_revision:
            raise ReferenceError(
                409, "revision_conflict", "Camera motion changed. Reload before saving."
            )
        timestamp = projects.now()
        settings = previous.model_copy(
            update={
                **changes,
                "revision": expected_revision + 1,
                "created_at": previous.created_at or timestamp,
                "updated_at": timestamp,
            }
        )
        Settings.model_validate(settings.model_dump())
        connection.execute(
            "INSERT INTO camera_motion_settings(project_id,revision,settings) VALUES (?,?,?) "
            "ON CONFLICT(project_id) DO UPDATE SET "
            "revision=excluded.revision,settings=excluded.settings",
            (project_id, settings.revision, settings.model_dump_json()),
        )
        connection.execute("UPDATE projects SET updated_at=? WHERE id=?", (timestamp, project_id))
    return read(project_id)


def save(project_id, request):
    """The user's on/off choice; copied shots are kept."""
    return store(project_id, request.expected_revision, enabled=request.enabled)


def save_analysis(project_id, measurement, expected_revision):
    """Copy a measurement into the saved setting, keeping the user's on/off choice."""
    reference = jobs.get_operation(project_id)
    if reference.status != "ready":
        raise ReferenceError(409, "reference_not_ready", "Retrieve the reference first.")
    return store(
        project_id,
        expected_revision,
        method=measurement.method,
        reference_operation_id=str(reference.operation_id),
        reference_sha256=reference.media.sha256,
        shots=measurement.shots,
        timings=measurement.timings,
    )


def render_binding(project_id, expected_revision, sequence):
    """Saved motion for a sequence render (None when never saved or not a sequence)."""
    current = read(project_id)
    if expected_revision is None:
        if current.settings.revision:
            raise ReferenceError(
                409, "revision_conflict", "Include the saved camera motion revision."
            )
    elif expected_revision != current.settings.revision:
        raise ReferenceError(
            409, "revision_conflict", "Camera motion changed. Reload before rendering."
        )
    if sequence is None or not current.settings.revision:
        return None
    if current.status == "stale" and current.settings.enabled:
        raise ReferenceError(
            409,
            "camera_motion_stale",
            "The reference changed since camera motion was measured. Generate again, "
            "or turn camera motion off.",
        )
    return current.settings


def applies(settings):
    return bool(settings and settings.enabled and any(s.moves for s in settings.shots))


def slot_samples(settings, slot):
    """Per-frame (scale, x, y) for a slot, rebased to identity at its first reference frame.

    A slot whose reference interval spans a cut keeps going from where its curve is with the
    next shot's motion (the footage does not cut there); past the last shot it holds.
    """
    if not applies(settings) or slot.reference_start_frame is None:
        return None
    start = slot.reference_start_frame
    end = start + slot.duration_frames
    if not any(s.moves and s.start_frame < end and start < s.end_frame for s in settings.shots):
        return None
    base, shot, origin, result = (1.0, 0.0, 0.0), None, None, []
    for frame in range(start, end):
        current = next((s for s in settings.shots if s.start_frame <= frame < s.end_frame), None)
        if current is not shot:
            base = result[-1] if result else base
            shot, origin = current, sample(current, frame) if current else None
        if shot is None:
            result.append(base)
            continue
        s, x, y, _ = sample(shot, frame)
        s0, x0, y0, _ = origin
        relative = s / s0
        # This shot's move since `origin`, applied on top of the accumulated `base`.
        bs, bx, by = base
        result.append(
            (relative * bs, relative * bx + x - relative * x0, relative * by + y - relative * y0)
        )
    return result


def analyze_project(project_id, directory, stop, deadline):
    """Measure every saved pacing shot of the retained reference (worker thread)."""
    import pacing_analysis
    import reference_engine as engine

    engine._control.stop = stop
    source = color.source(project_id, stop, deadline)
    pacing = pacing_analysis.get_operation(project_id)
    if pacing.status != "ready":
        raise ReferenceError(409, "pacing_not_ready", "Analyze reference pacing first.")
    shots = [
        (round(shot.start_seconds * 30), min(3600, round(shot.end_seconds * 30)))
        for shot in pacing.blueprint.shots
    ]
    shots = [(start, end) for start, end in shots if end > start][:MAX_SHOTS]
    if directory.exists():
        shutil.rmtree(directory)
    try:
        return measure(source["path"], shots, directory, stop, deadline)
    finally:
        shutil.rmtree(directory, ignore_errors=True)
