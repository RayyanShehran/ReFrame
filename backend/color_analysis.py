"""Offline encoded-pixel color observations, versioned separately from edit instructions."""

import hashlib
import json
import math
import re
import shutil
import sys
import time
import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

import projects
import reference_engine as engine
import reference_jobs as jobs
from references import ReferenceError

ALGORITHM = "encoded-rgb-midpoints-v1"
TABLE = "color_operations"
STAGING = "color-staging"
TOTAL_SECONDS = 120
FRAME_SIZE = 96
SAMPLES = 12
FRAME_BYTES = FRAME_SIZE * FRAME_SIZE * 3
OUTPUT_BYTES = SAMPLES * FRAME_BYTES
TEMP_BUDGET = 2 * 1024 * 1024
Unit = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
LIMITS = [
    "Brightness measures encoded RGB pixels, not physical exposure.",
    "No camera settings, LUT, color temperature or creative intent is inferred.",
    "Twelve time samples can miss brief events; short clips may repeat source frames.",
    "Resizing and decoder versions can affect measurements; no tone mapping is performed.",
    "Palette proportions use all sampled pixels; displayed bins may cover less than 100%.",
]


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Source(Schema):
    video_id: str = Field(pattern=r"^[0-9]+$")
    canonical_url: str = Field(pattern=r"^https://www\.tiktok\.com/@[A-Za-z0-9._-]+/video/[0-9]+$")
    media_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class Sampling(Schema):
    method: Literal["12 evenly spaced midpoint targets; FFmpeg fps nearest rounding"]
    timestamps_seconds: list[Annotated[float, Field(gt=0, le=120, allow_inf_nan=False)]] = Field(
        min_length=12, max_length=12
    )
    successful_samples: Literal[12]
    duration_seconds: float = Field(gt=0, le=120, allow_inf_nan=False)
    video_stream_index: int = Field(ge=0)
    frame_width: Literal[96] = 96
    frame_height: Literal[96] = 96
    decoded_bytes: Literal[331776]
    pixel_weighting: Literal[
        "Equal resized pixels and equal time samples; area resize to 96x96 without padding"
    ]
    temporal_rule: Literal[
        "PTS normalized, shifted by half an interval, fps=12/duration"
        " round=near eof_action=pass; frames may repeat"
    ]

    @model_validator(mode="after")
    def midpoints(self):
        for index, timestamp in enumerate(self.timestamps_seconds):
            if not math.isclose(
                timestamp, (index + 0.5) * self.duration_seconds / 12, abs_tol=1e-8
            ):
                raise ValueError("Invalid midpoint target")
        return self


class PaletteColor(Schema):
    hex: str = Field(pattern=r"^#[0-9a-f]{6}$")
    proportion: Unit


class Measurements(Schema):
    rgb_mean: tuple[Unit, Unit, Unit]
    brightness_p05: Unit
    brightness_p50: Unit
    brightness_p95: Unit
    contrast_spread: Unit
    mean_hsv_saturation: Unit
    palette: list[PaletteColor] = Field(min_length=1, max_length=5)
    palette_coverage: Unit
    brightness_method: Literal["(0.2126 R + 0.7152 G + 0.0722 B) / 255; encoded channels"]
    quantile_method: Literal["Linear interpolation at (N-1)*p in sorted sampled pixel values"]
    contrast_method: Literal["brightness_p95 - brightness_p05"]
    palette_method: Literal[
        "RGB bins of width 32; top five by count then lexicographic "
        "bin; mean RGB rounded half up; proportions=count/all pixels"
    ]

    @model_validator(mode="after")
    def consistent(self):
        if not self.brightness_p05 <= self.brightness_p50 <= self.brightness_p95:
            raise ValueError("Invalid percentiles")
        if not math.isclose(
            self.contrast_spread, self.brightness_p95 - self.brightness_p05, abs_tol=1e-8
        ):
            raise ValueError("Invalid contrast")
        if not math.isclose(
            sum(c.proportion for c in self.palette), self.palette_coverage, abs_tol=1e-8
        ):
            raise ValueError("Invalid palette coverage")
        return self


