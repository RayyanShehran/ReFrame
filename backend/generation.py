"""Persisted, sequential orchestration on the existing single owned media worker."""

import asyncio
import json
import logging
import shutil
import time
import uuid
from typing import Literal

from pydantic import Field

import assembly
import audio_settings
import automatic_captions
import captions
import clip_library
import color_analysis as color
import framing
import grading
import pacing_analysis
import projects
import reference_engine as engine
import reference_jobs as jobs
import sequence
import transcribe_local
import video_render as render
import visual_matching
from clips import finish_thread
from references import ReferenceError

logger = logging.getLogger(__name__)
TOTAL_SECONDS = 1200
STAGES = (
    "Reading reference",
    "Choosing your clips",
    "Matching the look",
    "Recreating captions",
    "Rendering",
)
REVISION_TABLES = (
    "sequences",
    "grading_settings",
    "audio_settings",
    "caption_tracks",
    "framing_settings",
    "color_recipes",
    "project_fonts",
    "edit_plans",
)


class Request(color.Schema):
    expected_revision: int = Field(default=0, ge=0, strict=True)
    audio: Literal["reference", "original", "mix", "mute"] = "reference"
    captions: Literal["reference", "output_audio", "off"] = "reference"
    allow_reused_ranges: bool = Field(default=False, strict=True)
    replace_customized: bool = Field(default=False, strict=True)


class Operation(color.Schema):
    project_id: str | None = None
    schema_version: Literal[1] = 1
    revision: int = 0
    operation_id: str | None = None
    status: Literal["idle", "running", "ready", "failed"] = "idle"
    stage: str | None = None
    options: Request | None = None
    completed: list[str] = Field(default_factory=list, max_length=8)
    notices: list[str] = Field(default_factory=list, max_length=30)
    failure_code: str | None = None
    message: str | None = None
    output_id: str | None = None


def record(project_id):
    projects.get_project(project_id)
    with projects.database() as db:
        return db.execute(
            "SELECT * FROM generation_operations WHERE project_id=?", (project_id,)
        ).fetchone()


def get_operation(project_id, require_active=False):
    row = record(project_id)
    if not row:
        return Operation(project_id=project_id)
    return Operation.model_validate_json(row["operation"])


def snapshot(project_id, stop=None, deadline=None):
    current = projects.get_project(project_id)
    if current.status != "active":
        raise ReferenceError(409, "project_deleting", "Project deletion is in progress.")
    clips = {}
    for clip in clip_library.read(project_id).clips:
        _, valid = clip_library.source(project_id, clip.id, stop=stop, deadline=deadline)
        clips[str(valid.id)] = valid.sha256
    if not clips:
        raise ReferenceError(409, "footage_required", "Upload footage before generating.")
    reference = jobs.get_operation(project_id)
    binding = None
    if reference.status == "ready":
        source = color.source(project_id, stop, deadline)
        binding = {
            "operation_id": source["reference_operation_id"],
            "source": source["identity"].model_dump(mode="json"),
        }
    with projects.database() as db:
        revisions = {
            table: (
                row[0]
                if (
                    row := db.execute(
                        f"SELECT revision FROM {table} WHERE project_id=?", (project_id,)
                    ).fetchone()
                )
                else 0
            )
            for table in REVISION_TABLES
        }
    return {
        "reference_url": current.reference.canonical_url,
        "reference": binding,
        "clips": clips,
        "revisions": revisions,
    }


def persist(project_id, operation, bound, cleanup_safe=True):
    with projects.database() as db:
        db.execute(
            "UPDATE generation_operations SET state=?,operation=?,snapshot=?,cleanup_safe=? "
            "WHERE project_id=? AND operation_id=?",
            (
                operation.status,
                operation.model_dump_json(),
                json.dumps(bound),
                int(cleanup_safe),
                project_id,
                operation.operation_id,
            ),
        )


