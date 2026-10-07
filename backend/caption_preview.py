"""Explicit local reference inspection and actual saved-caption still rendering."""

import math
import shutil
from contextlib import nullcontext
from typing import Literal
from uuid import UUID

from pydantic import Field

import captions
import color_analysis as color
import color_recipe as recipe
import edit_plan
import font_assets
import footage_analysis as footage
import frame_preview as preview
import framing
import sequence
import video_render as render
from references import ReferenceError


class ReferenceRequest(color.Schema):
    timestamp_seconds: float = Field(ge=0, le=120.1, strict=True, allow_inf_nan=False)
    expected_reference_operation_id: UUID


class ReferenceFrame(color.Schema):
    schema_version: Literal[1] = 1
    project_id: str
    source: color.Source
    reference_operation_id: UUID
    requested_timestamp_seconds: float
    timestamp_seconds: float
    image: preview.Image
    warnings: list[str]
    ffmpeg_version: str


class CaptionRequest(color.Schema):
    cue_index: int = Field(ge=0, le=199, strict=True)
    expected_recipe_revision: int = Field(ge=1, strict=True)
    expected_framing_revision: int = Field(ge=0, strict=True)
    expected_caption_revision: int = Field(ge=1, strict=True)
    expected_plan_revision: int | None = Field(default=None, ge=1, strict=True)
    expected_sequence_revision: int | None = Field(default=None, ge=1, strict=True)


class CaptionFrame(color.Schema):
    schema_version: Literal[1] = 1
    source: footage.Source
    recipe_revision: int
    framing_revision: int
    caption_revision: int
    font: font_assets.Binding
    font_origin: Literal["manual", "assisted"] = "manual"
    cue_index: int
    cue_start_seconds: float
    cue_end_seconds: float
    output_timestamp_seconds: float
    source_timestamp_seconds: float
    mode: Literal["whole", "cuts", "sequence"]
    plan_revision: int | None
    sequence_revision: int | None = None
    source_clip_id: UUID | None = None
    image: preview.Image
    warnings: list[str]
    ffmpeg_version: str


def version(directory, deadline):
    return (
        preview.tool(
            [shutil.which("ffmpeg") or "ffmpeg", "-version"],
            directory,
            "preview-version",
            deadline,
            8192,
        )
        .decode()
        .splitlines()[0]
    )


def png(graph, path, directory, deadline, size):
    target = directory / "caption-preview.png"
    args = [
        shutil.which("ffmpeg") or "ffmpeg",
        "-v",
        "error",
        "-nostdin",
        "-xerror",
        "-threads",
        "2",
        "-protocol_whitelist",
        "file",
        "-copyts",
        "-i",
        str(path),
        "-filter_complex_threads",
        "1",
        "-filter_complex",
        graph,
        "-map",
        "[image]",
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
        str(target),
    ]
    preview.tool(args, directory, "caption-preview-decode", deadline)
    return preview.image(target, size)


def reference_frame(project_id, request, *, stage=None):
    with nullcontext(stage) if stage else preview.operation() as (directory, deadline):
        source = color.source(project_id, deadline=deadline)
        if str(request.expected_reference_operation_id) != source["reference_operation_id"]:
            raise ReferenceError(
                409, "revision_conflict", "Reference media changed. Reload its status."
            )
        video, _, duration = render.probe(
            source["path"], directory, deadline, temp_budget=preview.TEMP_BUDGET
        )
        metadata = color.inspect_colors(video)
        selected = preview.moment(
            source["path"], video, duration, request.timestamp_seconds, directory, deadline
        )
        scale, canvas, _ = framing.geometry(video, framing.Settings())
        size = preview.display_size(canvas)
        graph = (
            f"[0:{video['index']}]setpts=PTS-STARTPTS,"
            f"select='gte(t,{selected:.9f}-0.000001)',trim=end_frame=1,"
            f"{render.resize_filter(*scale, metadata)},"
            f"scale={size[0]}:{size[1]}:flags=area,format=rgb24[image]"
        )
        image = png(graph, source["path"], directory, deadline, size)
        if color.source(project_id, deadline=deadline) != source:
            raise ReferenceError(
                409, "source_changed", "Reference changed before preview publication."
            )
        result = ReferenceFrame(
            project_id=project_id,
            source=source["identity"],
            reference_operation_id=source["reference_operation_id"],
            requested_timestamp_seconds=request.timestamp_seconds,
            timestamp_seconds=selected,
            image=image,
            warnings=metadata.warnings,
            ffmpeg_version=version(directory, deadline),
        )
        color.guard(None, deadline)
        return result


