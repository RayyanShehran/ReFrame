"""Revision-bound, color-only local renders using the existing owned media worker."""

import json
import math
import re
import shutil
import uuid
from fractions import Fraction
from functools import partial
from typing import Literal

from pydantic import Field
from starlette.responses import FileResponse

import audio_settings as audio_choices
import captions as caption_tracks
import color_analysis as color
import color_recipe as recipe
import edit_plan as cuts
import footage_analysis as footage
import projects
import reference_engine as engine
from references import ReferenceError

VERSION = "sdr-eq-mp4-v2"
CUTS_VERSION = "sdr-eq-mp4-v3"
AUDIO_VERSION = "sdr-eq-mp4-v4"
CAPTION_VERSION = "sdr-eq-mp4-v5"
TOTAL_SECONDS = 300
MAX_BYTES = 100 * 1024 * 1024
TEMP_BUDGET = 120 * 1024 * 1024
TABLE = "render_operations"
STAGING = "render-staging"
staging = partial(color.staging, component=__import__(__name__))
clean_stage = partial(color.clean_stage, component=__import__(__name__))


class Settings(color.Schema):
    container: Literal["mp4"] = "mp4"
    video_codec: Literal["libx264"] = "libx264"
    pixel_format: Literal["yuv420p"] = "yuv420p"
    fps: Literal[30] = 30
    crf: Literal[20] = 20
    preset: Literal["veryfast"] = "veryfast"
    audio_codec: Literal["aac"] = "aac"
    audio_rate: Literal[48000] = 48000
    audio_bitrate: Literal["128k"] = "128k"
    landscape: tuple[Literal[1280], Literal[720]] = (1280, 720)
    portrait: tuple[Literal[720], Literal[1280]] = (720, 1280)
    faststart: Literal[True] = True


class Spec(color.Schema):
    schema_version: Literal[1] = 1
    renderer_version: str = Field(default=VERSION, pattern=r"^sdr-eq-mp4-v[1-9][0-9]*$")
    recipe_revision: int = Field(ge=1, strict=True)
    reference: recipe.ReferenceBinding
    footage: recipe.FootageBinding
    effective: recipe.Values
    settings: Settings = Field(default_factory=Settings)
    edit_plan: cuts.Plan | None = None
    audio: audio_choices.Settings = Field(default_factory=audio_choices.Settings)
    captions: caption_tracks.Track = Field(default_factory=caption_tracks.Track)


class Output(color.Schema):
    output_id: uuid.UUID
    spec: Spec
    size_bytes: int = Field(gt=0, le=MAX_BYTES)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    width: int = Field(ge=2, le=1280)
    height: int = Field(ge=2, le=1280)
    duration_seconds: float = Field(gt=0, le=120.1, allow_inf_nan=False)
    has_audio: bool
    decoded_frames: int = Field(gt=0, le=3603)
    decoded_audio_samples: int = Field(ge=0, le=5764800)
    ffmpeg_version: str
    created_at: str


class Operation(color.Schema):
    operation_id: str | None = None
    status: Literal["idle", "running", "ready", "failed"] = "idle"
    spec: Spec | None = None
    output: Output | None = None
    outdated: bool = False
    started_at: str | None = None
    finished_at: str | None = None
    failure_code: str | None = None
    message: str | None = None


class RenderRequest(color.Schema):
    expected_revision: int = Field(ge=1, strict=True)
    expected_plan_revision: int | None = Field(default=None, ge=1, strict=True)
    expected_audio_revision: int | None = Field(default=None, ge=0, strict=True)
    expected_caption_revision: int | None = Field(default=None, ge=0, strict=True)


def destination(project_id, output_id):
    return (
        projects.DATA_DIR
        / "projects"
        / projects.identifier(project_id)
        / f"render-{uuid.UUID(projects.identifier(str(output_id))).hex}.mp4"
    )


