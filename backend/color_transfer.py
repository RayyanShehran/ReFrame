"""Fixed encoded-SDR tone/chroma transfer; no inference or per-frame fitting."""

import bisect
import math
from typing import Annotated, Literal

from pydantic import Field, model_validator

import color_analysis as color

ALGORITHM = "regularized-encoded-tone-chroma-v1"
GRID = 17
LUMA = (0.2126, 0.7152, 0.0722)
Finite = Annotated[float, Field(allow_inf_nan=False)]
Unit = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
Cast = Annotated[float, Field(ge=-0.12, le=0.12, allow_inf_nan=False)]


class Model(color.Schema):
    algorithm: Literal["regularized-encoded-tone-chroma-v1"] = ALGORITHM
    tone_x: list[Unit] = Field(min_length=9, max_length=9)
    tone_y: list[Unit] = Field(min_length=9, max_length=9)
    matrix: tuple[tuple[Finite, Finite], tuple[Finite, Finite]]
    casts: tuple[tuple[Cast, Cast], tuple[Cast, Cast], tuple[Cast, Cast]]
    source_pixels: int = Field(ge=1, le=12288)
    reference_pixels: int = Field(ge=1, le=12288)
    warnings: list[str] = Field(max_length=8)

    @model_validator(mode="after")
    def bounded(self):
        if self.tone_x[0] != 0 or self.tone_x[-1] != 1:
            raise ValueError("Tone curve needs endpoints")
        for i in range(8):
            delta = self.tone_x[i + 1] - self.tone_x[i]
            slope = (self.tone_y[i + 1] - self.tone_y[i]) / delta if delta > 0 else 0
            if not 0.25 - 1e-8 <= slope <= 2 + 1e-8:
                raise ValueError("Nonmonotone or excessive tone slope")
        if not all(abs(v) <= 1.5 for row in self.matrix for v in row):
            raise ValueError("Excessive chromatic mapping")
        return self


class Controls(color.Schema):
    strength: float = Field(default=0.5, ge=0, le=1, strict=True, allow_inf_nan=False)
    shadows: float = Field(default=0, ge=-0.1, le=0.1, strict=True, allow_inf_nan=False)
    midtones: float = Field(default=0, ge=-0.1, le=0.1, strict=True, allow_inf_nan=False)
    highlights: float = Field(default=0, ge=-0.1, le=0.1, strict=True, allow_inf_nan=False)
    shadow_red: float = Field(default=0, ge=-0.08, le=0.08, strict=True, allow_inf_nan=False)
    shadow_blue: float = Field(default=0, ge=-0.08, le=0.08, strict=True, allow_inf_nan=False)
    highlight_red: float = Field(default=0, ge=-0.08, le=0.08, strict=True, allow_inf_nan=False)
    highlight_blue: float = Field(default=0, ge=-0.08, le=0.08, strict=True, allow_inf_nan=False)
    saturation: float = Field(default=1, ge=0.8, le=1.2, strict=True, allow_inf_nan=False)


def clamp(value, low=0, high=1):
    return max(low, min(high, value))


def weights(y):
    return (max(0, 1 - 2 * y), 1 - abs(2 * y - 1), max(0, 2 * y - 1))


def pixels(raw):
    """12 x 64x64, stratified 32x32 grid; remove only persistent black edge bands."""
    if len(raw) != 12 * 64 * 64 * 3:
        raise ValueError("Expected twelve complete transfer frames")
    frames = [raw[i * 12288 : (i + 1) * 12288] for i in range(12)]

    def dark_row(y):
        return all(
            max(f[(y * 64 + x) * 3 : (y * 64 + x + 1) * 3]) <= 8
            for f in frames
            for x in range(0, 64, 2)
        )

    def dark_column(x):
        return all(
            max(f[(y * 64 + x) * 3 : (y * 64 + x + 1) * 3]) <= 8
            for f in frames
            for y in range(0, 64, 2)
        )

    top, bottom, left, right = 0, 64, 0, 64
    while top < 16 and dark_row(top):
        top += 1
    while bottom > 48 and dark_row(bottom - 1):
        bottom -= 1
    while left < 16 and dark_column(left):
        left += 1
    while right > 48 and dark_column(right - 1):
        right -= 1
    selected = []
    for f in frames:
        for y in range(top, bottom, 2):
            for x in range(left, right, 2):
                start = (y * 64 + x) * 3
                selected.append(tuple(v / 255 for v in f[start : start + 3]))
    return selected, (top, bottom, left, right) != (0, 64, 0, 64)


def stats(samples):
    if not 1 <= len(samples) <= 12288:
        raise ValueError("Invalid sample count")
    ys, chroma, casts, sums = [], [], [[0.0, 0.0] for _ in range(3)], [0.0] * 3
    for rgb in samples:
        if len(rgb) != 3 or any(not math.isfinite(v) or not 0 <= v <= 1 for v in rgb):
            raise ValueError("Invalid RGB sample")
        y = sum(a * b for a, b in zip(rgb, LUMA))
        c = (rgb[0] - y, rgb[2] - y)
        ys.append(y)
        chroma.append(c)
        for i, w in enumerate(weights(y)):
            sums[i] += w
            for j in range(2):
                casts[i][j] += c[j] * w
    mean = [sum(c[j] for c in chroma) / len(chroma) for j in range(2)]
    covariance = [
        [sum((c[i] - mean[i]) * (c[j] - mean[j]) for c in chroma) / len(chroma) for j in range(2)]
        for i in range(2)
    ]
    # Shrink rare tone regions toward the overall mean rather than amplifying noise.
    casts = [
        [
            (v + mean[j] * len(samples) * 0.05) / (sums[i] + len(samples) * 0.05)
            for j, v in enumerate(row)
        ]
        for i, row in enumerate(casts)
    ]
    return sorted(ys), covariance, casts


