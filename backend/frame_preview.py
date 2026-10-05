"""Transient, bounded before/after frames under the existing shared media lock."""

import base64
import json
import math
import shutil
import struct
import tempfile
import time
from pathlib import Path
from typing import Literal

from pydantic import Field

import color_analysis as color
import color_recipe as recipe
import footage_analysis as footage
import framing
import projects
import reference_engine as engine
import video_render as render
from references import ReferenceError

TOTAL_SECONDS = 30
TEMP_BUDGET = 16 * 1024 * 1024
IMAGE_LIMIT = 3 * 1024 * 1024
TIMING_LIMIT = 512 * 1024
MAX_EDGE = 960


class PreviewRequest(color.Schema):
    expected_recipe_revision: int = Field(ge=1, strict=True)
    expected_framing_revision: int = Field(ge=0, strict=True)
    timestamp_seconds: float = Field(ge=0, le=120.1, strict=True, allow_inf_nan=False)


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


def generate(project_id, request):
    deadline = time.monotonic() + TOTAL_SECONDS
    previous_stop = getattr(engine._control, "stop", None)
    engine._control.stop = None
    directory = None
    safe = True
    try:
        saved, settings = snapshot(project_id, request, deadline)
        source = footage.source(project_id, deadline=deadline)
        root = projects.DATA_DIR / "preview-staging"
        root.mkdir(exist_ok=True)
        directory = Path(tempfile.mkdtemp(dir=root))
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
    except engine.ProcessCleanupError:
        safe = False
        raise ReferenceError(
            500,
            "cleanup_failure",
            "Preview containment failed; staging retained for manual review.",
        ) from None
    except engine.ProbeTimeout:
        raise ReferenceError(
            408, "preview_timeout", "Preview exceeded its 30-second deadline. Retry explicitly."
        ) from None
    except (engine.SizeLimit, engine.ToolOutputError):
        raise ReferenceError(
            413, "preview_limit", "Preview exceeded its image, timing-output or staging limit."
        ) from None
    except engine.RetrievalFailure as exc:
        raise ReferenceError(409, exc.code, exc.message) from None
    except (ValueError, KeyError, StopIteration, FileNotFoundError):
        raise ReferenceError(
            409,
            "preview_failed",
            "A valid footage frame could not be decoded. Check the source and retry.",
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
                    "Preview staging could not be removed; manual review is required.",
                ) from None
