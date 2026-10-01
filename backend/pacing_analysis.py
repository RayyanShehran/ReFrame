"""Offline estimated hard cuts; shares the color operation lifecycle and storage rules."""

import math
import re
import statistics
import sys
from functools import partial
from typing import Annotated, Literal

from pydantic import Field, model_validator

import color_analysis as color
import projects
import reference_engine as engine
from references import ReferenceError

ALGORITHM = "scdet-consecutive-v1"
TABLE = "pacing_operations"
STAGING = "pacing-staging"
TOTAL_SECONDS = 120
THRESHOLD = 10
MAX_CUTS = 1000
OUTPUT_LIMIT = 1024 * 1024
TEMP_BUDGET = color.TEMP_BUDGET
Seconds = Annotated[float, Field(ge=0, le=120, allow_inf_nan=False)]
PositiveSeconds = Annotated[float, Field(gt=0, le=120, allow_inf_nan=False)]
LIMITS = [
    "Estimated cuts and pacing, not transition-type recognition.",
    "Flashes and motion can cause false positives; similar-looking shots can hide cuts.",
    "Threshold 10, downscaling and FFmpeg versions affect detected boundaries.",
]


class Shot(color.Schema):
    start_seconds: Seconds
    end_seconds: PositiveSeconds
    duration_seconds: PositiveSeconds


class OtherCategories(color.Schema):
    transitions: Literal["not_analyzed"] = "not_analyzed"
    captions: Literal["not_analyzed"] = "not_analyzed"
    audio: Literal["not_analyzed"] = "not_analyzed"


class Blueprint(color.Schema):
    schema_version: Literal[1] = 1
    algorithm_version: Literal["scdet-consecutive-v1"] = ALGORITHM
    analyzed_at: str
    source: color.Source
    tool_versions: dict[str, str]
    duration_seconds: PositiveSeconds
    video_stream_index: int = Field(ge=0)
    processing_width: int = Field(ge=2, le=320)
    processing_height: int = Field(ge=2, le=320)
    threshold: Literal[10] = THRESHOLD
    successful_frames: int = Field(ge=1)
    method: Literal[
        "Consecutive decoded frames; normalized PTS; area resize, aspect preserved "
        "within even-pixel rounding, no crop; YUV420P; scdet threshold=10 sc_pass=0"
    ]
    candidate_cut_timestamps: list[PositiveSeconds] = Field(max_length=1000)
    estimated_cut_count: int = Field(ge=0, le=1000)
    shot_count: int = Field(ge=1, le=1001)
    shots: list[Shot] = Field(min_length=1, max_length=1001)
    mean_shot_seconds: PositiveSeconds
    median_shot_seconds: PositiveSeconds
    cuts_per_minute: float = Field(ge=0, allow_inf_nan=False)
    color_metadata: color.ColorMetadata
    interpretation_limits: list[str] = Field(max_length=10)
    other_categories: OtherCategories = Field(default_factory=OtherCategories)

    @model_validator(mode="after")
    def consistent(self):
        cuts = self.candidate_cut_timestamps
        if cuts != sorted(set(cuts)) or any(t >= self.duration_seconds for t in cuts):
            raise ValueError("Invalid cut boundaries")
        boundaries = [0, *cuts, self.duration_seconds]
        lengths = [end - start for start, end in zip(boundaries, boundaries[1:])]
        if self.estimated_cut_count != len(cuts) or self.shot_count != len(lengths):
            raise ValueError("Invalid shot count")
        if len(self.shots) != len(lengths):
            raise ValueError("Invalid shot intervals")
        for shot, start, end in zip(self.shots, boundaries, boundaries[1:]):
            if (shot.start_seconds, shot.end_seconds) != (start, end) or not math.isclose(
                shot.duration_seconds, end - start, abs_tol=1e-8
            ):
                raise ValueError("Noncontiguous shot intervals")
        for actual, expected in [
            (self.mean_shot_seconds, statistics.mean(lengths)),
            (self.median_shot_seconds, statistics.median(lengths)),
            (self.cuts_per_minute, len(cuts) * 60 / self.duration_seconds),
        ]:
            if not math.isclose(actual, expected, abs_tol=1e-8):
                raise ValueError("Invalid pacing calculation")
        return self


class Operation(color.Operation):
    blueprint: Blueprint | None = None


# Bind only the existing persistence functions to this independent table/schema.
source = color.source
component = sys.modules[__name__]
staging = partial(color.staging, component=component)
clean_stage = partial(color.clean_stage, component=component)
fail_operation = partial(color.fail_operation, component=component)
get_operation = partial(color.get_operation, component=component)
begin_operation = partial(color.begin_operation, component=component)
commit = partial(color.commit, component=component)
prepare_delete = partial(color.prepare_delete, component=component)
recover = partial(color.recover, component=component)
compensate = color.compensate


