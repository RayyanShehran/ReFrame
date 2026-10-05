"""Bounded static-caption fitting with a reviewed face; no OCR or identity claim."""

import base64
import math
import shutil
import statistics
import time
from collections import Counter
from typing import Literal

from pydantic import Field

import caption_preview
import captions
import color_analysis as color
import font_assets as fonts
import font_match as match
import frame_preview as preview
import projects
from references import ReferenceError

METHOD = "static-fill-render-fit-v1"
MAX_FITS = 4
FIELDS = ("size_percent", "horizontal", "vertical", "color", "outline_color", "outline_percent")
UNSUPPORTED = ("bold", "italic", "shadow_color", "shadow_percent", "alignment", "placement")


class Values(color.Schema):
    size_percent: float | None = Field(default=None, ge=2, le=15, allow_inf_nan=False)
    horizontal: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    vertical: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    outline_color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    outline_percent: float | None = Field(default=None, ge=0, le=2, allow_inf_nan=False)


class Suggestion(color.Schema):
    schema_version: Literal[1] = 1
    method: str = Field(max_length=64)
    selection: match.Selection
    selection_revision: int = Field(ge=1)
    selection_token: str = Field(pattern=r"^[0-9a-f]{64}$")
    base_style: captions.Style
    values: Values
    outline_status: Literal["measured", "none_detected", "not_estimated"]
    notes: list[str] = Field(max_length=12)
    fitting_attempts: int = Field(ge=1, le=MAX_FITS)
    generated_at: str
    ffmpeg_version: str = Field(max_length=256)


class Request(color.Schema):
    expected_revision: int = Field(ge=0, strict=True)
    expected_selection_revision: int = Field(ge=1, strict=True)
    expected_selection_token: str = Field(pattern=r"^[0-9a-f]{64}$")
    base_style: captions.Style


class Apply(color.Schema):
    expected_revision: int = Field(ge=1, strict=True)
    token: str = Field(pattern=r"^[0-9a-f]{64}$")
    fields: list[
        Literal[
            "size_percent", "horizontal", "vertical", "color", "outline_color", "outline_percent"
        ]
    ] = Field(min_length=1, max_length=6)
    base_style: captions.Style


def current(project_id, deadline=None):
    saved = match.read(project_id, deadline)
    selection = saved["selection"]
    if saved["status"] != "ready" or not selection or not selection["reviewed_font"]:
        raise ReferenceError(
            409,
            "appearance_prerequisite",
            (
                "Inspect a current reference caption, confirm its text and choose "
                "a font in Find similar fonts first."
            ),
        )
    return saved["revision"], selection, match.fingerprint(selection)


def record(project_id):
    with projects.database() as db:
        projects.row_project(db, project_id)
        return db.execute(
            "SELECT * FROM caption_appearance_suggestions WHERE project_id=?", (project_id,)
        ).fetchone()


def read(project_id):
    row = record(project_id)
    suggestion = Suggestion.model_validate_json(row["suggestion"]) if row else None
    ready, message, selection = False, None, None
    try:
        revision, selected, token = current(project_id, time.monotonic() + 10)
        selection = {"revision": revision, "token": token, "font": selected["reviewed_font"]}
        ready = bool(
            suggestion
            and suggestion.method == METHOD
            and suggestion.selection_token == token
            and suggestion.selection_revision == revision
        )
    except ReferenceError as error:
        message = error.message
    return {
        "revision": row["revision"] if row else 0,
        "status": "ready" if ready else "stale" if suggestion else "empty",
        "suggestion": suggestion.model_dump(mode="json") if suggestion else None,
        "token": match.fingerprint(suggestion.model_dump(mode="json")) if suggestion else None,
        "current_selection": selection,
        "message": message,
    }


def unsupported(style):
    return {field: getattr(style, field) for field in UNSUPPORTED}