class OtherCategories(Schema):
    pacing: Literal["not_analyzed"] = "not_analyzed"
    transitions: Literal["not_analyzed"] = "not_analyzed"
    captions: Literal["not_analyzed"] = "not_analyzed"
    audio: Literal["not_analyzed"] = "not_analyzed"


class ColorMetadata(Schema):
    color_primaries: str | None
    color_transfer: str | None
    color_space: str | None
    color_range: str | None
    assumption: Literal[
        "Ordinary SDR encoded RGB; missing YCbCr matrix assumed "
        "BT.709, missing range assumed limited"
    ]
    warnings: list[str] = Field(max_length=4)


class Blueprint(Schema):
    schema_version: Literal[1] = 1
    algorithm_version: Literal["encoded-rgb-midpoints-v1"] = ALGORITHM
    analyzed_at: str
    source: Source
    tool_versions: dict[str, str]
    sampling: Sampling
    color_metadata: ColorMetadata
    color: Measurements
    interpretation_limits: list[str] = Field(min_length=1, max_length=10)
    other_categories: OtherCategories = Field(default_factory=OtherCategories)


class Operation(Schema):
    operation_id: str | None = None
    status: Literal["idle", "running", "ready", "failed"] = "idle"
    started_at: str | None = None
    finished_at: str | None = None
    failure_code: str | None = None
    message: str | None = None
    blueprint: Blueprint | None = None


def guard(stop, deadline):
    if stop is not None and stop.is_set():
        raise engine.RetrievalFailure("interrupted", "Color analysis was interrupted.")
    engine.remaining(deadline, TOTAL_SECONDS)


def digest(path, stop=None, deadline=None, *, max_bytes=engine.MAX_BYTES):
    deadline = deadline if deadline is not None else time.monotonic() + 10
    result = hashlib.sha256()
    try:
        before = path.stat()
        if not 0 < before.st_size <= max_bytes:
            raise ValueError("Source size")
        with path.open("rb") as file:
            while True:
                guard(stop, deadline)
                data = file.read(1024 * 1024)
                if not data:
                    break
                result.update(data)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError("Source changed")
    except (OSError, ValueError):
        raise ReferenceError(
            409, "source_unavailable", "Retained analysis media is missing or changed."
        ) from None
    except engine.ProbeTimeout:
        raise ReferenceError(
            503, "deadline", "Source hash verification exceeded its deadline."
        ) from None
    return result.hexdigest()


def source(project_id, stop=None, deadline=None):
    reference = jobs.get_operation(project_id)
    if reference.status != "ready":
        raise ReferenceError(
            409, "reference_not_ready", "Retrieve valid reference media before analysis."
        )
    path = jobs.destination(project_id, reference.operation_id)
    if digest(path, stop, deadline) != reference.media.sha256:
        raise ReferenceError(
            409, "source_changed", "Retained reference bytes no longer match their saved hash."
        )
    with projects.database() as connection:
        project = projects.row_project(connection, project_id)
        saved = json.loads(project["reference"])
    return {
        "path": path,
        "reference_operation_id": reference.operation_id,
        "identity": Source(
            video_id=saved["video_id"],
            canonical_url=saved["canonical_url"],
            media_sha256=reference.media.sha256,
        ),
    }


def staging(operation_id, *, component=None):
    module = component or sys.modules[__name__]
    return projects.DATA_DIR / module.STAGING / projects.identifier(operation_id)


def clean_stage(operation_id, *, component=None):
    module = component or sys.modules[__name__]
    try:
        path = module.staging(operation_id)
        if path.exists():
            shutil.rmtree(path)
    except OSError:
        raise ReferenceError(
            500, "cleanup_failure", "Analysis staging could not be removed."
        ) from None


def fail_operation(project_id, operation_id, failure, *, component=None):
    module = component or sys.modules[__name__]
    with projects.database() as connection:
        connection.execute(
            f"UPDATE {module.TABLE} SET state='failed', blueprint=NULL, finished_at=?, "
            "failure_code=?, message=?, cleanup_safe=? WHERE project_id=? AND operation_id=?",
            (
                projects.now(),
                failure.code,
                failure.message,
                int(failure.cleanup_safe),
                project_id,
                operation_id,
            ),
        )