def specification(
    project_id,
    expected_revision,
    expected_plan_revision=None,
    expected_audio_revision=None,
    expected_caption_revision=None,
):
    result = recipe.read(project_id)
    if result.status != "ready":
        raise ReferenceError(409, "recipe_stale", "Save a valid color recipe before rendering.")
    if result.recipe.revision != expected_revision:
        raise ReferenceError(409, "revision_conflict", "Recipe changed. Reload before rendering.")
    plan = None
    if expected_plan_revision is not None:
        result_plan = cuts.read(project_id)
        if result_plan.status != "ready":
            raise ReferenceError(409, "plan_stale", "Save a valid cut plan before rendering cuts.")
        if result_plan.plan.revision != expected_plan_revision:
            raise ReferenceError(
                409, "revision_conflict", "Cut plan changed. Reload before rendering."
            )
        plan = result_plan.plan
    audio = audio_choices.read(project_id)
    if audio.status == "stale":
        raise ReferenceError(409, "audio_stale", "Save valid audio choices before rendering.")
    if expected_audio_revision is None:
        # Old clients are compatible only while the default original mode is unchanged.
        if audio.settings.revision:
            raise ReferenceError(
                409, "revision_conflict", "Include the saved audio revision before rendering."
            )
    elif expected_audio_revision != audio.settings.revision:
        raise ReferenceError(409, "revision_conflict", "Audio changed. Reload before rendering.")
    caption = caption_tracks.read(project_id)
    if expected_caption_revision is None:
        if caption.track.revision:
            raise ReferenceError(409, "revision_conflict", "Include the saved caption revision.")
    elif expected_caption_revision != caption.track.revision:
        raise ReferenceError(409, "revision_conflict", "Captions changed. Reload before rendering.")
    if caption.track.enabled:
        selected = caption_tracks.timeline(
            project_id, "cuts" if plan else "whole", expected_plan_revision
        )
        if caption.status != "ready" or caption.track.timeline != selected:
            raise ReferenceError(
                409,
                "captions_stale",
                "Rebind and save captions for this output timeline before rendering.",
            )
    return Spec(
        renderer_version=CAPTION_VERSION
        if caption.track.revision
        else AUDIO_VERSION
        if audio.settings.revision
        else CUTS_VERSION
        if plan
        else VERSION,
        audio=audio.settings,
        captions=caption.track,
        edit_plan=plan,
        recipe_revision=result.recipe.revision,
        reference=result.recipe.reference,
        footage=result.recipe.footage,
        effective=result.effective,
    )


def get_operation(project_id, require_active=False):
    projects.identifier(project_id)
    with projects.database() as connection:
        project = projects.row_project(connection, project_id)
        if project["status"] != "active":
            raise ReferenceError(409, "project_deleting", "Retry project deletion first.")
        row = connection.execute(
            "SELECT * FROM render_operations WHERE project_id=?", (project_id,)
        ).fetchone()
        completed = connection.execute(
            "SELECT * FROM render_outputs WHERE project_id=?", (project_id,)
        ).fetchone()
    output = Output.model_validate_json(completed["metadata"]) if completed else None
    if output:
        path = destination(project_id, output.output_id)
        try:
            valid = (
                path.is_file()
                and path.stat().st_size == output.size_bytes
                and color.digest(path, max_bytes=MAX_BYTES) == output.sha256
            )
        except ReferenceError as exc:
            if exc.code == "deadline":
                raise
            valid = False
        if not valid:
            path.unlink(missing_ok=True)
            with projects.database() as connection:
                connection.execute("DELETE FROM render_outputs WHERE project_id=?", (project_id,))
                if row and row["state"] != "running":
                    connection.execute(
                        "UPDATE render_operations SET state='failed',"
                        "failure_code='output_unavailable',message=? WHERE project_id=?",
                        (
                            "The saved render is missing or changed. Retry rendering explicitly.",
                            project_id,
                        ),
                    )
            return get_operation(project_id, require_active)
    spec = Spec.model_validate_json(row["spec"]) if row else None
    outdated = False
    if spec or output:
        try:
            bound = output.spec if output else spec
            current_plan = cuts.read(project_id) if bound.edit_plan else None
            current = recipe.read(project_id)
            current_audio = audio_choices.read(project_id)
            current_captions = caption_tracks.read(project_id)
            outdated = (
                current.status != "ready"
                or (current_plan is not None and current_plan.status != "ready")
                or current_audio.status == "stale"
                or specification(
                    project_id,
                    current.recipe.revision,
                    current_plan.plan.revision if current_plan and current_plan.plan else None,
                    current_audio.settings.revision,
                    current_captions.track.revision,
                )
                != bound
            )
        except ReferenceError:
            outdated = True
    return Operation(
        operation_id=row["operation_id"] if row else None,
        status=row["state"] if row else "idle",
        spec=spec,
        output=output,
        outdated=outdated,
        started_at=row["started_at"] if row else None,
        finished_at=row["finished_at"] if row else None,
        failure_code=row["failure_code"] if row else None,
        message=row["message"] if row else None,
    )