def apply(project_id, request):
    saved = read(project_id)
    if (
        saved["status"] != "ready"
        or saved["revision"] != request.expected_revision
        or saved["token"] != request.token
    ):
        raise ReferenceError(
            409,
            "appearance_stale",
            "The reference selection/font or suggestion changed. Suggest again before applying.",
        )
    suggestion = Suggestion.model_validate(saved["suggestion"])
    if request.base_style.font != suggestion.base_style.font or unsupported(
        request.base_style
    ) != unsupported(suggestion.base_style):
        raise ReferenceError(
            409,
            "appearance_stale",
            "Font or unestimated appearance controls changed. Suggest again with this draft.",
        )
    patch = {field: getattr(suggestion.values, field) for field in set(request.fields)}
    if any(value is None for value in patch.values()):
        raise ReferenceError(
            422, "property_not_estimated", "Choose only properties with a measured suggestion."
        )
    return {"patch": patch, "font_hash": suggestion.selection.reviewed_font.sha256}


def unusable(message):
    raise ReferenceError(422, "appearance_unusable", message)


def rgb(path, width, height, directory, deadline):
    target = directory / "pixels.rgb"
    preview.tool(
        [
            shutil.which("ffmpeg") or "ffmpeg",
            "-v",
            "error",
            "-nostdin",
            "-y",
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            str(target),
        ],
        directory,
        "appearance-pixels",
        deadline,
    )
    raw = target.read_bytes()
    target.unlink()
    if len(raw) != width * height * 3:
        unusable("Decoded comparison pixels are incomplete.")
    return raw


