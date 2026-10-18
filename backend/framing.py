"""Saved output framing, independent of source analysis and timeline bindings."""

import math
import shutil
import tempfile
import time
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

import color_analysis as color
import footage_analysis as footage
import projects
import reference_engine as engine
from references import ReferenceError

FORMATS = {"portrait": (720, 1280), "square": (720, 720), "landscape": (1280, 720)}


ALGORITHM = "stable-borders-center-v1"


class SlotChoices(color.Schema):
    fit: Literal["automatic", "fit", "fill"] = "automatic"
    horizontal: float = Field(default=0.5, ge=0, le=1, strict=True, allow_inf_nan=False)
    vertical: float = Field(default=0.5, ge=0, le=1, strict=True, allow_inf_nan=False)
    zoom: float = Field(default=1, ge=1, le=2, strict=True, allow_inf_nan=False)
    bars: Literal["auto", "off"] = "auto"
    # Explicit user corrections, fractions of the oriented display (left, top, right, bottom).
    insets: tuple[float, float, float, float] | None = None

    @field_validator("insets", mode="before")
    @classmethod
    def numeric_insets(cls, values):
        if values is not None and (
            not isinstance(values, (list, tuple))
            or any(type(x) not in (int, float) for x in values)
        ):
            raise ValueError("Border trims must be finite numeric fractions")
        return values

    @model_validator(mode="after")
    def bounds(self):
        if self.insets is not None and (
            any(not math.isfinite(x) or not 0 <= x <= 0.45 for x in self.insets)
            or self.insets[0] + self.insets[2] > 0.65
            or self.insets[1] + self.insets[3] > 0.65
        ):
            raise ValueError("Crop corrections must retain at least 35% of each axis")
        return self


class Decision(color.Schema):
    algorithm: Literal["stable-borders-center-v1"] = ALGORITHM
    clip_id: UUID
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    start_frame: int = Field(ge=0)
    end_frame: int = Field(gt=0)
    insets: tuple[float, float, float, float]
    note: str = Field(max_length=500)


class Choices(color.Schema):
    format: Literal["original", "reference", "portrait", "square", "landscape"] = "original"
    fit: Literal["fit", "fill", "automatic"] = "fit"
    bars: Literal["auto", "off"] = "off"
    slots: dict[UUID, SlotChoices] = Field(default_factory=dict, max_length=60)
    horizontal: float = Field(default=0.5, ge=0, le=1, strict=True, allow_inf_nan=False)
    vertical: float = Field(default=0.5, ge=0, le=1, strict=True, allow_inf_nan=False)


class Settings(Choices):
    canvas: tuple[int, int] | None = None
    decisions: dict[str, Decision] = Field(default_factory=dict, max_length=61)
    schema_version: Literal[1] = 1
    revision: int = Field(default=0, ge=0, strict=True)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class Result(color.Schema):
    status: Literal["default", "ready"]
    settings: Settings


class SaveRequest(Choices):
    expected_revision: int = Field(ge=0, strict=True)


def record(project_id, connection):
    projects.identifier(project_id)
    if projects.row_project(connection, project_id)["status"] != "active":
        raise ReferenceError(409, "project_deleting", "Retry project deletion first.")
    return connection.execute(
        "SELECT * FROM framing_settings WHERE project_id=?", (project_id,)
    ).fetchone()


def read(project_id):
    with projects.database() as connection:
        row = record(project_id, connection)
    return Result(
        status="ready" if row else "default",
        settings=Settings.model_validate_json(row["settings"]) if row else Settings(),
    )


