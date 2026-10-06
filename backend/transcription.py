"""Reviewed caption proposals; the shared media worker owns inference and cleanup."""

import sys
import uuid
from functools import partial
from typing import Literal

from pydantic import Field

import audio_settings
import captions
import color_analysis as color
import projects
import reference_engine as engine
import transcribe_local as adapter
import video_render as render
from references import ReferenceError

TABLE = "transcription_operations"
STAGING = "transcription-staging"
TOTAL_SECONDS = 300
ALGORITHM = adapter.ALGORITHM
component = sys.modules[__name__]
staging = partial(color.staging, component=component)
clean_stage = partial(color.clean_stage, component=component)
fail_operation = partial(color.fail_operation, component=component)
prepare_delete = partial(color.prepare_delete, component=component)
recover = partial(color.recover, component=component)
compensate = color.compensate


class GenerateRequest(color.Schema):
    language: Literal["auto", "en", "ar"] = "auto"
    replace: bool = Field(default=False, strict=True)


class Proposal(color.Schema):
    schema_version: Literal[1] = 1
    algorithm_version: Literal["base-word-cues-v1"] = ALGORITHM
    model_repository: Literal["Systran/faster-whisper-base"] = adapter.REPOSITORY
    model_revision: Literal["ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66"] = adapter.REVISION
    versions: dict[str, str]
    requested_language: Literal["auto", "en", "ar"]
    detected_language: str = Field(min_length=1, max_length=10)
    output_id: uuid.UUID
    output_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    binding: captions.AutomaticBinding
    cues: list[captions.Cue] = Field(max_length=200)
    generated_at: str


class Operation(color.Schema):
    operation_id: str | None = None
    status: Literal["idle", "running", "ready", "failed"] = "idle"
    started_at: str | None = None
    finished_at: str | None = None
    failure_code: str | None = None
    message: str | None = None
    proposal: Proposal | None = None
    stale: bool = False


class ApplyRequest(color.Schema):
    operation_id: uuid.UUID
    expected_caption_revision: int = Field(ge=0, strict=True)
    replace: bool = Field(default=False, strict=True)
    cues: list[captions.Cue] = Field(min_length=1, max_length=200)


class Application(color.Schema):
    cues: list[captions.Cue]
    timeline: captions.Timeline
    automatic_proposal_id: uuid.UUID
    provenance: Literal["automatic_transcription"] = "automatic_transcription"


def binding(project_id, timeline, stop=None, deadline=None):
    current_audio = audio_settings.read(project_id)
    if current_audio.status == "stale":
        raise ReferenceError(
            409, "audio_stale", "Save valid audio choices and render before generation."
        )
    return captions.AutomaticBinding(
        timeline=captions.timeline(
            project_id,
            timeline.mode,
            timeline.plan_revision,
            expected_sequence_revision=timeline.sequence_revision,
            stop=stop,
            deadline=deadline,
        ),
        audio=current_audio.settings,
    )


def source(project_id, stop=None, deadline=None):
    with projects.database() as connection:
        project = projects.row_project(connection, project_id)
        if project["status"] != "active":
            raise ReferenceError(409, "project_deleting", "Project deletion is in progress.")
        row = connection.execute(
            "SELECT metadata FROM render_outputs WHERE project_id=?", (project_id,)
        ).fetchone()
    if not row:
        raise ReferenceError(409, "render_required", "Render a completed video with audio first.")
    output = render.Output.model_validate_json(row[0])
    project_details = projects.get_project(project_id)
    if project_details.clip_status != "ready" or not project_details.clip:
        raise ReferenceError(409, "footage_unavailable", "Restore valid footage before generation.")
    if not output.has_audio:
        raise ReferenceError(
            409,
            "audio_unavailable",
            "This output is muted or has no audio. Save an available audio mode and render first.",
        )
    timeline = captions.Timeline(
        mode="sequence" if output.spec.sequence else "cuts" if output.spec.edit_plan else "whole",
        footage=output.spec.footage.source,
        sequence_revision=output.spec.sequence.revision if output.spec.sequence else None,
        sequence_sources={str(s.clip_id): s.source_hash for s in output.spec.sequence.slots}
        if output.spec.sequence
        else {},
        duration_seconds=output.spec.sequence.output_frames / 30
        if output.spec.sequence
        else output.spec.edit_plan.output_frames / 30
        if output.spec.edit_plan
        else project_details.clip.duration_seconds,
        plan_revision=output.spec.edit_plan.revision if output.spec.edit_plan else None,
    )
    expected = captions.AutomaticBinding(timeline=timeline, audio=output.spec.audio)
    if binding(project_id, timeline, stop, deadline) != expected:
        raise ReferenceError(
            409, "render_outdated", "Audio or timeline changed. Render saved settings first."
        )
    path = render.destination(project_id, output.output_id)
    if color.digest(path, stop, deadline, max_bytes=render.MAX_BYTES) != output.sha256:
        raise ReferenceError(409, "source_changed", "The completed output changed. Render again.")
    return {"path": path, "output": output, "binding": expected}