def bbox(points, width):
    if not points:
        unusable("No usable fill pixels. Adjust crop, confirmed text or light/dark polarity.")
    xs, ys = [i % width for i in points], [i // width for i in points]
    return min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1


def median_color(raw, points):
    return tuple(round(statistics.median(raw[3 * i + c] for i in points)) for c in range(3))


def distance(a, b):
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def hex_color(rgb):
    return "#" + "".join(f"{v:02X}" for v in rgb)


def fill(raw, width, height, polarity):
    gray = bytes(
        round(0.299 * raw[i] + 0.587 * raw[i + 1] + 0.114 * raw[i + 2])
        for i in range(0, len(raw), 3)
    )
    try:
        _, _, points, _ = match.mask(gray, width, height, polarity, details=True)
    except ReferenceError as error:
        if error.code != "no_useful_font_match":
            raise
        unusable(
            "Fill could not be separated. Adjust crop/text/polarity; "
            "gradients or heavy effects may be unsupported."
        )
    buckets = Counter(tuple(raw[3 * i + c] // 16 for c in range(3)) for i in points)
    dominant = buckets.most_common(1)[0][0]
    core = {i for i in points if tuple(raw[3 * i + c] // 16 for c in range(3)) == dominant}
    ink = median_color(raw, core)
    points = {i for i in points if distance(raw[3 * i : 3 * i + 3], ink) <= 50}
    if len(points) < 30 or len(points) < sum(buckets.values()) * 0.6:
        unusable("Too few consistent fill pixels. Tighten the crop around one static caption.")
    box = bbox(points, width)
    if box[0] < 2 or box[1] < 2 or box[0] + box[2] > width - 2 or box[1] + box[3] > height - 2:
        unusable(
            (
                "Caption pixels touch the crop edge. Expand the region so letters "
                "and effects are not clipped."
            )
        )
    return points, box, ink


def outline(raw, points, width, height, canvas_height):
    border = {
        i
        for i in range(width * height)
        if i % width < 2 or i % width >= width - 2 or i // width < 2 or i // width >= height - 2
    }
    background = median_color(raw, border)
    if sum(distance(raw[3 * i : 3 * i + 3], background) > 30 for i in border) > len(border) * 0.1:
        return "not_estimated", None, None
    # ponytail: uniform border and concentric color rings only; spatial segmentation
    # is needed for graphics/gradients or shadow-vs-outline discrimination.
    expanded, measured, ink = set(points), 0, None
    for radius in range(1, min(8, math.ceil(canvas_height * 0.02) + 2)):
        next_points = set(expanded)
        for i in expanded:
            x, y = i % width, i // width
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)):
                if 0 <= x + dx < width and 0 <= y + dy < height:
                    next_points.add((y + dy) * width + x + dx)
        ring = next_points - expanded
        if not ring:
            return "not_estimated", None, None
        ring_ink = median_color(raw, ring)
        consistent = sum(distance(raw[3 * i : 3 * i + 3], ring_ink) <= 25 for i in ring) / len(ring)
        if distance(ring_ink, background) <= 30:
            break
        if consistent < 0.65:
            if radius == 1:
                expanded = next_points
                continue  # First antialiasing ring is not evidence of an outline.
            return "not_estimated", None, None
        if ink and distance(ink, ring_ink) > 30:
            return "not_estimated", None, None
        measured, ink = radius, ring_ink
        expanded = next_points
    if not measured:
        return "none_detected", None, 0.0
    percent = measured / canvas_height * 100
    if percent > 2:
        return "not_estimated", None, None
    return "measured", hex_color(ink), round(percent, 3)


def render_sample(style, binding, text, width, height, directory, prepared, deadline, *, png=False):
    track = captions.Track(
        style=style, font_binding=binding, cues=[captions.Cue(start=0, end=1, text=text)]
    )
    subtitle = captions.subtitle_filter(track, directory, width, height, prepared[:2])
    target = directory / ("reconstruction.png" if png else "fitting.rgb")
    args = [
        shutil.which("ffmpeg") or "ffmpeg",
        "-v",
        "error",
        "-nostdin",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c={'0x808080' if png else 'black'}:s={width}x{height}:d=0.1",
        "-vf",
        subtitle,
        "-frames:v",
        "1",
    ]
    args += [str(target)] if png else ["-pix_fmt", "rgb24", "-f", "rawvideo", str(target)]
    preview.tool(args, directory, "appearance-render", deadline)
    if png:
        return preview.image(target, (width, height))
    raw = target.read_bytes()
    target.unlink()
    if len(raw) != width * height * 3:
        unusable("Caption calibration did not produce a complete frame.")
    points = {i for i in range(width * height) if raw[3 * i] > 127}
    return bbox(points, width), match.mask(raw[::3], width, height, tight=False)


def generate(project_id, request):
    with preview.operation() as (directory, deadline):
        previous = record(project_id)
        if (previous["revision"] if previous else 0) != request.expected_revision:
            raise ReferenceError(
                409, "revision_conflict", "The saved appearance suggestion changed. Reload it."
            )
        revision, selected, token = current(project_id, deadline)
        if (revision, token) != (
            request.expected_selection_revision,
            request.expected_selection_token,
        ):
            raise ReferenceError(
                409, "appearance_stale", "Reference selection changed. Reload before suggesting."
            )
        selection = match.Selection.model_validate(selected)
        binding = selection.reviewed_font
        if request.base_style.font != binding.choice:
            raise ReferenceError(
                409,
                "appearance_prerequisite",
                "Choose the reviewed font in your caption draft first.",
            )
        if request.base_style.shadow_percent:
            unusable(
                "Shadow is not estimated. Set draft shadow to zero before fitting a static caption."
            )
        frame = caption_preview.reference_frame(
            project_id,
            caption_preview.ReferenceRequest(
                timestamp_seconds=selection.requested_timestamp_seconds,
                expected_reference_operation_id=selection.reference_operation_id,
            ),
            stage=(directory, deadline),
        )
        width, height = frame.image.width, frame.image.height
        source = directory / "source.png"
        source.write_bytes(base64.b64decode(frame.image.png_base64))
        pixels = rgb(source, width, height, directory, deadline)
        r = selection.rectangle
        left, top = math.floor(r.x * width), math.floor(r.y * height)
        cw, ch = (
            min(width - left, round(r.width * width)),
            min(height - top, round(r.height * height)),
        )
        crop = bytes(
            v
            for y in range(top, top + ch)
            for v in pixels[(y * width + left) * 3 : (y * width + left + cw) * 3]
        )
        points, observed, ink = fill(crop, cw, ch, selection.polarity)
        target = (left + observed[0], top + observed[1], observed[2], observed[3])
        reference_shape = match.mask(
            bytes(255 if i in points else 0 for i in range(cw * ch)), cw, ch
        )
        prepared = fonts.prepare(
            project_id,
            binding,
            [captions.Cue(start=0, end=1, text=selection.text)],
            directory,
            deadline,
        )
        neutral = request.base_style.model_copy(
            update={"color": "#FFFFFF", "outline_percent": 0.0, "horizontal": 0.5, "vertical": 0.5}
        )
        size, fits, calibrated = 5.0, 0, None
        for attempt in range(MAX_FITS):
            color.guard(None, deadline)
            calibrated, rendered_shape = render_sample(
                neutral.model_copy(update={"size_percent": size}),
                binding,
                selection.text,
                width,
                height,
                directory,
                prepared,
                deadline,
            )
            fits += 1
            ratio = statistics.median((target[2] / calibrated[2], target[3] / calibrated[3]))
            if abs(ratio - 1) < 0.025:
                break
            next_size = size * ratio
            if not 2 <= next_size <= 15:
                unusable(
                    (
                        "Measured size is outside supported 2–15% caption sizing. Choose "
                        "a larger readable caption region/font."
                    )
                )
            if attempt < MAX_FITS - 1:
                size = next_size
        if max(abs(target[2] / calibrated[2] - 1), abs(target[3] / calibrated[3] - 1)) > 0.15:
            unusable(
                (
                    "Chosen font/text does not fit the caption shape reliably. Check "
                    "words, font, clipping or heavy effects."
                )
            )
        if match.similarity(reference_shape, rendered_shape) < 45:
            unusable(
                "Fill shape disagrees with the reviewed font/text. "
                "Check graphics, effects or the selected face."
            )
        horizontal = 0.5 + (target[0] + target[2] / 2 - calibrated[0] - calibrated[2] / 2) / width
        vertical = 0.5 + (target[1] + target[3] / 2 - calibrated[1] - calibrated[3] / 2) / height
        if not 0 <= horizontal <= 1 or not 0 <= vertical <= 1:
            unusable(
                (
                    "The fitted anchor lies outside the supported canvas. Check "
                    "clipping and manual alignment."
                )
            )
        status, outline_ink, thickness = outline(crop, points, cw, ch, height)
        values = Values(
            size_percent=round(size, 3),
            horizontal=round(horizontal, 5),
            vertical=round(vertical, 5),
            color=hex_color(ink),
            outline_color=outline_ink,
            outline_percent=thickness,
        )
        notes = [
            (
                "Measured static fill/glyph geometry using the reviewed font; no "
                "exact-match guarantee."
            ),
            (
                "Bold, italic, alignment, placement preset, shadow and animation: "
                "Not estimated. Manual draft values are retained."
            ),
            (
                "Placement uses the full normalized reference canvas and the "
                "manual alignment/placement anchor."
            ),
            (
                "Different words or output aspect ratios may change wrapping; "
                "review saved captions on your footage."
            ),
        ]
        notes.append(
            {
                "measured": (
                    "Outline measured from consistent exterior color rings; "
                    "approximate pixel thickness."
                ),
                "none_detected": "No outline detected against the separable background.",
                "not_estimated": (
                    "Outline could not be estimated reliably; retain and review your "
                    "manual outline."
                ),
            }[status]
        )
        suggestion = Suggestion(
            method=METHOD,
            selection=selection,
            selection_revision=revision,
            selection_token=token,
            base_style=request.base_style,
            values=values,
            outline_status=status,
            notes=notes,
            fitting_attempts=fits,
            generated_at=projects.now(),
            ffmpeg_version=frame.ffmpeg_version,
        )
        proposed = request.base_style.model_copy(update=values.model_dump(exclude_none=True))
        reconstruction = render_sample(
            proposed,
            binding,
            selection.text,
            width,
            height,
            directory,
            prepared,
            deadline,
            png=True,
        )
        if current(project_id, deadline) != (revision, selected, token):
            raise ReferenceError(
                409, "appearance_stale", "Reference selection/font changed before publication."
            )
        color.guard(None, deadline)
        with projects.database() as db:
            projects.row_project(db, project_id)
            db.execute(
                (
                    "INSERT INTO caption_appearance_suggestions VALUES (?,?,?) ON "
                    "CONFLICT(project_id) DO UPDATE SET revision=excluded.revision,"
                    "suggestion=excluded.suggestion"
                ),
                (project_id, request.expected_revision + 1, suggestion.model_dump_json()),
            )
        saved = suggestion.model_dump(mode="json")
        return {
            "revision": request.expected_revision + 1,
            "status": "ready",
            "suggestion": saved,
            "token": match.fingerprint(saved),
            "current_selection": {
                "revision": revision,
                "token": token,
                "font": binding.model_dump(mode="json"),
            },
            "message": None,
            "reference": frame.image.model_dump(),
            "reconstruction": reconstruction.model_dump(),
        }