def save(project_id, request, *, stop=None, deadline=None):
    if read(project_id).settings.revision != request.expected_revision:
        raise ReferenceError(409, "revision_conflict", "Framing changed. Reload before saving.")
    prepared = prepare(project_id, request, stop=stop, deadline=deadline)
    with projects.database() as connection:
        row = record(project_id, connection)
        if (row["revision"] if row else 0) != request.expected_revision:
            raise ReferenceError(
                409,
                "revision_conflict",
                "Framing changed elsewhere. Reload explicitly; your draft is retained.",
            )
        previous = Settings.model_validate_json(row["settings"]) if row else None
        timestamp = projects.now()
        settings = Settings(
            **request.model_dump(exclude={"expected_revision"}),
            **prepared,
            revision=request.expected_revision + 1,
            created_at=previous.created_at if previous else timestamp,
            updated_at=timestamp,
        )
        connection.execute(
            "INSERT INTO framing_settings(project_id,revision,settings) VALUES (?,?,?) "
            "ON CONFLICT(project_id) DO UPDATE SET "
            "revision=excluded.revision,settings=excluded.settings",
            (project_id, settings.revision, settings.model_dump_json()),
        )
        connection.execute("UPDATE projects SET updated_at=? WHERE id=?", (timestamp, project_id))
    return read(project_id)


def display_dimensions(video):
    sar = Fraction(video.get("sample_aspect_ratio", "1:1").replace(":", "/"))
    if not 0 < sar <= 100:
        raise ValueError("Invalid pixel aspect ratio")
    width, height = float(video["width"] * sar), float(video["height"])
    angle = float(
        next(
            (s["rotation"] for s in video.get("side_data_list", []) if "rotation" in s),
            video.get("tags", {}).get("rotate", 0),
        )
    )
    if not math.isfinite(angle) or abs(angle / 90 - round(angle / 90)) > 0.001:
        raise ValueError("Only orthogonal display rotation is supported")
    if round(angle / 90) % 2:
        width, height = height, width
    if min(width, height) < 2:
        raise ValueError("Video dimensions are too small")
    return width, height


def geometry(video, settings):
    """Even square-pixel scale, final canvas and aligned crop position (0..1)."""
    width, height = display_dimensions(video)
    if settings.format == "original":
        maximum = (720, 1280) if height > width else (1280, 720)
        factor = min(1, maximum[0] / width, maximum[1] / height)
        scaled = (int(width * factor) // 2 * 2, int(height * factor) // 2 * 2)
        if min(scaled) < 2:
            raise ValueError("Video dimensions are too small")
        return scaled, scaled, ""
    canvas = settings.canvas if settings.format == "reference" else FORMATS[settings.format]
    if not canvas:
        raise ValueError("Reference canvas has not been prepared")
    factor = (min if settings.fit != "fill" else max)(canvas[0] / width, canvas[1] / height)
    # Fit rounds down to avoid clipping; Fill rounds up to cover every canvas pixel.
    rounding = math.floor if settings.fit != "fill" else math.ceil
    epsilon = 1e-9 if settings.fit != "fill" else -1e-9
    scaled = tuple(max(2, rounding(size * factor / 2 + epsilon) * 2) for size in (width, height))
    if max(scaled) > 8192:
        raise ValueError("Framing would require a scaled dimension above 8192 pixels")
    if settings.fit != "fill":
        final = f"pad={canvas[0]}:{canvas[1]}:(ow-iw)/2:(oh-ih)/2:color=black"
    else:
        x = round((scaled[0] - canvas[0]) * settings.horizontal / 2) * 2
        y = round((scaled[1] - canvas[1]) * settings.vertical / 2) * 2
        final = f"crop={canvas[0]}:{canvas[1]}:{x}:{y}"
    return scaled, canvas, final


class SourceGeometry(color.Schema):
    display_width: float = Field(gt=0, allow_inf_nan=False)
    display_height: float = Field(gt=0, allow_inf_nan=False)
    original_width: int = Field(ge=2, le=1280)
    original_height: int = Field(ge=2, le=1280)


def inspect_source(project_id, clip_id=None):
    """A short, bounded metadata check under the existing shared slot's start lock."""
    from video_render import probe

    deadline = time.monotonic() + 20
    source = footage.source(project_id, deadline=deadline)
    primary_path = source["path"]
    if clip_id:
        import clip_library

        source = {"path": clip_library.source(project_id, clip_id, deadline=deadline)[0]}
    root = projects.DATA_DIR / "framing-inspection"
    root.mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(dir=root))
    previous_stop = getattr(engine._control, "stop", None)
    engine._control.stop = None
    safe = True
    try:
        video, _, _ = probe(source["path"], directory, deadline)
        width, height = display_dimensions(video)
        primary_video = video if not clip_id else probe(primary_path, directory, deadline)[0]
        original, _, _ = geometry(primary_video, Settings())
        return SourceGeometry(
            display_width=width,
            display_height=height,
            original_width=original[0],
            original_height=original[1],
        )
    except engine.ProcessCleanupError:
        safe = False
        raise ReferenceError(
            500,
            "cleanup_failure",
            "Framing inspection containment failed; staging retained for manual review.",
        ) from None
    except (
        engine.ProbeTimeout,
        engine.SizeLimit,
        engine.ToolOutputError,
        engine.RetrievalFailure,
        ValueError,
        KeyError,
        StopIteration,
    ):
        raise ReferenceError(
            409,
            "geometry_unavailable",
            "Cannot inspect footage framing. Check FFprobe and retained footage, then retry.",
        ) from None
    finally:
        engine._control.stop = previous_stop
        if safe:
            try:
                shutil.rmtree(directory)
            except OSError:
                raise ReferenceError(
                    500, "cleanup_failure", "Framing inspection staging could not be removed."
                ) from None