def snapshot(project_id, request, deadline):
    color_request = preview.PreviewRequest(
        expected_recipe_revision=request.expected_recipe_revision,
        expected_framing_revision=request.expected_framing_revision,
        timestamp_seconds=0,
    )
    saved_recipe, saved_framing = preview.snapshot(project_id, color_request, deadline)
    row = captions.record(project_id)
    if not row or row["state"] != "ready":
        raise ReferenceError(
            409, "captions_stale", "Save enabled captions for a valid timeline first."
        )
    track = captions.Track.model_validate_json(row["track"])
    if track.revision != request.expected_caption_revision:
        raise ReferenceError(
            409, "revision_conflict", "Captions changed. Reload before previewing."
        )
    if not track.enabled or request.cue_index >= len(track.cues):
        raise ReferenceError(422, "invalid_cue", "Enable and save a valid cue before previewing.")
    font_assets.validate(project_id, track.font_binding, track.cues, deadline=deadline)
    if track.timeline != captions.timeline(project_id, track.timeline.mode, deadline=deadline):
        raise ReferenceError(
            409, "captions_stale", "Save captions for the current output timeline."
        )
    saved_sequence = None
    if track.timeline.mode == "sequence":
        current = sequence.read(project_id)
        if (
            current.status != "ready"
            or current.sequence.revision != request.expected_sequence_revision
        ):
            raise ReferenceError(
                409, "revision_conflict", "Sequence changed. Reload before previewing."
            )
        if request.expected_plan_revision is not None:
            raise ReferenceError(
                422, "invalid_timeline", "Sequence captions do not use a legacy plan."
            )
        saved_sequence = current.sequence
    elif request.expected_sequence_revision is not None:
        raise ReferenceError(422, "invalid_timeline", "This caption timeline has no sequence.")
    plan = None
    if track.timeline.mode == "cuts":
        current = edit_plan.read(project_id)
        if current.status != "ready" or current.plan.revision != request.expected_plan_revision:
            raise ReferenceError(
                409, "revision_conflict", "Cut plan changed. Reload before previewing."
            )
        plan = current.plan
    elif request.expected_plan_revision is not None:
        raise ReferenceError(422, "invalid_timeline", "Whole-clip captions do not use a cut plan.")
    if track.automatic_binding:
        import audio_settings

        audio = audio_settings.read(project_id)
        if (
            audio.status == "stale"
            or track.automatic_binding.audio != audio.settings
            or track.automatic_binding.timeline != track.timeline
        ):
            raise ReferenceError(
                409, "captions_stale", "Automatic caption audio changed; regenerate and review."
            )
    return render.Spec(
        renderer_version=(
            "sdr-eq-mp4-v9"
            if saved_sequence
            else render.ANIMATION_VERSION
            if track.animation.mode != "none"
            else render.STYLE_VERSION
        ),
        recipe_revision=saved_recipe.revision,
        reference=saved_recipe.reference,
        footage=saved_recipe.footage,
        effective=recipe.effective(saved_recipe.selected, saved_recipe.strength),
        framing=saved_framing,
        captions=track,
        edit_plan=plan,
        sequence=saved_sequence,
    )


def output_frame(cue):
    first = edit_plan.frames(cue.start, captions.ROUND_CEILING)
    last = edit_plan.frames(cue.end, captions.ROUND_CEILING) - 1
    if first > last:
        raise ReferenceError(
            422, "cue_between_frames", "This cue has no visible 30 fps frame. Adjust its timing."
        )
    return max(first, min(last, math.floor((cue.start + cue.end) * 15)))


