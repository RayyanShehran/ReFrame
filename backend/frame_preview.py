"""Transient, bounded before/after frames under the existing shared media lock."""

import base64
import json
import math
import shutil
import struct
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import Field

import clip_library
import color_analysis as color
import color_recipe as recipe
import footage_analysis as footage
import framing
import grading
import projects
import reference_engine as engine
import sequence
import video_render as render
from references import ReferenceError

TOTAL_SECONDS = 30
TEMP_BUDGET = 16 * 1024 * 1024
IMAGE_LIMIT = 3 * 1024 * 1024
TIMING_LIMIT = 512 * 1024
MAX_EDGE = 960


class PreviewRequest(color.Schema):
    expected_recipe_revision: int = Field(ge=0, strict=True)
    expected_framing_revision: int = Field(ge=0, strict=True)
    timestamp_seconds: float = Field(ge=0, le=120.1, strict=True, allow_inf_nan=False)
    expected_grading_revision: int | None = Field(default=None, ge=0, strict=True)
    clip_id: UUID | None = None
    slot_id: UUID | None = None
    expected_sequence_revision: int | None = Field(default=None, ge=1, strict=True)


class Image(color.Schema):
    width: int = Field(ge=2, le=MAX_EDGE)
    height: int = Field(ge=2, le=MAX_EDGE)
    png_base64: str = Field(max_length=4 * IMAGE_LIMIT // 3)


class Preview(color.Schema):
    schema_version: Literal[1] = 1
    source: footage.Source
    requested_timestamp_seconds: float
    timestamp_seconds: float
    recipe_revision: int
    framing_revision: int
    grading_revision: int | None = None
    clip_id: UUID | None = None
    slot_id: UUID | None = None
    reference: Image | None = None
    original: Image
    edited: Image
    warnings: list[str]
    ffmpeg_version: str


def snapshot(project_id, request, deadline):
    row = recipe.record(project_id)
    if not row or row["state"] != "ready":
        raise ReferenceError(409, "recipe_stale", "Save a valid color recipe before previewing.")
    saved = recipe.Recipe.model_validate_json(row["recipe"])
    settings = framing.read(project_id).settings
    if (saved.revision, settings.revision) != (
        request.expected_recipe_revision,
        request.expected_framing_revision,
    ):
        raise ReferenceError(
            409, "revision_conflict", "Saved settings changed. Reload before previewing."
        )
    # Read stored analyses without the normal read endpoint's invalidation writes.
    with projects.database() as connection:
        for module, bound in ((color, saved.reference), (footage, saved.footage)):
            analysis = connection.execute(
                f"SELECT * FROM {module.TABLE} WHERE project_id=?", (project_id,)
            ).fetchone()
            if not analysis or analysis["state"] != "ready":
                raise ReferenceError(
                    409, "recipe_stale", "Analyze valid source colors before previewing."
                )
            blueprint = module.Blueprint.model_validate_json(analysis["blueprint"])
            current = type(bound)(
                source=blueprint.source,
                operation_id=analysis["operation_id"],
                schema_version=blueprint.schema_version,
                algorithm_version=blueprint.algorithm_version,
                analyzed_at=blueprint.analyzed_at,
            )
            if bound != current or blueprint.algorithm_version != module.ALGORITHM:
                raise ReferenceError(
                    409, "recipe_stale", "Analysis changed. Regenerate explicitly."
                )
    if (
        saved.reference.source != color.source(project_id, deadline=deadline)["identity"]
        or saved.footage.source != footage.source(project_id, deadline=deadline)["identity"]
        or saved.suggestion_algorithm_version != recipe.ALGORITHM
    ):
        raise ReferenceError(
            409, "recipe_stale", "Sources changed. Regenerate the recipe explicitly."
        )
    return saved, settings


def tool(args, directory, name, deadline, limit=65536):
    return render.tool(
        args, directory, name, deadline, TOTAL_SECONDS, limit, temp_budget=TEMP_BUDGET
    )


def moment(path, video, duration, requested, directory, deadline):
    if requested > duration:
        raise ReferenceError(
            422, "invalid_timestamp", "Choose a source time within the footage duration."
        )
    raw = tool(
        [
            shutil.which("ffprobe") or "ffprobe",
            "-v",
            "error",
            "-protocol_whitelist",
            "file",
            "-select_streams",
            str(video["index"]),
            "-show_packets",
            "-show_entries",
            "packet=pts_time",
            "-of",
            "json",
            str(path),
        ],
        directory,
        "preview-timing",
        deadline,
        TIMING_LIMIT,
    )
    packets = json.loads(raw)["packets"]
    if not isinstance(packets, list) or not 1 <= len(packets) <= 32768:
        raise ValueError("Invalid or excessive frame timing")
    if any(
        not isinstance(packet, dict) or not isinstance(packet.get("pts_time"), (str, int, float))
        for packet in packets
    ):
        raise ValueError("Missing frame timestamps")
    times = [float(packet["pts_time"]) for packet in packets]
    if not all(math.isfinite(value) for value in times):
        raise ValueError("Invalid frame timing")
    origin = min(times)
    times = sorted(set(value - origin for value in times))
    if times[-1] >= duration + 0.1:
        raise ValueError("Frame timing exceeds the video duration")
    # The frame displayed at this moment, including the final decodable frame at EOF.
    return max(value for value in times if value <= requested)


def display_size(size):
    factor = min(1, MAX_EDGE / max(size))
    return tuple(max(2, int(value * factor) // 2 * 2) for value in size)


def image(path, expected):
    size = path.stat().st_size
    if not 45 <= size <= IMAGE_LIMIT:
        raise engine.SizeLimit
    raw = path.read_bytes()
    if (
        raw[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        or struct.unpack(">II", raw[16:24]) != expected
        or raw[-8:] != b"IEND\xaeB`\x82"
    ):
        raise ValueError("Missing or invalid decoded preview image")
    return Image(
        width=expected[0], height=expected[1], png_base64=base64.b64encode(raw).decode("ascii")
    )


@contextmanager
def operation():
    deadline = time.monotonic() + TOTAL_SECONDS
    previous_stop = getattr(engine._control, "stop", None)
    engine._control.stop = None
    directory = None
    safe = True
    try:
        root = projects.DATA_DIR / "preview-staging"
        root.mkdir(exist_ok=True)
        directory = Path(tempfile.mkdtemp(dir=root))
        yield directory, deadline
    except engine.ProcessCleanupError:
        safe = False
        raise ReferenceError(
            500,
            "cleanup_failure",
            "Preview containment failed; staging retained for manual review.",
        ) from None
    except engine.ProbeTimeout:
        raise ReferenceError(
            408, "preview_timeout", "Processing exceeded its 30-second deadline. Retry explicitly."
        ) from None
    except (engine.SizeLimit, engine.ToolOutputError):
        raise ReferenceError(
            413, "preview_limit", "Processing exceeded its image, tool-output or staging limit."
        ) from None
    except engine.RetrievalFailure as exc:
        raise ReferenceError(409, exc.code, exc.message) from None
    except (ValueError, KeyError, StopIteration, FileNotFoundError):
        raise ReferenceError(
            409,
            "preview_failed",
            "A valid frame or font could not be decoded. Check the source and retry.",
        ) from None
    finally:
        engine._control.stop = previous_stop
        if directory is not None and safe:
            try:
                shutil.rmtree(directory)
            except OSError:
                raise ReferenceError(
                    500,
                    "cleanup_failure",
                    "Staging could not be removed; manual review is required.",
                ) from None


def generate(project_id, request):
    if request.expected_grading_revision is not None:
        return generate_graded(project_id, request)
    with operation() as (directory, deadline):
        saved, settings = snapshot(project_id, request, deadline)
        source = footage.source(project_id, deadline=deadline)
        video, _, duration = render.probe(
            source["path"], directory, deadline, temp_budget=TEMP_BUDGET
        )
        metadata = color.inspect_colors(video)
        selected = moment(
            source["path"], video, duration, request.timestamp_seconds, directory, deadline
        )
        original_scale, original_canvas, _ = framing.geometry(video, framing.Settings())
        edited_scale, edited_canvas, final = framing.geometry(video, settings)
        original_size, edited_size = display_size(original_canvas), display_size(edited_canvas)
        values = recipe.effective(saved.selected, saved.strength)
        original_filter = render.resize_filter(*original_scale, metadata)
        edited_filter = (
            render.resize_filter(*edited_scale, metadata) + "," + render.color_filter(values)
        )
        if final:
            edited_filter += "," + final
        graph = (
            f"[0:{video['index']}]setpts=PTS-STARTPTS,select='gte(t,{selected:.9f}-0.000001)',"
            "trim=end_frame=1,split=2[before][after];"
            f"[before]{original_filter},scale={original_size[0]}:{original_size[1]}:flags=area,format=rgb24[original];"
            f"[after]{edited_filter},scale={edited_size[0]}:{edited_size[1]}:flags=area,format=rgb24[edited]"
        )
        ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
        version = (
            tool([ffmpeg, "-version"], directory, "preview-version", deadline, 8192)
            .decode()
            .splitlines()[0]
        )
        args = [
            ffmpeg,
            "-v",
            "error",
            "-nostdin",
            "-xerror",
            "-threads",
            "2",
            "-protocol_whitelist",
            "file",
            "-i",
            str(source["path"]),
            "-filter_complex_threads",
            "1",
            "-filter_complex",
            graph,
        ]
        for label in ("original", "edited"):
            args += [
                "-map",
                f"[{label}]",
                "-frames:v",
                "1",
                "-fps_mode",
                "passthrough",
                "-c:v",
                "png",
                "-threads",
                "1",
                "-f",
                "image2",
                "-update",
                "1",
                str(directory / f"{label}.png"),
            ]
        tool(args, directory, "preview-decode", deadline)
        before = image(directory / "original.png", original_size)
        after = image(directory / "edited.png", edited_size)
        # Publication is bound to the same settings and source; never update saved records.
        if (
            snapshot(project_id, request, deadline) != (saved, settings)
            or footage.source(project_id, deadline=deadline)["identity"] != source["identity"]
        ):
            raise ReferenceError(409, "source_changed", "Preview source changed. Retry explicitly.")
        color.guard(None, deadline)
        return Preview(
            source=source["identity"],
            requested_timestamp_seconds=request.timestamp_seconds,
            timestamp_seconds=selected,
            recipe_revision=saved.revision,
            framing_revision=settings.revision,
            original=before,
            edited=after,
            warnings=metadata.warnings,
            ffmpeg_version=version,
        )


def generate_graded(project_id, request):
    from caption_preview import ReferenceRequest, png, reference_frame

    with operation() as (directory, deadline):
        selected_settings = grading.read_settings(project_id)
        if selected_settings.revision != request.expected_grading_revision:
            raise ReferenceError(
                409, "revision_conflict", "Color mode changed. Reload before previewing."
            )
        settings = framing.read(project_id).settings
        if settings.revision != request.expected_framing_revision:
            raise ReferenceError(
                409, "revision_conflict", "Framing changed. Reload before previewing."
            )
        slot = None
        clip_id = request.clip_id
        if request.slot_id:
            seq = sequence.read(project_id)
            if seq.status != "ready" or seq.sequence.revision != request.expected_sequence_revision:
                raise ReferenceError(409, "sequence_stale", "Save and reload the sequence first.")
            slot = next((s for s in seq.sequence.slots if s.id == request.slot_id), None)
            if not slot or (clip_id and clip_id != slot.clip_id):
                raise ReferenceError(422, "invalid_slot", "Choose an assigned saved slot.")
            clip_id = slot.clip_id
            if (
                not slot.source_start_frame / 30
                <= request.timestamp_seconds
                < slot.source_end_frame / 30
            ):
                raise ReferenceError(
                    422, "invalid_timestamp", "Choose a time within this slot's source range."
                )
        if not clip_id:
            clip_id = UUID(footage.source(project_id, deadline=deadline)["identity"].clip_id[5:37])
        source_path, clip = clip_library.source(project_id, clip_id, deadline=deadline)
        bound = grading.snapshot(project_id, [clip_id], [slot] if slot else [], deadline=deadline)
        saved = recipe.read(project_id) if selected_settings.mode == "basic" else None
        if saved and (
            saved.status != "ready" or saved.recipe.revision != request.expected_recipe_revision
        ):
            raise ReferenceError(409, "recipe_stale", "Save and reload a valid Basic recipe first.")
        video, _, duration = render.probe(source_path, directory, deadline, temp_budget=TEMP_BUDGET)
        metadata = color.inspect_colors(video)
        selected = moment(
            source_path, video, duration, request.timestamp_seconds, directory, deadline
        )
        scale, canvas, final = framing.geometry(video, settings)
        size = display_size(canvas)
        resize = render.resize_filter(*scale, metadata)
        grade_filter = (
            render.color_filter(saved.effective)
            if saved
            else grading.filter_for(bound, clip_id, directory, slot)
        )
        prefix = (
            f"[0:{video['index']}]setpts=PTS-STARTPTS,"
            f"select='gte(t,{selected:.9f}-0.000001)',trim=end_frame=1,"
        )

        def picture(effect):
            filters = ",".join(
                p
                for p in (
                    resize,
                    effect,
                    final,
                    f"scale={size[0]}:{size[1]}:flags=area",
                    "format=rgb24",
                )
                if p
            )
            return png(prefix + filters + "[image]", source_path, directory, deadline, size)

        before, after = picture("null"), picture(grade_filter)
        reference = None
        try:
            ref_source = color.source(project_id, deadline=deadline)
        except ReferenceError:
            if selected_settings.mode == "transfer":
                raise
        else:
            ref_time = (
                (slot.reference_start_frame + slot.reference_end_frame) / 60
                if slot and slot.reference_start_frame is not None
                else render.probe(ref_source["path"], directory, deadline, temp_budget=TEMP_BUDGET)[
                    2
                ]
                / 2
            )
            reference = reference_frame(
                project_id,
                ReferenceRequest(
                    timestamp_seconds=ref_time,
                    expected_reference_operation_id=ref_source["reference_operation_id"],
                ),
                stage=(directory, deadline),
            ).image
        if (
            grading.read_settings(project_id) != selected_settings
            or framing.read(project_id).settings != settings
            or clip_library.source(project_id, clip_id, deadline=deadline)[1].sha256 != clip.sha256
            or grading.snapshot(project_id, [clip_id], [slot] if slot else [], deadline=deadline)
            != bound
        ):
            raise ReferenceError(409, "source_changed", "Preview settings or sources changed.")
        color.guard(None, deadline)
        version = (
            tool(
                [shutil.which("ffmpeg") or "ffmpeg", "-version"],
                directory,
                "preview-version",
                deadline,
                8192,
            )
            .decode()
            .splitlines()[0]
        )
        return Preview(
            source=footage.Source(
                project_id=project_id, clip_id=source_path.name, media_sha256=clip.sha256
            ),
            requested_timestamp_seconds=request.timestamp_seconds,
            timestamp_seconds=selected,
            recipe_revision=saved.recipe.revision if saved else 0,
            framing_revision=settings.revision,
            grading_revision=selected_settings.revision,
            clip_id=clip_id,
            slot_id=request.slot_id,
            reference=reference,
            original=before,
            edited=after,
            warnings=metadata.warnings,
            ffmpeg_version=version,
        )