def parse_detector(raw, duration, stop, deadline):
    """Require a score/MAFD record for every decoded frame, even when there are no cuts."""
    if not raw or not raw.endswith(b"\n"):
        raise engine.RetrievalFailure(
            "detector_output_missing", "Complete detector output is missing."
        )
    count, cuts, record, previous_time = 0, [], None, -1.0

    def finish_record():
        nonlocal count, previous_time
        if record is None or not {"mafd", "score"} <= record.keys():
            raise engine.ToolOutputError
        timestamp = record["timestamp"]
        if not 0 <= timestamp <= duration or timestamp < previous_time:
            raise engine.ToolOutputError
        if count == 0 and timestamp != 0:
            raise engine.ToolOutputError
        previous_time = timestamp
        count += 1
        if "time" in record:
            if record["score"] < THRESHOLD or abs(record["time"] - timestamp) > 0.002:
                raise engine.ToolOutputError
            cuts.append(timestamp)
            if len(cuts) > MAX_CUTS:
                raise engine.RetrievalFailure(
                    "cut_limit", "More than 1,000 cut candidates detected."
                )
        elif record["score"] > THRESHOLD:
            raise engine.ToolOutputError

    for line in raw.decode("utf-8").splitlines():
        color.guard(stop, deadline)
        header = re.fullmatch(r"frame:(\d+)\s+pts:(\d+)\s+pts_time:(\S+)", line)
        if header:
            if record is not None:
                finish_record()
            if int(header[1]) != count or int(header[2]) > 2**63 - 1:
                raise engine.ToolOutputError
            timestamp = float(header[3])
            if not math.isfinite(timestamp):
                raise engine.ToolOutputError
            record = {"timestamp": timestamp}
            continue
        item = re.fullmatch(r"lavfi\.scd\.(mafd|score|time)=(\S+)", line)
        if not item or record is None or item[1] in record:
            raise engine.ToolOutputError
        value = float(item[2])
        ceiling = duration if item[1] == "time" else 100
        if not math.isfinite(value) or not 0 <= value <= ceiling:
            raise engine.ToolOutputError
        record[item[1]] = value
    finish_record()
    return sorted({t for t in cuts if 0 < t < duration}), count


def pipeline(current_source, directory, stop, deadline):
    directory.mkdir(parents=True, exist_ok=False)
    engine._control.stop = stop
    try:
        color.guard(stop, deadline)
        path = current_source["path"]
        if color.digest(path, stop, deadline) != current_source["identity"].media_sha256:
            raise ReferenceError(409, "source_changed", "The retained reference has changed.")
        ffmpeg, versions, video, duration, metadata = color.inspect_video(path, directory, deadline)
        ratio = min(1, 320 / max(video["width"], video["height"]))
        width, height = [max(2, int(video[key] * ratio) // 2 * 2) for key in ("width", "height")]
        filters = (
            f"setpts=PTS-STARTPTS,scale={width}:{height}:flags=area:in_color_matrix=bt709:"
            f"in_range={'full' if metadata.color_range == 'pc' else 'limited'},"
            "format=yuv420p,metadata=mode=delete,scdet=threshold=10:sc_pass=0,"
            "metadata=mode=print:file=-:direct=1"
        )
        result = engine.run_command(
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
                "-fps_mode",
                "passthrough",
                "-f",
                "null",
                "-",
            ],
            directory,
            "pacing-detector",
            deadline,
            90,
            output_limit=OUTPUT_LIMIT,
            temp_budget=TEMP_BUDGET,
        )
        if result.returncode:
            raise engine.RetrievalFailure("decode_failed", "Consecutive-frame decoding failed.")
        cuts, count = parse_detector(result.stdout, duration, stop, deadline)
        if color.digest(path, stop, deadline) != current_source["identity"].media_sha256:
            raise ReferenceError(409, "source_changed", "Reference bytes changed during analysis.")
        boundaries = [0, *cuts, duration]
        shots = [
            Shot(start_seconds=a, end_seconds=b, duration_seconds=b - a)
            for a, b in zip(boundaries, boundaries[1:])
        ]
        lengths = [shot.duration_seconds for shot in shots]
        return current_source, Blueprint(
            analyzed_at=projects.now(),
            source=current_source["identity"],
            tool_versions=versions,
            duration_seconds=duration,
            video_stream_index=video["index"],
            processing_width=width,
            processing_height=height,
            successful_frames=count,
            method=(
                "Consecutive decoded frames; normalized PTS; area resize, aspect preserved "
                "within even-pixel rounding, no crop; YUV420P; scdet threshold=10 sc_pass=0"
            ),
            candidate_cut_timestamps=cuts,
            estimated_cut_count=len(cuts),
            shot_count=len(shots),
            shots=shots,
            mean_shot_seconds=statistics.mean(lengths),
            median_shot_seconds=statistics.median(lengths),
            cuts_per_minute=len(cuts) * 60 / duration,
            color_metadata=metadata,
            interpretation_limits=LIMITS,
        )
    except engine.ProcessCleanupError:
        raise engine.RetrievalFailure(
            "cleanup_failure",
            "Owned pacing processing is unconfirmed stopped; staging retained.",
            False,
        ) from None
    except engine.ProbeInterrupted:
        raise engine.RetrievalFailure("interrupted", "Pacing analysis was interrupted.") from None
    except engine.SizeLimit:
        raise engine.RetrievalFailure(
            "temporary_size_limit", "Pacing staging exceeded 2 MiB."
        ) from None
    except engine.ToolOutputError:
        raise engine.RetrievalFailure(
            "detector_output_invalid",
            "Detector output is malformed, incomplete or exceeds its 1 MiB cap.",
        ) from None
    except ReferenceError as exc:
        raise engine.RetrievalFailure(exc.code, exc.message) from None
    except (ValueError, TypeError, KeyError, IndexError, UnicodeError):
        raise engine.RetrievalFailure(
            "detector_output_invalid", "Detector output could not be verified."
        ) from None
    finally:
        engine._control.stop = None
