"""Bounded silent MP4 using the same full-output video graph and caption renderer."""

import base64
import math
import shutil
from decimal import ROUND_CEILING
from typing import Literal

from pydantic import Field

import caption_preview
import color_analysis as color
import edit_plan
import footage_analysis as footage
import frame_preview as preview
import framing
import video_render as render
from references import ReferenceError

MAX_BYTES = 4 * 1024 * 1024
MAX_FRAMES = 120


class Motion(color.Schema):
    schema_version: Literal[1] = 1
    source: footage.Source
    spec: render.Spec
    cue_index: int = Field(ge=0, le=199)
    start_frame: int = Field(ge=0)
    end_frame: int = Field(gt=0)
    width: int = Field(ge=2, le=640)
    height: int = Field(ge=2, le=640)
    duration_seconds: float = Field(gt=0, le=4, allow_inf_nan=False)
    decoded_frames: int = Field(ge=1, le=MAX_FRAMES)
    size_bytes: int = Field(gt=0, le=MAX_BYTES)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    video_base64: str = Field(max_length=5592408)
    silent: Literal[True] = True
    warnings: list[str]
    ffmpeg_version: str


def interval(cue, total_frames):
    # ponytail: a long cue shows its first four seconds, not a second preview framework.
    # Keep full-output PTS through ASS; rebasing afterwards preserves animation phase.
    caption_preview.output_frame(cue)  # rejects a cue falling entirely between frames
    first = edit_plan.frames(cue.start, ROUND_CEILING)
    last = edit_plan.frames(cue.end, ROUND_CEILING)
    start = max(0, first - 6)
    end = min(total_frames, last + 6, start + MAX_FRAMES)
    if end <= start:
        raise ReferenceError(422, "invalid_cue", "Cue is outside the decoded output timeline.")
    return start, end


def generate(project_id, request):
    with preview.operation() as (directory, deadline):
        spec = caption_preview.snapshot(project_id, request, deadline)
        source = footage.source(project_id, deadline=deadline)
        video, _, duration = render.probe(
            source["path"], directory, deadline, temp_budget=preview.TEMP_BUDGET
        )
        total = (
            spec.sequence.output_frames
            if spec.sequence
            else spec.edit_plan.output_frames
            if spec.edit_plan
            else edit_plan.frames(duration, ROUND_CEILING)
        )
        if spec.edit_plan and spec.edit_plan.footage_frames > edit_plan.frames(
            duration, edit_plan.ROUND_FLOOR
        ):
            raise ReferenceError(409, "source_changed", "Cut plan exceeds current footage.")
        cue = spec.captions.cues[request.cue_index]
        start, end = interval(cue, total)
        path = source["path"]
        if spec.sequence:
            path = render.assemble_sequence(
                {"project_id": project_id, "path": path, "spec": spec},
                directory,
                None,
                deadline,
                shutil.which("ffmpeg") or "ffmpeg",
                start_frame=start,
                end_frame=end,
                include_audio=False,
                temp_budget=preview.TEMP_BUDGET,
            )
            video, _, _ = render.probe(path, directory, deadline, temp_budget=preview.TEMP_BUDGET)
        metadata = color.inspect_colors(video)
        scale, canvas, final = framing.geometry(video, spec.framing)
        resize = render.resize_filter(*scale, metadata)
        if spec.sequence:
            canvas = (video["width"], video["height"])
            final = ""
            graph = f"[0:{video['index']}]setpts=PTS-STARTPTS+{start}/30/TB[vout]"
        elif spec.edit_plan:
            graph = render.cut_graph(
                spec, video, None, 0, resize, grade_filter=render.grading_filter(spec, directory)
            )
        else:
            grade_filter = render.grading_filter(spec, directory)
            graph = (
                f"[0:{video['index']}]"
                f"{render.whole_video_filter(resize, spec.effective, grade_filter)}[vout]"
            )
        subtitle, warnings = render.subtitle(
            spec.captions, project_id, directory, *canvas, deadline, temp_budget=preview.TEMP_BUDGET
        )
        factor = min(1, 640 / max(canvas))
        width, height = (max(2, math.floor(n * factor / 2) * 2) for n in canvas)
        chain = ",".join(
            part
            for part in (
                f"trim=start_frame={0 if spec.sequence else start}:"
                f"end_frame={end - start if spec.sequence else end}",
                final,
                subtitle,
                "setpts=PTS-STARTPTS",
                f"scale={width}:{height}:flags=area",
                "format=yuv420p",
            )
            if part
        )
        graph += f";[vout]{chain}[motion]"
        target = directory / "motion.mp4"
        preview.tool(
            [
                shutil.which("ffmpeg") or "ffmpeg",
                "-v",
                "error",
                "-nostdin",
                "-xerror",
                "-protocol_whitelist",
                "file",
                "-copyts",
                "-i",
                str(path),
                "-filter_complex",
                graph,
                "-map",
                "[motion]",
                "-an",
                "-sn",
                "-dn",
                "-frames:v",
                str(end - start),
                "-map_metadata",
                "-1",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "23",
                "-pix_fmt",
                "yuv420p",
                "-color_primaries",
                "bt709",
                "-color_trc",
                "bt709",
                "-colorspace",
                "bt709",
                "-color_range",
                "tv",
                "-movflags",
                "+faststart",
                str(target),
            ],
            directory,
            "caption-motion-encode",
            deadline,
        )
        size = target.stat().st_size
        if not 0 < size <= MAX_BYTES:
            raise ReferenceError(413, "motion_limit", "Motion preview exceeded its four-MiB limit.")
        encoded_video, audio, actual_duration = render.probe(
            target, directory, deadline, temp_budget=preview.TEMP_BUDGET
        )
        if (
            audio
            or (encoded_video["width"], encoded_video["height"]) != (width, height)
            or abs(actual_duration - (end - start) / 30) > 0.05
            or actual_duration > 4
        ):
            raise ValueError("Invalid motion preview dimensions/duration/audio")
        raw = preview.tool(
            [
                shutil.which("ffmpeg") or "ffmpeg",
                "-v",
                "error",
                "-nostdin",
                "-xerror",
                "-protocol_whitelist",
                "file",
                "-i",
                str(target),
                "-map",
                "0:v:0",
                "-f",
                "framehash",
                "-hash",
                "sha256",
                "-",
            ],
            directory,
            "caption-motion-decode",
            deadline,
        )
        frames, samples = render.decode_counts(raw, False)
        if frames != end - start or samples:
            raise ValueError("Incomplete motion preview decoding")
        if (
            caption_preview.snapshot(project_id, request, deadline) != spec
            or footage.source(project_id, deadline=deadline) != source
        ):
            raise ReferenceError(409, "source_changed", "Motion preview sources/settings changed.")
        digest = color.digest(target, deadline=deadline, max_bytes=MAX_BYTES)
        result = Motion(
            source=source["identity"],
            spec=spec,
            cue_index=request.cue_index,
            start_frame=start,
            end_frame=end,
            width=width,
            height=height,
            duration_seconds=actual_duration,
            decoded_frames=frames,
            size_bytes=size,
            sha256=digest,
            video_base64=base64.b64encode(target.read_bytes()).decode(),
            warnings=metadata.warnings + warnings,
            ffmpeg_version=caption_preview.version(directory, deadline),
        )
        color.guard(None, deadline)
        return result