def begin_operation(project_id, request):
    prior = get_operation(project_id)
    if prior.revision != request.expected_revision:
        raise ReferenceError(409, "revision_conflict", "Generation changed. Reload its status.")
    if prior.status == "running":
        return prior, None
    row = record(project_id)
    if row and not row["cleanup_safe"]:
        raise ReferenceError(
            500, "cleanup_failure", "Owned processing needs manual cleanup before retry."
        )
    bound = snapshot(project_id)
    old = json.loads(row["snapshot"]) if row else None
    customized = (
        any(bound["revisions"].values()) if not row else old["revisions"] != bound["revisions"]
    )
    if customized and not request.replace_customized:
        raise ReferenceError(
            409,
            "generation_replace_required",
            "Saved edits changed. Confirm replacement, or keep editing in Customize.",
        )
    completed = (
        prior.completed
        if row
        and old == bound
        and prior.status == "failed"
        and (
            prior.options.model_dump(exclude={"expected_revision", "replace_customized"})
            == request.model_dump(exclude={"expected_revision", "replace_customized"})
        )
        else []
    )
    operation = Operation(
        project_id=project_id,
        revision=prior.revision + 1,
        operation_id=str(uuid.uuid4()),
        status="running",
        stage=STAGES[0],
        options=request,
        completed=completed,
        notices=prior.notices if completed else [],
    )
    with projects.database() as db:
        db.execute(
            "INSERT INTO generation_operations VALUES (?,?,?,'running',?,?,1) "
            "ON CONFLICT(project_id) DO UPDATE SET operation_id=excluded.operation_id,"
            "revision=excluded.revision,"
            "state='running',operation=excluded.operation,snapshot=excluded.snapshot,cleanup_safe=1",
            (
                project_id,
                operation.operation_id,
                operation.revision,
                operation.model_dump_json(),
                json.dumps(bound),
            ),
        )
    return operation, {"operation": operation, "snapshot": bound}


def guard(project_id, operation_id, bound, stop=None, deadline=None):
    color.guard(stop, deadline)
    current = get_operation(project_id)
    if current.operation_id != operation_id or current.status != "running":
        raise ReferenceError(409, "generation_stale", "Generation is no longer current.")
    if snapshot(project_id, stop, deadline) != bound:
        raise ReferenceError(
            409,
            "generation_stale",
            "Footage, reference or saved edits changed. Retry with current inputs.",
        )


async def owned(function, stop, *args):
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        stop.set()
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not task.cancelled():
            try:
                task.result()
            except engine.ProcessCleanupError:
                raise
            except engine.RetrievalFailure as error:
                if not error.cleanup_safe:
                    raise
            except Exception:
                pass
        raise