def reusable(
    project_id,
    expected_revision,
    expected_plan_revision=None,
    expected_audio_revision=None,
    expected_caption_revision=None,
):
    spec = specification(
        project_id,
        expected_revision,
        expected_plan_revision,
        expected_audio_revision,
        expected_caption_revision,
    )
    current = get_operation(project_id, True)
    return current if current.status in {"running", "ready"} and current.spec == spec else None


def served_output(project_id, output_id):
    projects.identifier(output_id)
    operation = get_operation(project_id, True)
    output = operation.output
    if not output or str(output.output_id) != output_id:
        raise ReferenceError(404, "output_not_found", "This completed render is unavailable.")
    path = destination(project_id, output_id)
    return path, output, operation.outdated


class VideoResponse(FileResponse):
    """Serialize deletion/replacement until the framework finishes reading the file."""

    def __init__(self, project_id, output_id, download=False):
        self.project_id, self.output_id = project_id, output_id
        super().__init__(
            destination(project_id, output_id),
            media_type="video/mp4",
            filename=f"reframe-{output_id}.mp4",
            content_disposition_type="attachment" if download else "inline",
        )

    async def __call__(self, scope, receive, send):
        async with projects.operation_lock:
            _, output, outdated = await projects.storage_call(
                served_output, self.project_id, self.output_id
            )
            self.headers["X-Recipe-Revision"] = str(output.spec.recipe_revision)
            self.headers["X-Edit-Plan-Revision"] = (
                str(output.spec.edit_plan.revision) if output.spec.edit_plan else "none"
            )
            self.headers["X-Render-Outdated"] = str(outdated).lower()
            self.headers["X-Audio-Revision"] = str(output.spec.audio.revision)
            self.headers["X-Audio-Mode"] = output.spec.audio.mode
            self.headers["X-Caption-Revision"] = str(output.spec.captions.revision)
            self.headers["Cache-Control"] = "no-store"
            await super().__call__(scope, receive, send)


def begin_operation(
    project_id,
    expected_revision,
    expected_plan_revision=None,
    expected_audio_revision=None,
    expected_caption_revision=None,
):
    current = reusable(
        project_id,
        expected_revision,
        expected_plan_revision,
        expected_audio_revision,
        expected_caption_revision,
    )
    if current:
        return current, None
    prepare_delete(project_id)
    spec = specification(
        project_id,
        expected_revision,
        expected_plan_revision,
        expected_audio_revision,
        expected_caption_revision,
    )
    source = footage.source(project_id)
    operation_id = str(uuid.uuid4())
    with projects.database() as connection:
        connection.execute("DELETE FROM render_operations WHERE project_id=?", (project_id,))
        connection.execute(
            "INSERT INTO render_operations(project_id,operation_id,state,started_at,spec) "
            "VALUES (?,?,'running',?,?)",
            (project_id, operation_id, projects.now(), spec.model_dump_json()),
        )
    return get_operation(project_id), {
        "path": source["path"],
        "spec": spec,
        "project_id": project_id,
    }