def sqrt_matrix(cov):
    # Positive definite 2x2 covariance after a fixed ridge (encoded channel units).
    a, b, d = cov[0][0] + 0.0025, cov[0][1], cov[1][1] + 0.0025
    root = math.sqrt(max(1e-12, a * d - b * b))
    scale = math.sqrt(a + d + 2 * root)
    return ((a + root) / scale, b / scale, (d + root) / scale)


def fit(source, reference, masked=False):
    sy, sc, sm = stats(source)
    ry, rc, rm = stats(reference)
    warnings = ["Different scene content can bias this approximate statistical look."]
    if masked:
        warnings.append("Persistent near-black edge bands were excluded (up to 25% per edge).")

    def quantile(values, p):
        return values[round((len(values) - 1) * p)]

    x = [0.0] + [quantile(sy, i / 8) for i in range(1, 8)] + [1.0]
    # Strict spacing remains well-defined for flat/clipped footage.
    for i in range(1, 8):
        x[i] = clamp(x[i], x[i - 1] + 0.025, 1 - (8 - i) * 0.025)
    target = [0.0] + [quantile(ry, i / 8) for i in range(1, 8)] + [1.0]
    if sy[-1] - sy[0] < 0.02:
        offset = clamp(quantile(ry, 0.5) - quantile(sy, 0.5), -0.15, 0.15)
        target = [clamp(v + offset) for v in x]
        warnings.append(
            "Low footage variation: bounded brightness/casts, neutral chromatic matrix."
        )
    y = [clamp(target[0] - x[0], -0.15, 0.15)]
    for i in range(1, 9):
        desired = x[i] + clamp(0.7 * (target[i] - x[i]), -0.15, 0.15)
        y.append(
            clamp(
                desired,
                y[-1] + 0.25 * (x[i] - x[i - 1]),
                min(1 - 0.25 * (1 - x[i]), y[-1] + 2 * (x[i] - x[i - 1])),
            )
        )
    a, b, d = sqrt_matrix(sc)
    ra, rb, rd = sqrt_matrix(rc)
    det = a * d - b * b
    raw = (
        ((ra * d - rb * b) / det, (-ra * b + rb * a) / det),
        ((rb * d - rd * b) / det, (-rb * b + rd * a) / det),
    )
    matrix = (
        (clamp(raw[0][0], 0.75, 1.35), clamp(raw[0][1], -0.25, 0.25)),
        (clamp(raw[1][0], -0.25, 0.25), clamp(raw[1][1], 0.75, 1.35)),
    )
    if sc[0][0] + sc[1][1] < 0.0004:
        matrix = ((1.0, 0.0), (0.0, 1.0))
    casts = tuple(
        tuple(
            clamp(rm[i][j] - sum(matrix[j][k] * sm[i][k] for k in range(2)), -0.08, 0.08)
            for j in range(2)
        )
        for i in range(3)
    )
    return Model(
        tone_x=x,
        tone_y=y,
        matrix=matrix,
        casts=casts,
        source_pixels=len(source),
        reference_pixels=len(reference),
        warnings=warnings,
    )


def apply(rgb, model, controls):
    if controls.strength == 0:
        return tuple(rgb)
    y = sum(a * b for a, b in zip(rgb, LUMA))
    w = weights(y)
    i = max(0, min(7, bisect.bisect_right(model.tone_x, y) - 1))
    amount = (y - model.tone_x[i]) / (model.tone_x[i + 1] - model.tone_x[i])
    tone = model.tone_y[i] * (1 - amount) + model.tone_y[i + 1] * amount
    tone = clamp(
        tone
        + sum(a * b for a, b in zip(w, (controls.shadows, controls.midtones, controls.highlights)))
    )
    c = (rgb[0] - y, rgb[2] - y)
    mapped = [
        sum(model.matrix[j][k] * c[k] for k in range(2))
        + sum(w[k] * model.casts[k][j] for k in range(3))
        for j in range(2)
    ]
    mapped[0] += w[0] * controls.shadow_red + w[2] * controls.highlight_red
    mapped[1] += w[0] * controls.shadow_blue + w[2] * controls.highlight_blue
    mapped = [v * controls.saturation for v in mapped]
    offsets = (mapped[0], -(LUMA[0] * mapped[0] + LUMA[2] * mapped[1]) / LUMA[1], mapped[1])
    # Compress chroma as a unit into gamut; preserves hue rather than channel clipping.
    factor = min([1.0] + [(1 - tone) / v if v > 0 else -tone / v for v in offsets if v])
    graded = [tone + factor * v for v in offsets]
    return tuple(
        clamp(before + controls.strength * (after - before)) for before, after in zip(rgb, graded)
    )


def cube(path, model, controls):
    lines = ["LUT_3D_SIZE 17", "DOMAIN_MIN 0 0 0", "DOMAIN_MAX 1 1 1"]
    for b in range(GRID):
        for g in range(GRID):
            for r in range(GRID):
                lines.append(
                    " ".join(f"{v:.7f}" for v in apply((r / 16, g / 16, b / 16), model, controls))
                )
    path.write_text("\n".join(lines) + "\n", encoding="ascii")