async def worker(project_id, operation_id, data, stop):
    operation, bound = data["operation"], data["snapshot"]
    deadline = time.monotonic() + TOTAL_SECONDS

    async def locked(function, *args, advance=None):
        nonlocal bound
        async with projects.operation_lock:
            await owned(guard, stop, project_id, operation_id, bound, stop, deadline)
            result = await owned(function, stop, *args)
            bound = await owned(snapshot, stop, project_id, stop, deadline)
            if advance and advance not in operation.completed:
                operation.completed.append(advance)
            await finish_thread(persist, project_id, operation, bound)
            return result

    async def stage(label):
        operation.stage = label
        async with projects.operation_lock:
            await owned(guard, stop, project_id, operation_id, bound, stop, deadline)
            await finish_thread(persist, project_id, operation, bound)

    async def job(module, analysis, *args):
        async with projects.operation_lock:
            await owned(guard, stop, project_id, operation_id, bound, stop, deadline)
            sub, source = await owned(module.begin_operation, stop, project_id, *args)
        if source:
            await jobs.worker(
                project_id,
                sub.operation_id,
                source,
                stop,
                analysis,
                overall_deadline=deadline,
                before_commit=lambda: guard(project_id, operation_id, bound, stop, deadline),
            )
        current = await projects.storage_call(module.get_operation, project_id)
        if current.status != "ready":
            raise ReferenceError(
                409,
                current.failure_code or "generation_stage_failed",
                current.message or "This stage failed. Retry explicitly.",
            )
        return current

    failure = None
    safe = True
    try:
        # Setup is checked before retrieval or expensive analysis; no downloads here.
        if not visual_matching.readiness()["ready"]:
            raise ReferenceError(
                503, "visual_setup", "Set up local visual matching before generating. See Details."
            )
        if operation.options.captions != "off":
            await locked(capability, project_id, operation.options.captions)
        await job(jobs, False)
        # Retaining the reference legitimately extends the owned source snapshot.
        async with projects.operation_lock:
            bound = await owned(snapshot, stop, project_id, stop, deadline)
            await finish_thread(persist, project_id, operation, bound)
        await job(color, True)
        pacing = await job(pacing_analysis, "pacing")
        operation.notices.extend(pacing.blueprint.interpretation_limits[:2])
        await stage(STAGES[1])
        if "sequence" not in operation.completed:
            current = sequence.read(project_id)
            if "slots" not in operation.completed:
                generated = await locked(
                    sequence.generate,
                    project_id,
                    sequence.Generate(
                        expected_revision=current.sequence.revision if current.sequence else 0,
                        replace=bool(current.sequence),
                    ),
                    advance="slots",
                )
            else:
                generated = current
            revision = generated.sequence.revision
            settings = assembly.Settings(
                expected_sequence_revision=revision,
                matching_mode="subject_aware",
                allow_reused_ranges=operation.options.allow_reused_ranges,
            )
            selected = await job(assembly, "assembly", settings)
            if any(choice.clip_id is None for choice in selected.proposal.choices):
                missing = (
                    sum(
                        choice.duration_frames
                        for choice in selected.proposal.choices
                        if choice.clip_id is None
                    )
                    / 30
                )
                raise ReferenceError(
                    409,
                    "footage_shortage",
                    f"Need footage for {missing:.2f} more seconds. "
                    "Upload more, or explicitly allow reused footage.",
                )
            assignment = await locked(
                assembly.apply,
                project_id,
                assembly.Apply(**settings.model_dump(), proposal_id=selected.proposal.id),
            )
            await locked(
                sequence.save,
                project_id,
                sequence.Save.model_validate(assignment),
                advance="sequence",
            )
            operation.notices.extend(selected.proposal.warnings[:5])
        saved_sequence = sequence.read(project_id).sequence
        await stage(STAGES[2])
        if "grading" not in operation.completed:
            revision = grading.read_settings(project_id).revision
            await job(
                grading,
                "grading",
                grading.Prepare(
                    expected_revision=revision,
                    shot_slots=[slot.id for slot in saved_sequence.slots],
                    expected_sequence_revision=saved_sequence.revision,
                ),
            )
            await locked(
                grading.save,
                project_id,
                grading.Save(
                    expected_revision=revision,
                    mode="transfer",
                    shot_slots=[slot.id for slot in saved_sequence.slots],
                    controls={
                        f"slot:{slot.id}": grading.transfer.Controls(strength=0.7)
                        for slot in saved_sequence.slots
                    },
                ),
                advance="grading",
            )
        if "audio" not in operation.completed:
            previous = audio_settings.read(project_id).settings
            await locked(
                audio_settings.save,
                project_id,
                audio_settings.SaveRequest(
                    expected_revision=previous.revision,
                    mode=operation.options.audio,
                    original_volume=70.0 if operation.options.audio == "mix" else 100.0,
                    reference_volume=30.0 if operation.options.audio == "mix" else 100.0,
                    refresh_sources=True,
                ),
                advance="audio",
            )
        await stage(STAGES[3])
        if "captions" not in operation.completed:
            await locked(
                prepare_captions,
                project_id,
                operation.options.captions,
                stop,
                min(deadline, time.monotonic() + automatic_captions.TOTAL_SECONDS),
                operation,
                advance="captions",
            )
        await stage(STAGES[4])
        current = captions.read(project_id).track
        await job(
            render,
            "render",
            0,
            None,
            audio_settings.read(project_id).settings.revision,
            current.revision,
            framing.read(project_id).settings.revision,
            saved_sequence.revision,
            grading.read_settings(project_id).revision,
        )
        output = render.get_operation(project_id).output
        operation.output_id = str(output.output_id)
        operation.status = "ready"
        operation.notices = list(dict.fromkeys(operation.notices))[:30]
        async with projects.operation_lock:
            await owned(guard, stop, project_id, operation_id, bound, stop, deadline)
            await finish_thread(persist, project_id, operation, bound)
    except engine.ProcessCleanupError:
        safe = False
        failure = (
            "cleanup_failure",
            "Owned processing could not be confirmed stopped; files retained.",
        )
    except engine.RetrievalFailure as error:
        safe = error.cleanup_safe
        failure = (error.code, error.message)
    except ReferenceError as error:
        safe = error.code != "cleanup_failure"
        failure = (error.code, error.message)
    except (asyncio.CancelledError, engine.ProbeTimeout):
        stop.set()
        failure = ("interrupted", "Generation stopped. Retry explicitly to resume safe saved work.")
    except Exception:
        logger.exception("Generation failed; internal details withheld from UI")
        failure = ("generation_failed", "Generation failed. Retry or open Details.")
    finally:
        if failure:
            operation.status = "failed"
            operation.failure_code, operation.message = failure
            await finish_thread(persist, project_id, operation, bound, safe)


def staging(operation_id):
    return projects.DATA_DIR / "generation-staging" / projects.identifier(operation_id)