def fail_operation(project_id, operation_id, failure):
    with projects.database() as connection:
        connection.execute(
            "UPDATE render_operations SET state='failed',finished_at=?,failure_code=?,message=?,"
            "cleanup_safe=? WHERE project_id=? AND operation_id=?",
            (
                projects.now(),
                failure.code,
                failure.message,
                int(failure.cleanup_safe),
                project_id,
                operation_id,
            ),
        )


def tool(args, directory, label, deadline, seconds=300, limit=65536):
    result = engine.run_command(
        args, directory, label, deadline, seconds, output_limit=limit, temp_budget=TEMP_BUDGET
    )
    if result.returncode:
        raise engine.RetrievalFailure(
            "render_failed", "Video processing failed; raw tool output withheld."
        )
    return result.stdout


def probe(path, directory, deadline):
    raw = tool(
        [
            shutil.which("ffprobe") or "ffprobe",
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
        "render-probe",
        deadline,
        10,
    )
    data = json.loads(raw)
    video = next(
        s
        for s in data["streams"]
        if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")
    )
    audio = next((s for s in data["streams"] if s.get("codec_type") == "audio"), None)
    duration = float(video.get("duration", data["format"]["duration"]))
    if not math.isfinite(duration) or not 0 < duration <= 120.1:
        raise ValueError("Invalid duration")
    for field in ("width", "height", "index"):
        if type(video[field]) is not int or not 0 <= video[field] <= 4096:
            raise ValueError("Invalid video metadata")
    if audio and (type(audio["index"]) is not int or not 0 <= audio["index"] <= 4096):
        raise ValueError("Invalid audio metadata")
    return video, audio, duration


def dimensions(video):
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
    maximum = (720, 1280) if height > width else (1280, 720)
    factor = min(1, maximum[0] / width, maximum[1] / height)
    result = (int(width * factor) // 2 * 2, int(height * factor) // 2 * 2)
    if min(result) < 2:
        raise ValueError("Video dimensions are too small")
    return result


def decode_counts(raw, has_audio):
    frames = samples = 0
    for line in raw.decode("ascii").splitlines():
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 6 or not re.fullmatch(r"[0-9a-f]{64}", parts[5]):
            raise ValueError("Malformed decoded frame hashes")
        stream, dts, pts, duration, size = map(int, parts[:5])
        if duration <= 0 or size <= 0 or stream not in ({0, 1} if has_audio else {0}):
            raise ValueError("Empty decoded packet")
        if stream == 0:
            frames += 1
        else:
            samples += duration
    if not frames or (has_audio and not samples):
        raise ValueError("Missing decoded frames or audio samples")
    return frames, samples


def cut_graph(spec, video, audio, video_start, resize, audio_label="aout"):
    """Chronological select concatenates video without buffering sixty video branches."""
    plan = spec.edit_plan
    selection = "+".join(
        f"gte(n,{s.source_start_frame})*lt(n,{s.source_end_frame})" for s in plan.segments
    )
    v = spec.effective
    graph = (
        f"[0:{video['index']}]setpts=PTS-STARTPTS,{resize},fps=30:round=near,"
        f"select='{selection}',setpts=N/(30*TB),"
        f"eq=brightness={v.brightness}:contrast={v.contrast}:saturation={v.saturation}[vout]"
    )
    if audio:
        count = len(plan.segments)
        graph += (
            f";[0:{audio['index']}]asetpts=PTS-{video_start}/TB,"
            "aresample=48000:async=1:first_pts=0,"
            f"apad=whole_len={plan.footage_frames * 1600},"
            f"atrim=end_sample={plan.footage_frames * 1600},"
            f"asplit={count}" + "".join(f"[a{i}]" for i in range(count))
        )
        for i, segment in enumerate(plan.segments):
            graph += (
                f";[a{i}]atrim=start_sample={segment.source_start_frame * 1600}:"
                f"end_sample={segment.source_end_frame * 1600},asetpts=PTS-STARTPTS[s{i}]"
            )
        graph += ";" + "".join(f"[s{i}]" for i in range(count))
        graph += f"concat=n={count}:v=0:a=1[{audio_label}]"
    if len(graph.encode("utf-8")) > 16 * 1024:
        raise engine.RetrievalFailure("graph_limit", "Cut filter graph exceeds its 16 KiB limit.")
    return graph


def audio_graph(spec, original, video_start, reference, reference_start, duration):
    """Reference is continuous output-time audio; only original follows footage cuts."""
    settings = spec.audio
    graph = []
    total = round(duration * 48000)
    if settings.mode in {"original", "mix"} and original:
        if spec.edit_plan:
            prefix = "[aorig]"
        else:
            prefix = f"[0:{original['index']}]asetpts=PTS-{video_start}/TB,atrim=start=0,"
        chain = f"volume={settings.original_volume / 100}"
        if settings.mode == "mix":
            chain = (
                "aresample=48000:async=1:first_pts=0,"
                "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
                f"apad=whole_len={total},atrim=end_sample={total}," + chain
            )
        graph.append(prefix + chain + ("[original]" if settings.mode == "mix" else "[aout]"))
    if settings.mode in {"reference", "mix"}:
        offset = round(settings.reference_offset_seconds * 48000)
        graph.append(
            f"[1:{reference['index']}]asetpts=PTS-{reference_start}/TB,"
            "aresample=48000:async=1:first_pts=0,"
            "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
            f"apad=whole_len={offset + total},atrim=start_sample={offset}:"
            f"end_sample={offset + total},"
            f"asetpts=PTS-STARTPTS,volume={settings.reference_volume / 100}"
            + ("[reference]" if settings.mode == "mix" else "[aout]")
        )
    if settings.mode == "mix":
        graph.append(
            "[original][reference]amix=inputs=2:duration=longest:normalize=0:dropout_transition=0,"
            "alimiter=limit=0.95:attack=5:release=50:level=0:latency=1,"
            f"atrim=end_sample={total},asetpts=PTS-STARTPTS[aout]"
        )
    return ";".join(graph)


def pipeline(source, directory, stop, deadline):
    directory.mkdir(parents=True, exist_ok=False)
    engine._control.stop = stop
    try:
        spec, path = source["spec"], source["path"]
        if footage.digest(path, stop, deadline) != spec.footage.source.media_sha256:
            raise engine.RetrievalFailure("source_changed", "Footage changed before rendering.")
        ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
        version = (
            tool([ffmpeg, "-version"], directory, "render-version", deadline, 5, 8192)
            .decode("utf-8")
            .splitlines()[0]
        )
        video, audio, duration = probe(path, directory, deadline)
        video_start = float(video.get("start_time", 0))
        if not math.isfinite(video_start):
            raise ValueError("Invalid video start timestamp")
        metadata = color.inspect_colors(video)
        width, height = dimensions(video)
        subtitle_filter = ""
        if spec.captions.enabled:
            capability = tool(
                [ffmpeg, "-hide_banner", "-h", "filter=ass"],
                directory,
                "render-caption-capability",
                deadline,
                5,
                8192,
            )
            if not re.search(rb"(?m)^Filter ass\s*$", capability):
                raise engine.RetrievalFailure(
                    "captions_unavailable",
                    "Install FFmpeg with the libass ASS filter to render captions.",
                )
            subtitle_filter = caption_tracks.subtitle_filter(
                spec.captions, directory, width, height
            )
        values = spec.effective
        # Normalize range before eq; output is tagged limited BT.709. Autorotation is enabled.
        resize = (
            f"scale={width}:{height}:flags=area:"
            f"in_range={'full' if metadata.color_range == 'pc' else 'limited'}:"
            "out_range=limited:in_color_matrix=bt709:out_color_matrix=bt709,"
            "setsar=1,format=yuv420p"
        )
        filters = (
            f"setpts=PTS-STARTPTS,{resize},eq=brightness={values.brightness}:"
            f"contrast={values.contrast}:saturation={values.saturation},fps=30:round=near"
        )
        target = directory / "output.mp4"
        args = [
            ffmpeg,
            "-v",
            "error",
            "-nostdin",
            "-xerror",
            "-protocol_whitelist",
            "file",
            "-copyts",
            "-i",
            str(path),
        ]
        reference_audio, reference_start = None, 0
        settings = spec.audio
        if settings.mode in {"reference", "mix"}:
            reference_path, binding, _ = audio_choices.reference_source(
                source["project_id"], stop, deadline
            )
            if binding != settings.reference:
                raise engine.RetrievalFailure(
                    "source_changed", "Reference audio changed before rendering."
                )
            reference_video, reference_audio, reference_duration = probe(
                reference_path, directory, deadline
            )
            reference_start = float(reference_video.get("start_time", 0))
            if not reference_audio or not math.isfinite(reference_start):
                raise ValueError("Reference audio is unavailable")
            if settings.reference_offset_seconds >= reference_duration:
                raise ValueError("Reference audio offset exceeds the video timeline")
            args += ["-i", str(reference_path)]
        if settings.mode == "mix" and not audio:
            raise ValueError("Mix requires original audio")
        has_audio = settings.mode in {"reference", "mix"} or (
            settings.mode == "original" and bool(audio)
        )
        custom_audio = settings.revision > 0
        graph = ""
        if spec.edit_plan:
            if spec.edit_plan.footage_frames > cuts.frames(duration, cuts.ROUND_FLOOR):
                raise ValueError("Cut plan exceeds decoded source duration")
            duration = spec.edit_plan.output_frames / 30
            graph = cut_graph(
                spec,
                video,
                audio if settings.mode in {"original", "mix"} else None,
                video_start,
                resize,
                "aorig" if custom_audio else "aout",
            )
            if subtitle_filter:
                graph += f";[vout]{subtitle_filter}[vcaption]"
            args += ["-map", "[vcaption]" if subtitle_filter else "[vout]"]
        else:
            args += [
                "-map",
                f"0:{video['index']}",
                "-vf",
                filters + ("," + subtitle_filter if subtitle_filter else ""),
            ]
        if custom_audio:
            extra = audio_graph(
                spec, audio, video_start, reference_audio, reference_start, duration
            )
            if extra:
                graph += (";" if graph else "") + extra
        if graph:
            if len(graph.encode("utf-8")) > 16 * 1024:
                raise engine.RetrievalFailure("graph_limit", "Render filter graph exceeds 16 KiB.")
            args += ["-filter_complex", graph]
        args += [
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-color_primaries",
            "bt709",
            "-color_trc",
            metadata.color_transfer or "bt709",
            "-colorspace",
            "bt709",
            "-color_range",
            "tv",
        ]
        if has_audio:
            args += (
                ["-map", "[aout]"]
                if spec.edit_plan or custom_audio
                else [
                    "-map",
                    f"0:{audio['index']}",
                    "-af",
                    f"asetpts=PTS-{video_start}/TB,atrim=start=0",
                ]
            )
            args += [
                "-c:a",
                "aac",
                "-ar",
                "48000",
                "-b:a",
                "128k",
            ]
        else:
            args += ["-an"]
        args += [
            "-map_metadata",
            "-1",
            "-metadata:s:v:0",
            "rotate=0",
            "-t",
            str(duration),
            "-movflags",
            "+faststart",
            "-fs",
            str(MAX_BYTES + 1),
            str(target),
        ]
        tool(args, directory, "render-encode", deadline)
        if not 0 < target.stat().st_size <= MAX_BYTES:
            raise engine.RetrievalFailure(
                "output_limit", "Rendered video exceeds the 100 MiB output limit."
            )
        rendered, rendered_audio, rendered_duration = probe(target, directory, deadline)
        if (
            rendered.get("codec_name") != "h264"
            or rendered.get("pix_fmt") != "yuv420p"
            or (rendered["width"], rendered["height"]) != (width, height)
            or rendered.get("sample_aspect_ratio") != "1:1"
            or Fraction(rendered["avg_frame_rate"]) != 30
            or bool(rendered_audio) != has_audio
            or (rendered_audio and rendered_audio.get("codec_name") != "aac")
            or abs(rendered_duration - duration) > 2 / 30
        ):
            raise ValueError("Output stream verification failed")
        raw = tool(
            [
                ffmpeg,
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
                "-map",
                "0:a:0?",
                "-c:v",
                "rawvideo",
                "-c:a",
                "pcm_s16le",
                "-f",
                "framehash",
                "-hash",
                "sha256",
                "-",
            ],
            directory,
            "render-decode",
            deadline,
            limit=2 * 1024 * 1024,
        )
        frames, samples = decode_counts(raw, has_audio)
        if spec.edit_plan and frames != spec.edit_plan.output_frames:
            raise ValueError("Incomplete cut frames")
        if abs(frames / 30 - rendered_duration) > 1 / 30:
            raise ValueError("Incomplete decoded video")
        if rendered_audio:
            audio_duration = float(rendered_audio["duration"])
            if (
                not math.isfinite(audio_duration)
                or not 0 < audio_duration <= duration + 0.1
                or abs(samples / 48000 - audio_duration) > 2 * 1024 / 48000
                or (
                    settings.mode in {"reference", "mix"}
                    and abs(audio_duration - duration) > 2 * 1024 / 48000
                )
            ):
                raise ValueError("Incomplete decoded audio")
        digest = color.digest(target, stop, deadline, max_bytes=MAX_BYTES)
        return target, Output(
            output_id=directory.name,
            spec=spec,
            size_bytes=target.stat().st_size,
            sha256=digest,
            width=width,
            height=height,
            duration_seconds=rendered_duration,
            has_audio=has_audio,
            decoded_frames=frames,
            decoded_audio_samples=samples,
            ffmpeg_version=version,
            created_at=projects.now(),
        )
    except engine.ProcessCleanupError:
        raise engine.RetrievalFailure(
            "cleanup_failure",
            "Owned rendering processes could not be confirmed stopped; staging retained.",
            cleanup_safe=False,
        ) from None
    except engine.ProbeInterrupted:
        raise engine.RetrievalFailure("interrupted", "Rendering was interrupted.") from None
    except engine.SizeLimit:
        raise engine.RetrievalFailure(
            "staging_limit", "Rendering exceeded the 120 MiB staging watchdog."
        ) from None
    except engine.ToolOutputError:
        raise engine.RetrievalFailure(
            "tool_output_limit", "Rendering tool output exceeded its capture limit."
        ) from None
    except (ValueError, KeyError, StopIteration, TypeError, ZeroDivisionError):
        raise engine.RetrievalFailure(
            "render_invalid", "Rendered media or source metadata could not be verified."
        ) from None
    finally:
        engine._control.stop = None


def commit(project_id, operation_id, path, output, stop, deadline):
    color.guard(stop, deadline)
    # An edit can make this revision outdated; source/analysis bindings must still match.
    _, _, reference, clip = recipe.bindings(project_id, stop=stop, deadline=deadline)
    if reference != output.spec.reference or clip != output.spec.footage:
        raise engine.RetrievalFailure(
            "source_changed", "Render sources changed before publication."
        )
    if output.spec.edit_plan:
        _, binding, source, _ = cuts.bindings(project_id, stop=stop, deadline=deadline)
        if binding != output.spec.edit_plan.pacing or source != output.spec.edit_plan.footage:
            raise engine.RetrievalFailure(
                "source_changed", "Cut plan sources changed before publication."
            )
    if output.spec.audio.reference:
        _, binding, _ = audio_choices.reference_source(project_id, stop, deadline)
        if binding != output.spec.audio.reference:
            raise engine.RetrievalFailure(
                "source_changed", "Reference audio changed before publication."
            )
    target = destination(project_id, output.output_id)
    if output.spec.captions.enabled:
        binding = caption_tracks.timeline(
            project_id, "cuts" if output.spec.edit_plan else "whole", stop=stop, deadline=deadline
        )
        if binding != output.spec.captions.timeline:
            raise engine.RetrievalFailure(
                "source_changed", "Caption timeline changed before publication."
            )
    with projects.database() as connection:
        project = projects.row_project(connection, project_id)
        row = connection.execute(
            "SELECT * FROM render_operations WHERE project_id=?", (project_id,)
        ).fetchone()
        if (
            project["status"] != "active"
            or not row
            or row["state"] != "running"
            or row["operation_id"] != operation_id
            or Spec.model_validate_json(row["spec"]) != output.spec
        ):
            raise engine.RetrievalFailure("interrupted", "Render operation is no longer current.")
        old = connection.execute(
            "SELECT * FROM render_outputs WHERE project_id=?", (project_id,)
        ).fetchone()
        path.replace(target)
        clean_stage(operation_id)
        color.guard(stop, deadline)
        connection.execute(
            "INSERT INTO render_outputs(project_id,output_id,metadata) VALUES (?,?,?) "
            "ON CONFLICT(project_id) DO UPDATE SET output_id=excluded.output_id,"
            "metadata=excluded.metadata",
            (project_id, str(output.output_id), output.model_dump_json()),
        )
        connection.execute(
            "UPDATE render_operations SET state='ready',finished_at=?,failure_code=NULL,"
            "message=NULL WHERE project_id=? AND operation_id=?",
            (projects.now(), project_id, operation_id),
        )
        connection.execute(
            "UPDATE projects SET updated_at=? WHERE id=?", (projects.now(), project_id)
        )
    if old:
        try:
            destination(project_id, old["output_id"]).unlink(missing_ok=True)
        except OSError:
            # The project lock hides publication until replacement cleanup completes.
            # Preserve the previous completed output when its removal fails.
            with projects.database() as connection:
                connection.execute(
                    "UPDATE render_outputs SET output_id=?,metadata=? WHERE project_id=?",
                    (old["output_id"], old["metadata"], project_id),
                )
                connection.execute(
                    "UPDATE render_operations SET state='failed',"
                    "failure_code='cleanup_failure',message=? "
                    "WHERE project_id=? AND operation_id=?",
                    (
                        "Previous render cleanup failed; replacement was not published.",
                        project_id,
                        operation_id,
                    ),
                )
            raise ReferenceError(
                500,
                "cleanup_failure",
                "Previous render could not be removed; replacement was not published.",
            ) from None


def compensate(project_id, operation_id):
    with projects.database() as connection:
        row = connection.execute(
            "SELECT output_id FROM render_outputs WHERE project_id=?", (project_id,)
        ).fetchone()
    if not row or row[0] != operation_id:
        destination(project_id, operation_id).unlink(missing_ok=True)


def prepare_delete(project_id):
    with projects.database() as connection:
        row = connection.execute(
            "SELECT * FROM render_operations WHERE project_id=?", (project_id,)
        ).fetchone()
    if row:
        if not row["cleanup_safe"]:
            raise ReferenceError(
                500,
                "cleanup_failure",
                "Unconfirmed rendering processes require manual review; files retained.",
            )
        clean_stage(row["operation_id"])
        compensate(project_id, row["operation_id"])


def recover():
    root = projects.DATA_DIR / STAGING
    root.mkdir(exist_ok=True)
    with projects.database() as connection:
        rows = connection.execute("SELECT * FROM render_operations").fetchall()
    for row in rows:
        if row["state"] == "running":
            fail_operation(
                row["project_id"],
                row["operation_id"],
                engine.RetrievalFailure(
                    "interrupted", "Rendering was interrupted. Retry explicitly."
                ),
            )
        if row["cleanup_safe"]:
            prepare_delete(row["project_id"])
    owned = {row["operation_id"] for row in rows}
    for path in root.iterdir():
        try:
            projects.identifier(path.name)
        except ReferenceError:
            continue
        if path.name not in owned:
            clean_stage(path.name)
    with projects.database() as connection:
        outputs = {
            destination(row["project_id"], row["output_id"])
            for row in connection.execute("SELECT * FROM render_outputs")
        }
    for path in (projects.DATA_DIR / "projects").glob("*/render-*.mp4"):
        if re.fullmatch(r"render-[0-9a-f]{32}\.mp4", path.name) and path not in outputs:
            path.unlink()
