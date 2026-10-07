"""Small, versioned local image measurements; no semantic or quality claims."""

import math
import statistics

from pydantic import Field

import color_analysis as color
import framing
import projects
import reference_engine as engine

ALGORITHM = "rgb-motion-2fps-64-v1"
FPS = 2
MAX_SAMPLES = 2640  # ten 120-second sources plus one reference
TEMP_BUDGET = 8 * 1024 * 1024


class Sample(color.Schema):
    frame: int = Field(ge=0, le=3600)
    brightness: float = Field(ge=0, le=1, allow_inf_nan=False)
    motion: float = Field(ge=0, le=1, allow_inf_nan=False)
    sharpness: float = Field(ge=0, le=1, allow_inf_nan=False)


class Analysis(color.Schema):
    algorithm_version: str = ALGORITHM
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    duration_frames: int = Field(ge=1, le=3600)
    width: int = Field(ge=2, le=64)
    height: int = Field(ge=2, le=64)
    samples: list[Sample] = Field(min_length=1, max_length=240)
    boundaries: list[int] = Field(max_length=239)
    ffmpeg_version: str
    warnings: list[str]


def measure(raw, width, height, stop, deadline):
    size = width * height * 3
    if not raw or len(raw) % size or len(raw) // size > 240:
        raise engine.RetrievalFailure("decode_failed", "Complete sampled frames are required.")
    samples, boundaries, previous = [], [], None
    for index in range(len(raw) // size):
        color.guard(stop, deadline)
        frame = raw[index * size : (index + 1) * size]
        luma = [
            (frame[i] * 0.2126 + frame[i + 1] * 0.7152 + frame[i + 2] * 0.0722) / 255
            for i in range(0, size, 3)
        ]
        motion = (
            statistics.fmean(abs(a - b) for a, b in zip(frame, previous)) / 255 if previous else 0
        )
        sharpness = (
            statistics.fmean(
                abs(
                    4 * luma[y * width + x]
                    - luma[y * width + x - 1]
                    - luma[y * width + x + 1]
                    - luma[(y - 1) * width + x]
                    - luma[(y + 1) * width + x]
                )
                / 4
                for y in range(1, height - 1)
                for x in range(1, width - 1)
            )
            if min(width, height) > 2
            else 0
        )
        samples.append(
            Sample(
                frame=index * 15,
                brightness=statistics.fmean(luma),
                motion=motion,
                sharpness=sharpness,
            )
        )
        if previous and motion >= 0.20:
            boundaries.append(index * 15)
        previous = frame
    return samples, boundaries


def analyze(project_id, path, digest, directory, stop, deadline):
    color.guard(stop, deadline)
    with projects.database() as db:
        cached = db.execute(
            "SELECT analysis FROM candidate_cache WHERE project_id=? AND "
            "source_hash=? AND algorithm=?",
            (project_id, digest, ALGORITHM),
        ).fetchone()
    if cached:
        result = Analysis.model_validate_json(cached[0])
        if result.source_hash != digest or result.algorithm_version != ALGORITHM:
            raise engine.RetrievalFailure("cache_invalid", "Candidate cache is incompatible.")
        return result, True
    ffmpeg, versions, video, duration, metadata = color.inspect_video(path, directory, deadline)
    _, canvas, _ = framing.geometry(video, framing.Settings())
    ratio = min(1, 64 / max(canvas))
    width, height = [max(2, int(n * ratio) // 2 * 2) for n in canvas]
    expected = min(240, math.ceil(duration * FPS))
    result = engine.run_command(
        [
            ffmpeg,
            "-v",
            "error",
            "-nostdin",
            "-xerror",
            "-abort_on",
            "empty_output",
            "-threads",
            "1",
            "-filter_threads",
            "1",
            "-protocol_whitelist",
            "file",
            "-i",
            str(path),
            "-map",
            f"0:{video['index']}",
            "-an",
            "-sn",
            "-dn",
            "-vf",
            f"setpts=PTS-STARTPTS,fps=2:start_time=0:round=near:eof_action=pass,"
            f"scale={width}:{height}:flags=area,setsar=1,format=rgb24",
            "-t",
            str(duration),
            "-f",
            "rawvideo",
            "-",
        ],
        directory,
        "candidate-decode",
        deadline,
        60,
        output_limit=240 * width * height * 3,
        temp_budget=TEMP_BUDGET,
    )
    if result.returncode:
        raise engine.RetrievalFailure("decode_failed", "Candidate footage could not be decoded.")
    samples, boundaries = measure(result.stdout, width, height, stop, deadline)
    if len(samples) < max(1, expected - 1):
        raise engine.RetrievalFailure("decode_failed", "Sampled decoding ended unexpectedly early.")
    analysis = Analysis(
        source_hash=digest,
        duration_frames=max(1, math.floor(duration * 30)),
        width=width,
        height=height,
        samples=samples,
        boundaries=boundaries,
        ffmpeg_version=versions["ffmpeg"],
        warnings=metadata.warnings,
    )
    color.guard(stop, deadline)
    with projects.database() as db:
        projects.row_project(db, project_id)
        db.execute(
            "INSERT OR REPLACE INTO candidate_cache VALUES(?,?,?,?)",
            (project_id, digest, ALGORITHM, analysis.model_dump_json()),
        )
    return analysis, False