def capability(project_id, mode):
    import workflow_capabilities

    result = workflow_capabilities.inspect(project_id)
    if not result.captions.available or not result.ocr.available:
        raise ReferenceError(
            503,
            "caption_setup",
            "Set up local caption reading/rendering, or choose captions Off. See Details.",
        )
    if mode == "output_audio" and not result.transcription.available:
        raise ReferenceError(
            503,
            "transcription_setup",
            "Set up local speech captions, or choose another caption mode. See Details.",
        )


def prepare_captions(project_id, mode, stop, deadline, operation):
    directory = staging(operation.operation_id)
    directory.parent.mkdir(exist_ok=True)
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir()
    safe = True
    engine._control.stop = stop
    try:
        previous = captions.read(project_id).track
        cues, notices, evidence = [], [], None
        if mode != "off":
            source = color.source(project_id, stop, deadline)
            cues, notices, evidence = automatic_captions.detect(
                project_id, source, directory, stop, deadline
            )
        selected_sequence = sequence.read(project_id).sequence
        values = dict(
            expected_revision=previous.revision,
            mode="sequence",
            expected_sequence_revision=selected_sequence.revision,
            confirm_rebind=True,
            enabled=bool(cues),
            cues=cues,
        )
        provenance = None
        if mode == "reference":
            values["provenance"] = "automatic_reference"
            provenance = captions.VisibleBinding.model_validate(evidence)
        audio_binding = None
        if mode == "output_audio":
            # Transcribe the actual assembled audio without publishing this scratch export.
            disabled = previous.model_copy(update={"enabled": False})
            spec = render.specification(
                project_id,
                0,
                None,
                audio_settings.read(project_id).settings.revision,
                previous.revision,
                framing.read(project_id).settings.revision,
                selected_sequence.revision,
                grading.read_settings(project_id).revision,
                caption_override=disabled,
            )
            spec = spec.model_copy(update={"captions": disabled})
            source = render.footage.source(project_id, stop, deadline)
            scratch = directory / "assembled-audio-source"
            path, output = render.pipeline(
                {"project_id": project_id, "path": source["path"], "spec": spec},
                scratch,
                stop,
                deadline,
            )
            if not output.has_audio:
                raise ReferenceError(
                    409,
                    "output_audio_unavailable",
                    "This output is silent. Choose reference captions or Off.",
                )
            speech = directory / "speech"
            speech.mkdir()
            recognized = transcribe_local.run(
                path, output.duration_seconds, "auto", speech, stop, deadline
            )
            style = cues[0].appearance if cues else None
            cues = [
                captions.Cue.model_validate(cue).model_copy(update={"appearance": style})
                for cue in recognized[0]
            ]
            values.update(cues=cues, enabled=bool(cues), provenance="generated_output_audio")
            audio_binding = (
                captions.AutomaticBinding(
                    timeline=captions.timeline(project_id, "sequence"),
                    audio=audio_settings.read(project_id).settings,
                ),
                output.sha256,
            )
        result = captions.save(
            project_id,
            captions.SaveRequest(**values),
            visible_binding=provenance,
            generated_audio=audio_binding,
        )
        operation.notices.extend(notices)
        return result
    except engine.ProcessCleanupError:
        safe = False
        raise
    except engine.RetrievalFailure as error:
        safe = error.cleanup_safe
        raise
    except ReferenceError as error:
        safe = error.code != "cleanup_failure"
        raise
    finally:
        if safe:
            try:
                shutil.rmtree(directory)
            except OSError:
                raise ReferenceError(
                    500, "cleanup_failure", "Caption staging needs cleanup before retry."
                ) from None


def prepare_delete(project_id):
    row = record(project_id)
    if row:
        if not row["cleanup_safe"]:
            raise ReferenceError(
                500, "cleanup_failure", "Owned generation files retained for manual review."
            )
        directory = staging(row["operation_id"])
        if directory.exists():
            shutil.rmtree(directory)


def recover():
    with projects.database() as db:
        rows = db.execute("SELECT * FROM generation_operations").fetchall()
    for row in rows:
        operation = Operation.model_validate_json(row["operation"])
        if operation.status == "running":
            operation.status = "failed"
            operation.failure_code = "interrupted"
            operation.message = "Backend restarted. Retry explicitly to resume valid saved work."
            persist(
                row["project_id"], operation, json.loads(row["snapshot"]), bool(row["cleanup_safe"])
            )
        if row["cleanup_safe"]:
            prepare_delete(row["project_id"])