def get_operation(project_id, require_active=False):
    with projects.database() as connection:
        project = projects.row_project(connection, projects.identifier(project_id))
        if require_active and project["status"] != "active":
            raise ReferenceError(409, "project_deleting", "Project deletion is in progress.")
        row = connection.execute(
            f"SELECT * FROM {TABLE} WHERE project_id=?", (project_id,)
        ).fetchone()
    if not row:
        return Operation()
    proposal = Proposal.model_validate_json(row["blueprint"]) if row["blueprint"] else None
    stale, message = False, row["message"]
    if proposal:
        try:
            if binding(project_id, proposal.binding.timeline) != proposal.binding:
                raise ReferenceError(
                    409, "proposal_stale", "Audio or timeline changed; regenerate captions."
                )
        except ReferenceError:
            stale, message = True, "Audio or timeline changed; regenerate captions before applying."
    return Operation(
        operation_id=row["operation_id"],
        status=row["state"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        failure_code=row["failure_code"],
        message=message,
        proposal=proposal,
        stale=stale,
    )


def reusable(project_id, request):
    current = get_operation(project_id, True)
    if current.status == "running":
        return current
    if current.status == "ready" and not request.replace:
        if current.stale or current.proposal.requested_language != request.language:
            raise ReferenceError(
                409, "proposal_stale", "Regenerate explicitly for current audio/language."
            )
        return current
    return None


def begin_operation(project_id, request):
    current = reusable(project_id, request)
    if current:
        return current, None
    value = source(project_id)
    previous = get_operation(project_id)
    if previous.operation_id:
        prepare_delete(project_id)
    operation_id = str(uuid.uuid4())
    with projects.database() as connection:
        connection.execute(f"DELETE FROM {TABLE} WHERE project_id=?", (project_id,))
        connection.execute(
            f"INSERT INTO {TABLE}(project_id,operation_id,state,started_at,"
            "source_hash,algorithm_version) VALUES (?,?,'running',?,?,?)",
            (project_id, operation_id, projects.now(), value["output"].sha256, ALGORITHM),
        )
    value["language"] = request.language
    return get_operation(project_id), value


def pipeline(value, directory, stop, deadline):
    directory.mkdir(parents=True, exist_ok=False)
    cues, language, versions = adapter.run(
        value["path"],
        value["output"].duration_seconds,
        value["language"],
        directory,
        stop,
        deadline,
    )
    proposal = Proposal(
        versions=versions | {"ffmpeg": value["output"].ffmpeg_version},
        requested_language=value["language"],
        detected_language=language,
        output_id=value["output"].output_id,
        output_sha256=value["output"].sha256,
        binding=value["binding"],
        cues=cues,
        generated_at=projects.now(),
    )
    return value, proposal


def commit(project_id, operation_id, value, proposal, stop, deadline):
    color.guard(stop, deadline)
    if (
        binding(project_id, proposal.binding.timeline, stop, deadline) != proposal.binding
        or color.digest(value["path"], stop, deadline, max_bytes=render.MAX_BYTES)
        != proposal.output_sha256
    ):
        raise engine.RetrievalFailure(
            "source_changed", "Audio, timeline or output changed during generation."
        )
    clean_stage(operation_id)
    color.guard(stop, deadline)
    with projects.database() as connection:
        project = projects.row_project(connection, project_id)
        row = connection.execute(
            f"SELECT * FROM {TABLE} WHERE project_id=?", (project_id,)
        ).fetchone()
        if (
            project["status"] != "active"
            or not row
            or row["operation_id"] != operation_id
            or row["state"] != "running"
        ):
            raise engine.RetrievalFailure(
                "interrupted", "The caption operation is no longer current."
            )
        color.guard(stop, deadline)
        connection.execute(
            f"UPDATE {TABLE} SET state='ready',blueprint=?,finished_at=? "
            "WHERE project_id=? AND operation_id=?",
            (proposal.model_dump_json(), projects.now(), project_id, operation_id),
        )


def validated_proposal(project_id, operation_id):
    result = get_operation(project_id, True)
    if result.operation_id != str(operation_id) or result.status != "ready" or result.stale:
        raise ReferenceError(409, "proposal_stale", "Regenerate a current caption proposal first.")
    return result.proposal


def apply(project_id, request):
    proposal = validated_proposal(project_id, request.operation_id)
    saved = captions.record(project_id)
    captions.revision_check(saved, request.expected_caption_revision)
    if saved and captions.Track.model_validate_json(saved["track"]).cues and not request.replace:
        raise ReferenceError(
            409, "caption_replace_required", "Confirm replacing the existing caption draft."
        )
    try:
        captions.Choices(cues=request.cues)
        captions.validate_duration(request.cues, proposal.binding.timeline.duration_seconds)
    except ValueError as exc:
        raise ReferenceError(422, "invalid_caption", str(exc).split("\n")[0]) from None
    return Application(
        cues=request.cues,
        timeline=proposal.binding.timeline,
        automatic_proposal_id=request.operation_id,
    )


def discard(project_id):
    current = get_operation(project_id, True)
    if current.status == "running":
        raise ReferenceError(409, "transcription_running", "Wait for generation before discarding.")
    prepare_delete(project_id)
    with projects.database() as connection:
        connection.execute(f"DELETE FROM {TABLE} WHERE project_id=?", (project_id,))
    return Operation()