def caption_frame(project_id, request):
    with preview.operation() as (directory, deadline):
        spec = snapshot(project_id, request, deadline)
        source = footage.source(project_id, deadline=deadline)
        video, _, duration = render.probe(
            source["path"], directory, deadline, temp_budget=preview.TEMP_BUDGET
        )
        cue = spec.captions.cues[request.cue_index]
        index = output_frame(cue)
        source_index = index
        source_clip_id = None
        original_source = source
        path = source["path"]
        if spec.sequence:
            import clip_library

            slot = next(
                s for s in spec.sequence.slots if s.output_start_frame <= index < s.output_end_frame
            )
            source_index = slot.source_start_frame + index - slot.output_start_frame
            source_clip_id = slot.clip_id
            actual_path, actual_clip = clip_library.source(
                project_id, slot.clip_id, deadline=deadline
            )
            source = dict(
                source,
                identity=footage.Source(
                    project_id=project_id, clip_id=actual_path.name, media_sha256=actual_clip.sha256
                ),
            )
            path = render.assemble_sequence(
                {"project_id": project_id, "path": original_source["path"], "spec": spec},
                directory,
                None,
                deadline,
                shutil.which("ffmpeg") or "ffmpeg",
                start_frame=index,
                end_frame=index + 1,
                include_audio=False,
                temp_budget=preview.TEMP_BUDGET,
            )
            video, _, duration = render.probe(
                path, directory, deadline, temp_budget=preview.TEMP_BUDGET
            )
        metadata = color.inspect_colors(video)
        scale, canvas, final = framing.geometry(video, spec.framing)
        resize = render.resize_filter(*scale, metadata)
        if spec.sequence:
            canvas = (video["width"], video["height"])
            final = ""
            graph = f"[0:{video['index']}]setpts=PTS-STARTPTS+{index}/30/TB[vout]"
        elif spec.edit_plan:
            if spec.edit_plan.footage_frames > edit_plan.frames(duration, edit_plan.ROUND_FLOOR):
                raise ReferenceError(409, "source_changed", "Cut plan exceeds the current footage.")
            segment = next(
                s
                for s in spec.edit_plan.segments
                if s.output_start_frame <= index < s.output_end_frame
            )
            source_index = segment.source_start_frame + index - segment.output_start_frame
            graph = render.cut_graph(spec, video, None, 0, resize)
        else:
            if index / 30 >= duration:
                raise ReferenceError(
                    422, "invalid_cue", "Cue has no frame inside the current video."
                )
            graph = f"[0:{video['index']}]{render.whole_video_filter(resize, spec.effective)}[vout]"
        subtitle, warnings = render.subtitle(
            spec.captions, project_id, directory, *canvas, deadline, temp_budget=preview.TEMP_BUDGET
        )
        size = preview.display_size(canvas)
        filters = ",".join(
            part
            for part in (
                final,
                subtitle,
                f"select='eq(n,{0 if spec.sequence else index})'",
                "trim=end_frame=1",
                f"scale={size[0]}:{size[1]}:flags=area",
                "format=rgb24",
            )
            if part
        )
        graph += f";[vout]{filters}[image]"
        image = png(graph, path, directory, deadline, size)
        if (
            snapshot(project_id, request, deadline) != spec
            or footage.source(project_id, deadline=deadline) != original_source
        ):
            raise ReferenceError(
                409, "source_changed", "Caption preview sources or settings changed."
            )
        result = CaptionFrame(
            source=source["identity"],
            recipe_revision=spec.recipe_revision,
            framing_revision=spec.framing.revision,
            caption_revision=spec.captions.revision,
            font=spec.captions.font_binding,
            font_origin=spec.captions.style.font_origin,
            cue_index=request.cue_index,
            cue_start_seconds=cue.start,
            cue_end_seconds=cue.end,
            output_timestamp_seconds=index / 30,
            source_timestamp_seconds=source_index / 30,
            mode=spec.captions.timeline.mode,
            plan_revision=spec.captions.timeline.plan_revision,
            sequence_revision=spec.captions.timeline.sequence_revision,
            source_clip_id=source_clip_id,
            image=image,
            warnings=metadata.warnings + warnings,
            ffmpeg_version=version(directory, deadline),
        )
        color.guard(None, deadline)
        return result