def get_operation(project_id, require_active=False, *, component=None):
    module = component or sys.modules[__name__]
    projects.identifier(project_id)
    with projects.database() as connection:
        project = projects.row_project(connection, project_id)
        if require_active and project["status"] != "active":
            raise ReferenceError(409, "project_deleting", "Retry project deletion first.")
        row = connection.execute(
            f"SELECT * FROM {module.TABLE} WHERE project_id=?", (project_id,)
        ).fetchone()
    if not row:
        return module.Operation()
    blueprint = None
    if row["state"] == "ready":
        try:
            current = module.source(project_id)
            if row["source_hash"] != current["identity"].media_sha256:
                raise ReferenceError(409, "source_changed", "The analysis source has changed.")
            if row["algorithm_version"] != module.ALGORITHM:
                raise ReferenceError(
                    409, "algorithm_changed", "The analysis algorithm changed. Analyze again."
                )
            blueprint = module.Blueprint.model_validate_json(row["blueprint"])
            if (
                blueprint.source != current["identity"]
                or blueprint.algorithm_version != module.ALGORITHM
            ):
                raise ValueError("Source or algorithm mismatch")
        except (ReferenceError, ValueError, engine.ProbeTimeout) as exc:
            failure = engine.RetrievalFailure(
                exc.code if isinstance(exc, ReferenceError) else "blueprint_invalid",
                exc.message
                if isinstance(exc, ReferenceError)
                else "The saved analysis result is invalid. Retry explicitly.",
            )
            module.fail_operation(project_id, row["operation_id"], failure)
            return module.get_operation(project_id, require_active)
    return module.Operation(
        operation_id=row["operation_id"],
        status=row["state"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        failure_code=row["failure_code"],
        message=row["message"],
        blueprint=blueprint,
    )


def begin_operation(project_id, *, component=None):
    module = component or sys.modules[__name__]
    current = module.get_operation(project_id, True)
    if current.status in {"running", "ready"}:
        return current, None
    current_source = module.source(project_id)
    if current.operation_id:
        module.prepare_delete(project_id)
    operation_id = str(uuid.uuid4())
    with projects.database() as connection:
        projects.row_project(connection, project_id)
        connection.execute(f"DELETE FROM {module.TABLE} WHERE project_id=?", (project_id,))
        connection.execute(
            f"INSERT INTO {module.TABLE}(project_id,operation_id,state,started_at,"
            "source_hash,algorithm_version) VALUES (?,?,'running',?,?,?)",
            (
                project_id,
                operation_id,
                projects.now(),
                current_source["identity"].media_sha256,
                module.ALGORITHM,
            ),
        )
    return module.get_operation(project_id), current_source


def measure(raw, stop=None, deadline=None):
    if len(raw) != OUTPUT_BYTES:
        raise engine.RetrievalFailure("decode_failed", "Expected 12 complete decoded RGB frames.")
    deadline = deadline if deadline is not None else time.monotonic() + TOTAL_SECONDS
    sums, brightness, saturation, bins = [0, 0, 0], [], 0.0, {}
    for offset in range(0, len(raw), 3):
        if offset % 12288 == 0:
            guard(stop, deadline)
        rgb = tuple(raw[offset : offset + 3])
        for channel in range(3):
            sums[channel] += rgb[channel]
        brightness.append((0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) / 255)
        high, low = max(rgb), min(rgb)
        saturation += (high - low) / high if high else 0
        key = tuple(value // 32 for value in rgb)
        count, totals = bins.setdefault(key, [0, [0, 0, 0]])
        bins[key][0] = count + 1
        for channel in range(3):
            totals[channel] += rgb[channel]
    brightness.sort()
    guard(stop, deadline)

    def percentile(p):
        position = (len(brightness) - 1) * p
        lower = math.floor(position)
        upper = min(lower + 1, len(brightness) - 1)
        return brightness[lower] + (brightness[upper] - brightness[lower]) * (position - lower)

    pixels = len(raw) // 3
    palette = []
    for key in sorted(bins, key=lambda key: (-bins[key][0], key))[:5]:
        count, totals = bins[key]
        rgb = [math.floor(value / count + 0.5) for value in totals]
        palette.append(
            PaletteColor(
                hex="#" + "".join(f"{value:02x}" for value in rgb), proportion=count / pixels
            )
        )
    return Measurements(
        rgb_mean=tuple(value / (pixels * 255) for value in sums),
        brightness_p05=percentile(0.05),
        brightness_p50=percentile(0.5),
        brightness_p95=percentile(0.95),
        contrast_spread=percentile(0.95) - percentile(0.05),
        mean_hsv_saturation=saturation / pixels,
        palette=palette,
        palette_coverage=sum(c.proportion for c in palette),
        brightness_method="(0.2126 R + 0.7152 G + 0.0722 B) / 255; encoded channels",
        quantile_method="Linear interpolation at (N-1)*p in sorted sampled pixel values",
        contrast_method="brightness_p95 - brightness_p05",
        palette_method=(
            "RGB bins of width 32; top five by count then lexicographic "
            "bin; mean RGB rounded half up; proportions=count/all pixels"
        ),
    )


def inspect_colors(stream):
    supported = {
        "color_primaries": {"bt709"},
        "color_transfer": {"bt709", "iec61966-2-1", "gamma22"},
        "color_space": {"bt709", "gbr"},
        "color_range": {"tv", "pc"},
    }
    tags, warnings = {}, []
    for key, allowed in supported.items():
        value = stream.get(key)
        if value in {None, "unknown", "unspecified"}:
            tags[key] = None
        elif not isinstance(value, str) or value not in allowed:
            raise engine.RetrievalFailure(
                "unsupported_color",
                "This version supports ordinary BT.709/sRGB SDR only; "
                "HDR/wide-gamut or other tagged color spaces are unsupported.",
            )
        else:
            tags[key] = value
    side_data = stream.get("side_data_list", [])
    if not isinstance(side_data, list) or any(not isinstance(item, dict) for item in side_data):
        raise engine.ToolOutputError
    if any(
        re.search(
            r"mastering display|content light|dovi|dolby|hdr",
            str(item.get("side_data_type", "")),
            re.I,
        )
        for item in side_data
    ):
        raise engine.RetrievalFailure(
            "unsupported_color", "HDR metadata is unsupported; no tone mapping is performed."
        )
    if any(value is None for value in tags.values()):
        warnings.append(
            "Color metadata is incomplete. Ordinary SDR is assumed; "
            "missing matrix uses BT.709 and missing YCbCr range uses "
            "limited. Untagged HDR cannot be identified reliably."
        )
    return ColorMetadata(
        **tags,
        warnings=warnings,
        assumption=(
            "Ordinary SDR encoded RGB; missing YCbCr matrix assumed "
            "BT.709, missing range assumed limited"
        ),
    )


def inspect_video(path, directory, deadline):
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise engine.RetrievalFailure("missing_tools", "Color analysis needs FFmpeg and FFprobe.")
    versions = {}
    for label, executable in [("ffmpeg", ffmpeg), ("ffprobe", ffprobe)]:
        result = engine.run_command(
            [executable, "-version"],
            directory,
            label + "-version",
            deadline,
            5,
            output_limit=8192,
            temp_budget=TEMP_BUDGET,
        )
        match = re.match(r"\w+ version ([\w.+-]{1,80})", result.stdout.decode().splitlines()[0])
        if result.returncode or not match:
            raise engine.ToolOutputError
        versions[label] = match.group(1)
    result = engine.run_command(
        [
            ffprobe,
            "-v",
            "error",
            "-protocol_whitelist",
            "file",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        directory,
        "color-probe",
        deadline,
        10,
        output_limit=65536,
        temp_budget=TEMP_BUDGET,
    )
    if result.returncode:
        raise engine.RetrievalFailure(
            "probe_failed", "Saved analysis media could not be inspected."
        )
    details = json.loads(result.stdout)
    streams = details["streams"]
    if not isinstance(streams, list) or any(
        not isinstance(s, dict) or not isinstance(s.get("disposition", {}), dict) for s in streams
    ):
        raise engine.ToolOutputError
    video = next(
        (
            s
            for s in streams
            if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")
        ),
        None,
    )
    if video is None:
        raise engine.RetrievalFailure("no_video_stream", "No genuine video stream is available.")
    if type(video.get("index")) is not int or video["index"] < 0:
        raise engine.ToolOutputError
    duration = float(video.get("duration", details.get("format", {}).get("duration")))
    if (
        not math.isfinite(duration)
        or not 0 < duration <= 120
        or any(
            type(video.get(key)) is not int or not 0 < video[key] <= 4096
            for key in ("width", "height")
        )
    ):
        raise engine.RetrievalFailure(
            "validation_limit", "Reference duration/dimensions are outside analysis limits."
        )
    metadata = inspect_colors(video)
    return ffmpeg, versions, video, duration, metadata


def pipeline(current_source, directory, stop, deadline, *, component=None):
    module = component or sys.modules[__name__]
    directory.mkdir(parents=True, exist_ok=False)
    engine._control.stop = stop
    try:
        guard(stop, deadline)
        path = current_source["path"]
        if module.digest(path, stop, deadline) != current_source["identity"].media_sha256:
            raise ReferenceError(409, "source_changed", "The retained analysis source has changed.")
        ffmpeg, versions, video, duration, metadata = inspect_video(path, directory, deadline)
        # Normalize stream PTS and shift midpoint targets onto the fps filter's zero-based grid.
        filters = (
            f"setpts=PTS-STARTPTS-{duration / 24:.12f}/TB,"
            f"fps={12 / duration:.12f}:start_time=0:round=near:eof_action=pass,"
            "scale=96:96:flags=area:in_color_matrix=bt709:"
            f"in_range={'full' if metadata.color_range == 'pc' else 'limited'},format=rgb24"
        )
        frames = engine.run_command(
            [
                ffmpeg,
                "-hide_banner",
                "-v",
                "error",
                "-xerror",
                "-abort_on",
                "empty_output",
                "-nostdin",
                "-threads",
                "1",
                "-filter_threads",
                "1",
                "-protocol_whitelist",
                "file",
                "-noautorotate",
                "-i",
                str(path),
                "-map",
                f"0:{video['index']}",
                "-an",
                "-sn",
                "-dn",
                "-vf",
                filters,
                "-frames:v",
                "12",
                "-fps_mode",
                "passthrough",
                "-pix_fmt",
                "rgb24",
                "-f",
                "rawvideo",
                "-",
            ],
            directory,
            "color-frames",
            deadline,
            60,
            output_limit=OUTPUT_BYTES,
            temp_budget=TEMP_BUDGET,
        )
        if frames.returncode or len(frames.stdout) != OUTPUT_BYTES:
            raise engine.RetrievalFailure(
                "decode_failed", "Expected 12 complete decoded RGB frames."
            )
        measurements = measure(frames.stdout, stop, deadline)
        if module.digest(path, stop, deadline) != current_source["identity"].media_sha256:
            raise ReferenceError(409, "source_changed", "Source bytes changed during analysis.")
        return current_source, module.Blueprint(
            analyzed_at=projects.now(),
            source=current_source["identity"],
            tool_versions=versions,
            sampling=Sampling(
                method="12 evenly spaced midpoint targets; FFmpeg fps nearest rounding",
                timestamps_seconds=[(i + 0.5) * duration / 12 for i in range(12)],
                successful_samples=12,
                duration_seconds=duration,
                video_stream_index=video["index"],
                decoded_bytes=len(frames.stdout),
                pixel_weighting=(
                    "Equal resized pixels and equal time samples; area resize to "
                    "96x96 without padding"
                ),
                temporal_rule=(
                    "PTS normalized, shifted by half an interval, fps=12/duration"
                    " round=near eof_action=pass; frames may repeat"
                ),
            ),
            color_metadata=metadata,
            color=measurements,
            interpretation_limits=LIMITS,
        )
    except engine.ProcessCleanupError:
        raise engine.RetrievalFailure(
            "cleanup_failure",
            "Owned analysis processing could not be confirmed stopped; staging retained.",
            False,
        ) from None
    except engine.ProbeInterrupted:
        raise engine.RetrievalFailure("interrupted", "Color analysis was interrupted.") from None
    except engine.SizeLimit:
        raise engine.RetrievalFailure(
            "temporary_size_limit", "Analysis staging exceeded its 2 MiB polled budget."
        ) from None
    except ReferenceError as exc:
        raise engine.RetrievalFailure(exc.code, exc.message) from None
    except (ValueError, KeyError, TypeError, IndexError, UnicodeError, engine.ToolOutputError):
        raise engine.RetrievalFailure(
            "invalid_tool_output", "Color analysis tool output could not be verified."
        ) from None
    finally:
        engine._control.stop = None


def commit(project_id, operation_id, current_source, blueprint, stop, deadline, *, component=None):
    module = component or sys.modules[__name__]
    guard(stop, deadline)
    latest = module.source(project_id, stop, deadline)
    if (
        latest["reference_operation_id"] != current_source["reference_operation_id"]
        or latest["identity"] != blueprint.source
        or blueprint.algorithm_version != module.ALGORITHM
    ):
        raise engine.RetrievalFailure("source_changed", "The analysis source or algorithm changed.")
    module.clean_stage(operation_id)
    guard(stop, deadline)
    with projects.database() as connection:
        project = projects.row_project(connection, project_id)
        row = connection.execute(
            f"SELECT * FROM {module.TABLE} WHERE project_id=?", (project_id,)
        ).fetchone()
        if (
            project["status"] != "active"
            or not row
            or row["operation_id"] != operation_id
            or row["state"] != "running"
            or row["source_hash"] != blueprint.source.media_sha256
            or row["algorithm_version"] != module.ALGORITHM
        ):
            raise engine.RetrievalFailure(
                "interrupted", "The analysis operation is no longer current."
            )
        guard(stop, deadline)
        connection.execute(
            f"UPDATE {module.TABLE} SET state='ready', finished_at=?, "
            "blueprint=?, failure_code=NULL,message=NULL WHERE "
            "project_id=? AND operation_id=?",
            (projects.now(), blueprint.model_dump_json(), project_id, operation_id),
        )
        connection.execute(
            "UPDATE projects SET updated_at=? WHERE id=?", (projects.now(), project_id)
        )


def compensate(project_id, operation_id):
    # The blueprint is a single SQLite commit; no result files need compensation.
    pass


def prepare_delete(project_id, *, component=None):
    module = component or sys.modules[__name__]
    with projects.database() as connection:
        row = connection.execute(
            f"SELECT * FROM {module.TABLE} WHERE project_id=?", (project_id,)
        ).fetchone()
    if row:
        if not row["cleanup_safe"]:
            raise ReferenceError(
                500,
                "cleanup_failure",
                "Owned analysis termination is unconfirmed; staging needs manual review.",
            )
        module.clean_stage(row["operation_id"])


def recover(*, component=None):
    module = component or sys.modules[__name__]
    root = projects.DATA_DIR / module.STAGING
    root.mkdir(exist_ok=True)
    with projects.database() as connection:
        rows = connection.execute(f"SELECT * FROM {module.TABLE}").fetchall()
    for row in rows:
        if row["state"] == "running":
            module.fail_operation(
                row["project_id"],
                row["operation_id"],
                engine.RetrievalFailure(
                    "interrupted",
                    "Backend restarted during analysis. Retry explicitly.",
                    bool(row["cleanup_safe"]),
                ),
            )
        if row["cleanup_safe"]:
            module.clean_stage(row["operation_id"])
        if row["state"] == "ready":
            module.get_operation(row["project_id"])
    known = {row["operation_id"] for row in rows}
    for directory in root.iterdir():
        try:
            projects.identifier(directory.name)
        except ReferenceError:
            continue
        if directory.is_dir() and directory.name not in known:
            module.clean_stage(directory.name)