def detect_borders(frames, width, height):
    """Conservative persistent near-black edges; dark/fading images provide no evidence."""
    candidates = []
    for image in frames:
        if len(image) != width * height:
            raise ValueError("Incomplete border sample")
        interior = sorted(
            image[y * width + x]
            for y in range(height // 4, 3 * height // 4, 3)
            for x in range(width // 4, 3 * width // 4, 3)
        )
        if not interior or interior[len(interior) * 4 // 5] < 40:
            continue

        # Near-uniform black across almost the entire edge, including potential border text.
        def black(line):
            return sum(value > 12 for value in line) <= len(line) * 0.005

        def extent(lines):
            count = 0
            for line in lines:
                if not black(line):
                    break
                count += 1
            return count

        top = extent(image[y * width : (y + 1) * width] for y in range(height))
        bottom = extent(image[y * width : (y + 1) * width] for y in reversed(range(height)))
        left = extent(image[x::width] for x in range(width))
        right = extent(image[x::width] for x in reversed(range(width)))
        candidates.append((left / width, top / height, right / width, bottom / height))
    if len(candidates) < 4:
        return (0, 0, 0, 0), "Uncertain/dark samples: borders retained."
    result = []
    for axis in range(4):
        values = sorted(c[axis] for c in candidates)
        stable = values[-1] - values[0] <= 0.02
        result.append(max(0, values[0] - 0.008) if stable and values[0] >= 0.03 else 0)
    if result[0] + result[2] > 0.65 or result[1] + result[3] > 0.65:
        return (0, 0, 0, 0), "Extreme/ambiguous borders retained; inspect or correct the slot."
    return tuple(result), (
        "Persistent near-black borders removed; fixed crop for this range."
        if any(result)
        else "No confident disposable borders; image retained, including border text."
    )


def sample_decision(path, clip, start, end, directory, deadline):
    from video_render import probe, tool

    video, _, _ = probe(path, directory, deadline, temp_budget=16 * 1024 * 1024)
    width, height = display_dimensions(video)
    factor = min(320 / width, 320 / height)
    w, h = max(2, int(width * factor) // 2 * 2), max(2, int(height * factor) // 2 * 2)
    indices = sorted({min(end - start - 1, int((i + 0.5) * (end - start) / 5)) for i in range(5)})
    if len(indices) < 5:
        return Decision(
            clip_id=clip.id,
            source_hash=clip.sha256,
            start_frame=start,
            end_frame=end,
            insets=(0, 0, 0, 0),
            note="Range too short: borders retained.",
        )
    select = "+".join(f"eq(n,{i})" for i in indices)
    raw = tool(
        [
            shutil.which("ffmpeg") or "ffmpeg",
            "-v",
            "error",
            "-nostdin",
            "-xerror",
            "-threads",
            "2",
            "-protocol_whitelist",
            "file",
            "-ss",
            str(float(video.get("start_time", 0)) + start / 30),
            "-seek_timestamp",
            "1",
            "-i",
            str(path),
            "-t",
            str((end - start) / 30),
            "-vf",
            f"fps=30,select='{select}',scale={w}:{h}:out_range=full,setsar=1,format=gray",
            "-frames:v",
            "5",
            "-fps_mode",
            "vfr",
            "-f",
            "rawvideo",
            "-",
        ],
        directory,
        "framing-samples",
        deadline,
        20,
        5 * w * h + 1,
        temp_budget=16 * 1024 * 1024,
    )
    if len(raw) != 5 * w * h:
        raise ValueError("Missing framing samples")
    insets, note = detect_borders([raw[i * w * h : (i + 1) * w * h] for i in range(5)], w, h)
    return Decision(
        clip_id=clip.id,
        source_hash=clip.sha256,
        start_frame=start,
        end_frame=end,
        insets=insets,
        note=note,
    )


def prepare(project_id, request, *, stop=None, deadline=None):
    """Server-owned bounded evidence. No client-supplied detections or hashes."""
    import clip_library
    import sequence
    from frame_preview import operation
    from video_render import probe

    if (
        request.format != "reference"
        and request.fit != "automatic"
        and request.bars == "off"
        and not request.slots
    ):
        return {}
    with operation(seconds=120) as (directory, operation_deadline):
        deadline = min(deadline or time.monotonic() + 120, time.monotonic() + 120)
        # The caller owns the shared worker/start lock; preserve its cancellation token.
        engine._control.stop = stop
        canvas = None
        if request.format == "reference":
            source = color.source(project_id, stop, deadline)
            video, _, _ = probe(source["path"], directory, deadline, temp_budget=16 * 1024 * 1024)
            canvas = geometry(video, Settings())[1]
        seq = sequence.read(project_id)
        routes = seq.sequence.slots if seq.status == "ready" else []
        if request.slots and any(key not in {s.id for s in routes} for key in request.slots):
            raise ReferenceError(
                409, "framing_stale", "A framing slot changed. Reload the sequence."
            )
        decisions = {}
        previous = read(project_id).settings
        if not routes:
            path, clip = clip_library.source(
                project_id,
                UUID(footage.source(project_id)["identity"].clip_id[5:37]),
                stop=stop,
                deadline=deadline,
            )
            routes = [
                SimpleNamespace(
                    id="whole",
                    clip_id=clip.id,
                    source_hash=clip.sha256,
                    source_start_frame=0,
                    source_end_frame=int(clip.metadata.duration_seconds * 30),
                )
            ]
        for slot in routes:
            color.guard(stop, deadline)
            controls = request.slots.get(
                slot.id,
                SlotChoices(
                    fit=request.fit,
                    horizontal=request.horizontal,
                    vertical=request.vertical,
                    bars=request.bars,
                ),
            )
            if controls.bars == "off" or controls.insets is not None:
                decisions[str(slot.id)] = Decision(
                    clip_id=slot.clip_id,
                    source_hash=slot.source_hash,
                    start_frame=slot.source_start_frame,
                    end_frame=slot.source_end_frame,
                    insets=(0, 0, 0, 0),
                    note="Explicit border correction."
                    if controls.insets
                    else "Embedded-bar removal is Off.",
                )
                continue
            key = str(slot.id)
            old = previous.decisions.get(key)
            if old and (old.algorithm, old.source_hash, old.start_frame, old.end_frame) == (
                ALGORITHM,
                slot.source_hash,
                slot.source_start_frame,
                slot.source_end_frame,
            ):
                decisions[key] = old
                continue
            path, clip = clip_library.source(project_id, slot.clip_id, stop=stop, deadline=deadline)
            decisions[key] = sample_decision(
                path, clip, slot.source_start_frame, slot.source_end_frame, directory, deadline
            )
            clip_library.source(project_id, slot.clip_id, stop=stop, deadline=deadline)
        return dict(canvas=canvas, decisions=decisions)


def transform(video, settings, slot=None, *, canvas=None):
    """Shared preview/export transform: oriented square pixels, active crop, scale, framing."""
    width, height = display_dimensions(video)
    controls = settings.slots.get(slot.id) if slot else None
    controls = controls or SlotChoices(
        fit=settings.fit,
        horizontal=settings.horizontal,
        vertical=settings.vertical,
        bars=settings.bars,
    )
    key = str(slot.id) if slot else "whole"
    decision = settings.decisions.get(key)
    if (
        decision
        and slot
        and (decision.clip_id, decision.source_hash, decision.start_frame, decision.end_frame)
        != (slot.clip_id, slot.source_hash, slot.source_start_frame, slot.source_end_frame)
    ):
        raise ReferenceError(
            409,
            "framing_stale",
            "This slot's framing evidence is stale. Save framing to inspect the changed range.",
        )
    insets = controls.insets or (
        decision.insets if decision and controls.bars == "auto" else (0, 0, 0, 0)
    )
    w, h = int(width) // 2 * 2, int(height) // 2 * 2
    x, y = int(insets[0] * w) // 2 * 2, int(insets[1] * h) // 2 * 2
    cw = max(2, int(w * (1 - insets[0] - insets[2])) // 2 * 2)
    ch = max(2, int(h * (1 - insets[1] - insets[3])) // 2 * 2)
    active = f"scale={w}:{h}:flags=area,setsar=1,crop={cw}:{ch}:{x}:{y}" if any(insets) else ""
    active_video = {"width": cw, "height": ch} if active else video
    if canvas is None:
        canvas = geometry(video, settings)[1]
    mode = controls.fit
    retained = min((canvas[0] / canvas[1]) / (cw / ch), (cw / ch) / (canvas[0] / canvas[1]))
    note = decision.note if decision else "Borders retained (Off or not inspected)."
    if mode == "automatic":
        mode = "fill" if retained >= 0.72 else "fit"
        note += (
            " Center Fill."
            if mode == "fill"
            else " Fit: filling would discard over 28% of the active image."
        )
    if (
        settings.format == "original"
        and not settings.decisions
        and not settings.slots
        and settings.fit != "automatic"
    ):
        # Preserve legacy sequence Fit and whole-source sizing.
        mode = "fit"
    local = Settings(
        format="reference",
        canvas=canvas,
        fit=mode,
        horizontal=controls.horizontal,
        vertical=controls.vertical,
    )
    scale, _, final = geometry(active_video, local)
    if mode == "fill" and controls.zoom > 1:
        scale = tuple(math.ceil(v * controls.zoom / 2) * 2 for v in scale)
        if max(scale) > 8192:
            raise ValueError("Framing zoom exceeds supported dimensions")
        x = round((scale[0] - canvas[0]) * controls.horizontal / 2) * 2
        y = round((scale[1] - canvas[1]) * controls.vertical / 2) * 2
        final = f"crop={canvas[0]}:{canvas[1]}:{x}:{y}"
    return active, scale, canvas, final, note


def automatic_default(project_id, stop=None, deadline=None):
    """Untouched framing uses the reference; saved edits survive retries."""
    if read(project_id).settings.revision == 0:
        return save(
            project_id,
            SaveRequest(expected_revision=0, format="reference", fit="automatic", bars="auto"),
            stop=stop,
            deadline=deadline,
        )
    return read(project_id)


def validate(project_id, settings, sequence=None):
    """Reject affected stale routes without touching color, captions or timing."""
    if sequence:
        for slot in sequence.slots:
            d = settings.decisions.get(str(slot.id))
            if d and (d.clip_id, d.source_hash, d.start_frame, d.end_frame) != (
                slot.clip_id,
                slot.source_hash,
                slot.source_start_frame,
                slot.source_end_frame,
            ):
                raise ReferenceError(
                    409,
                    "framing_stale",
                    "A source range changed. Save framing to refresh affected slots.",
                )
    elif "whole" in settings.decisions:
        identity = footage.source(project_id)["identity"]
        if settings.decisions["whole"].source_hash != identity.media_sha256:
            raise ReferenceError(
                409, "framing_stale", "Footage changed. Save framing before rendering."
            )
