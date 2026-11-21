"""Reference camera motion: per-shot zoom/pan/shake curves and their zoompan application."""

import logging
import math
import shutil
import time
from typing import Literal

from pydantic import Field

import color_analysis as color
import framing
import video_render as render

logger = logging.getLogger(__name__)

METHOD = "phase-similarity-v1"
LONG_EDGE = 192
WINDOW, STEP = 32, 16
DECODE_SECONDS, ESTIMATE_SECONDS, TOTAL_SECONDS = 30, 30, 60
MAX_RAW_BYTES = 80 * 1024 * 1024
SMOOTH_FRAMES = 19  # 0.6 s at 30 fps: intentional moves are slower than shake.
PUNCH_IN = math.log(1.04)
MAX_KEYFRAMES = 12
TOLERANCE = 0.003
SHAKE_RMS = 0.0015
MAX_BASE_ZOOM = 1.35
MAX_FILTER_POINTS = 40


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
    estimate_seconds: float = Field(ge=0, allow_inf_nan=False)
    frames: int = Field(ge=0, strict=True)


class Measurement(color.Schema):
    method: Literal["phase-similarity-v1"] = METHOD
    width: int = Field(ge=2, le=LONG_EDGE, strict=True)
    height: int = Field(ge=2, le=LONG_EDGE, strict=True)
    shots: list[ShotMotion] = Field(max_length=60)
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
        denominator = left - 2 * peak + right
        return np.where(np.abs(denominator) > 1e-9, 0.5 * (left - right) / denominator, 0.0)

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
    if len(centres) < 6:
        return 0.0, 0.0, gx, gy
    dx, dy, peak = correlate(np, np.array(patches_a) * window, np.array(patches_b) * window)
    dx, dy = dx + ox, dy + oy
    centres = np.array(centres)
    keep = peak > 0.05
    model = (0.0, 0.0, gx, gy)
    for _ in range(3):
        if keep.sum() < 6:
            break
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
    window = hann(np, (WINDOW, WINDOW))
    estimate_deadline = min(deadline, decoded + ESTIMATE_SECONDS)
    result = []
    for start, end in shots:
        color.guard(stop, deadline)
        if start >= len(frames):
            break
        result.append(shot_motion(np, frames, start, end, window, estimate_deadline))
    timings = Timings(
        decode_seconds=round(decoded - began, 3),
        estimate_seconds=round(time.monotonic() - decoded, 3),
        frames=len(frames),
    )
    logger.info(
        "Camera motion: %d frames, %d shots; decode %.2f s, estimate %.2f s",
        timings.frames,
        len(result),
        timings.decode_seconds,
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
    zoom = 1.0
    for scale, x, y, *_ in samples:  # Rotation is measured but not applied.
        cx, cy = abs(x / scale) * height, abs(y / scale) * height
        if 2 * cx >= width or 2 * cy >= height:
            return math.inf
        zoom = max(zoom, 1 / scale, width / ((width - 2 * cx) * scale))
        zoom = max(zoom, height / ((height - 2 * cy) * scale))
    return zoom * 1.0005  # Covers crop rounding at the frame edge.


def scaled(samples, share):
    return [(scale**share, x * share, y * share) for scale, x, y, *_ in samples]


def base_zoom(samples, width, height):
    """Smallest static zoom keeping every moved crop inside the frame, capped."""
    return min(MAX_BASE_ZOOM, required_zoom(samples, width, height))


def piecewise(points):
    """Balanced if-tree over output frame `on` for linear pieces (logarithmic nesting depth)."""

    def piece(i):
        (f0, v0), (f1, v1) = points[i], points[i + 1]
        slope = (v1 - v0) / (f1 - f0)
        return f"({v0:.6f}+{slope:.8f}*(on-{f0}))"

    def tree(first, last):
        if first == last:
            return piece(first)
        middle = (first + last) // 2
        return f"if(lt(on,{points[middle + 1][0]}),{tree(first, middle)},{tree(middle + 1, last)})"

    if len(points) == 1:
        return f"{points[0][1]:.6f}"
    return tree(0, len(points) - 2)


def motion_filter(samples, width, height):
    """zoompan filter applying per-frame (scale, x, y) content motion; never leaves the frame."""
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
        left = width / 2 - x / scale * height - crop_w / 2
        top = height / 2 - y / scale * height - crop_h / 2
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
    z = piecewise([(rows[i][0], rows[i][1]) for i in kept])
    x = piecewise([(rows[i][0], rows[i][2]) for i in kept])
    y = piecewise([(rows[i][0], rows[i][3]) for i in kept])
    graph = (
        f"zoompan=z='{z}':x='clip({x},0,iw-iw/zoom)':y='clip({y},0,ih-ih/zoom)'"
        f":d=1:s={width}x{height}:fps=30"
    )
    return graph, notice
