"""Lifecycle-owned experimental reference retrieval; no queue or automatic retry."""

import asyncio
import hashlib
import json
import logging
import shutil
import sqlite3
import threading
import time
import uuid
from typing import Literal

from pydantic import BaseModel, Field

import projects
import reference_engine as engine
from clips import finish_thread
from references import ReferenceError, canonicalize_url

logger = logging.getLogger(__name__)


class ReferenceMedia(BaseModel):
    size_bytes: int = Field(gt=0, le=50 * 1024 * 1024)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    duration_seconds: float = Field(gt=0, le=120, allow_inf_nan=False)
    width: int = Field(gt=0, le=4096)
    height: int = Field(gt=0, le=4096)
    video_codec: str = Field(pattern=r"^[a-zA-Z0-9_]{1,40}$")
    has_audio: bool
    audio_codec: str | None
    decoded_frame_bytes: int = Field(ge=12288, le=12288)
    decoded_audio_samples: int = Field(ge=0, le=16000)
    retrieved_at: str
    versions: dict[str, str]


class Operation(BaseModel):
    operation_id: str | None = None
    status: Literal["idle", "running", "ready", "failed"] = "idle"
    started_at: str | None = None
    finished_at: str | None = None
    failure_code: str | None = None
    message: str | None = None
    media: ReferenceMedia | None = None


# ponytail: one lifecycle-owned task for this single-process local app; no queued work.
active = None
start_lock = asyncio.Lock()
closing = set()
stopping = False


def staging(operation_id):
    return projects.DATA_DIR / "reference-staging" / projects.identifier(operation_id)


def destination(project_id, operation_id):
    path = (
        projects.DATA_DIR
        / "projects"
        / projects.identifier(project_id)
        / f"reference-{uuid.UUID(projects.identifier(operation_id)).hex}.media"
    )
    if not path.resolve().is_relative_to((projects.DATA_DIR / "projects").resolve()):
        raise ReferenceError(500, "storage_failure", "Reference storage is unavailable.")
    return path


def get_operation(project_id, require_active=False):
    projects.identifier(project_id)
    with projects.database() as connection:
        project = projects.row_project(connection, project_id)
        if require_active and project["status"] != "active":
            raise ReferenceError(409, "project_deleting", "Retry project deletion first.")
        row = connection.execute(
            "SELECT * FROM reference_operations WHERE project_id = ?", (project_id,)
        ).fetchone()
        if not row:
            return Operation()
        media = None
        if row["state"] == "ready":
            try:
                media = ReferenceMedia.model_validate_json(row["metadata"])
                path = destination(project_id, row["operation_id"])
                if not path.is_file() or path.stat().st_size != media.size_bytes:
                    raise ValueError("Missing media")
            except (ValueError, OSError, ReferenceError):
                connection.execute(
                    "UPDATE reference_operations SET state = 'failed', "
                    "failure_code = 'media_unavailable', message = ?, finished_at = ? "
                    "WHERE project_id = ?",
                    (
                        "Retained reference media is unavailable or corrupt.",
                        projects.now(),
                        project_id,
                    ),
                )
                media = None
                row = connection.execute(
                    "SELECT * FROM reference_operations WHERE project_id = ?", (project_id,)
                ).fetchone()
        return Operation(
            operation_id=row["operation_id"],
            status=row["state"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            failure_code=row["failure_code"],
            message=row["message"],
            media=media,
        )


def clean_stage(operation_id):
    path = staging(operation_id)
    try:
        if path.exists():
            shutil.rmtree(path)
    except OSError:
        raise ReferenceError(
            500, "cleanup_failure", "Reference staging could not be removed."
        ) from None


def begin_operation(project_id):
    previous = get_operation(project_id, require_active=True)
    if previous.status in {"ready", "running"}:
        return previous, None
    with projects.database() as connection:
        project = projects.row_project(connection, project_id)
        if project["status"] != "active":
            raise ReferenceError(409, "project_deleting", "Retry deleting this project first.")
        saved = json.loads(project["reference"])
        canonical, video_id = canonicalize_url(saved["canonical_url"])
        if canonical != saved["canonical_url"] or video_id != saved["video_id"]:
            raise ReferenceError(
                409, "identity_mismatch", "The saved reference identity is invalid."
            )
        old = connection.execute(
            "SELECT * FROM reference_operations WHERE project_id = ?", (project_id,)
        ).fetchone()
        if old:
            if not old["cleanup_safe"]:
                raise ReferenceError(
                    500,
                    "cleanup_failure",
                    "Owned processing could not be confirmed stopped; "
                    "retained staging needs manual review.",
                )
            clean_stage(old["operation_id"])
            destination(project_id, old["operation_id"]).unlink(missing_ok=True)
        operation_id = str(uuid.uuid4())
        connection.execute("DELETE FROM reference_operations WHERE project_id = ?", (project_id,))
        connection.execute(
            "INSERT INTO reference_operations(project_id, "
            "operation_id, state, started_at) VALUES (?, ?, 'running', ?)",
            (project_id, operation_id, projects.now()),
        )
    return get_operation(project_id), canonical


def fail_operation(project_id, operation_id, failure):
    with projects.database() as connection:
        connection.execute(
            "UPDATE reference_operations SET state = 'failed', "
            "finished_at = ?, failure_code = ?, message = ?, cleanup_safe = ? "
            "WHERE project_id = ? AND operation_id = ?",
            (
                projects.now(),
                failure.code,
                failure.message,
                int(failure.cleanup_safe),
                project_id,
                operation_id,
            ),
        )


def pipeline(url, directory, stop, deadline):
    directory.mkdir(parents=True, exist_ok=False)
    result = engine.retrieve_media(url, directory, stop)
    engine.remaining(deadline, engine.TOTAL_SECONDS)
    with result["path"].open("rb") as file:
        digest = hashlib.file_digest(file, "sha256").hexdigest()
    engine.remaining(deadline, engine.TOTAL_SECONDS)
    media = ReferenceMedia(
        **result["media"],
        size_bytes=result["path"].stat().st_size,
        sha256=digest,
        retrieved_at=projects.now(),
        versions=result["versions"],
    )
    return result["path"], media


def commit_media(project_id, operation_id, path, media, stop, deadline):
    engine.check_interrupted()
    if stop.is_set() or time.monotonic() >= deadline:
        raise engine.RetrievalFailure(
            "interrupted" if stop.is_set() else "deadline",
            "Reference retrieval stopped before retention.",
        )
    target = destination(project_id, operation_id)
    with projects.database() as connection:
        row = projects.row_project(connection, project_id)
        operation = connection.execute(
            "SELECT * FROM reference_operations WHERE project_id = ?", (project_id,)
        ).fetchone()
        if (
            row["status"] != "active"
            or not operation
            or operation["operation_id"] != operation_id
            or operation["state"] != "running"
        ):
            raise engine.RetrievalFailure(
                "interrupted", "The retrieval operation is no longer current."
            )
        connection.execute(
            "UPDATE reference_operations SET filename = ?, metadata = "
            "? WHERE project_id = ? AND operation_id = ?",
            (target.name, media.model_dump_json(), project_id, operation_id),
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    path.replace(target)
    clean_stage(operation_id)
    with projects.database() as connection:
        row = projects.row_project(connection, project_id)
        operation = connection.execute(
            "SELECT * FROM reference_operations WHERE project_id = ?", (project_id,)
        ).fetchone()
        if (
            row["status"] != "active"
            or not operation
            or operation["operation_id"] != operation_id
            or operation["state"] != "running"
            or stop.is_set()
            or time.monotonic() >= deadline
        ):
            raise engine.RetrievalFailure("interrupted", "Reference retention was interrupted.")
        connection.execute(
            "UPDATE reference_operations SET state = 'ready', "
            "finished_at = ?, failure_code = NULL, message = NULL "
            "WHERE project_id = ? AND operation_id = ?",
            (projects.now(), project_id, operation_id),
        )
        connection.execute(
            "UPDATE projects SET updated_at = ? WHERE id = ?", (projects.now(), project_id)
        )


def compensate(project_id, operation_id):
    target = destination(project_id, operation_id)
    target.unlink(missing_ok=True)


async def worker(project_id, operation_id, url, stop, analysis=False):
    global active
    failure = None
    if analysis:
        if analysis == "render":
            import video_render as color
        elif analysis == "grading":
            import grading as color
        elif analysis == "assembly":
            import assembly as color
        elif analysis == "transcription":
            import transcription as color
        elif analysis == "footage":
            import footage_analysis as color
        elif analysis == "pacing":
            import pacing_analysis as color
        else:
            import color_analysis as color

        process, commit, stage, clean, fail = (
            color.pipeline,
            color.commit,
            color.staging,
            color.clean_stage,
            color.fail_operation,
        )
        compensate_fn, seconds = color.compensate, color.TOTAL_SECONDS
    else:
        process, commit, stage, clean, fail = (
            pipeline,
            commit_media,
            staging,
            clean_stage,
            fail_operation,
        )
        compensate_fn, seconds = compensate, engine.TOTAL_SECONDS
    deadline = time.monotonic() + seconds
    try:
        processing = asyncio.create_task(
            asyncio.to_thread(process, url, stage(operation_id), stop, deadline)
        )
        try:
            path, media = await asyncio.shield(processing)
        except asyncio.CancelledError:
            stop.set()
            while not processing.done():
                try:
                    await asyncio.shield(processing)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if processing.done() and not processing.cancelled():
                try:
                    processing.result()
                except engine.RetrievalFailure as exc:
                    if not exc.cleanup_safe:
                        raise exc
                except Exception:
                    pass
            raise
        async with projects.operation_lock:
            await finish_thread(commit, project_id, operation_id, path, media, stop, deadline)
    except engine.RetrievalFailure as exc:
        failure = exc
    except engine.ProbeTimeout:
        failure = engine.RetrievalFailure("deadline", "Reference processing exceeded its deadline.")
    except asyncio.CancelledError:
        stop.set()
        failure = engine.RetrievalFailure("interrupted", "Reference retrieval was interrupted.")
    except ReferenceError as exc:
        failure = engine.RetrievalFailure(exc.code, exc.message)
    except (sqlite3.Error, OSError):
        logger.exception("Reference retention storage failure")
        failure = engine.RetrievalFailure(
            "storage_failure", "Reference media could not be saved safely."
        )
    except Exception:
        # Do not leak tool errors or leave an operation running after an unexpected failure.
        logger.error("Reference processing failed unexpectedly; raw details withheld")
        failure = engine.RetrievalFailure("retrieval_failure", "Reference processing failed.")
    finally:
        try:
            if failure and failure.cleanup_safe:
                await finish_thread(compensate_fn, project_id, operation_id)
            if not failure or failure.cleanup_safe:
                await finish_thread(clean, operation_id)
        except (OSError, ReferenceError):
            logger.exception("Reference staging/retention cleanup failed")
            failure = engine.RetrievalFailure(
                "cleanup_failure", "Reference temporary files could not be removed."
            )
        if failure:
            try:
                await finish_thread(fail, project_id, operation_id, failure)
            except sqlite3.Error:
                logger.error(
                    "Reference failure state could not be saved; restart recovery required"
                )
        if active and active[1] == operation_id:
            active = None


async def start(
    project_id,
    analysis=False,
    expected_revision=None,
    expected_plan_revision=None,
    expected_audio_revision=None,
    expected_caption_revision=None,
    expected_framing_revision=None,
    transcription_request=None,
    expected_sequence_revision=None,
    assembly_request=None,
    grading_request=None,
    expected_grading_revision=None,
):
    global active
    projects.identifier(project_id)
    if analysis:
        if analysis == "render":
            import video_render as color
        elif analysis == "grading":
            import grading as color
        elif analysis == "assembly":
            import assembly as color
        elif analysis == "transcription":
            import transcription as color
        elif analysis == "footage":
            import footage_analysis as color
        elif analysis == "pacing":
            import pacing_analysis as color
        else:
            import color_analysis as color

        read, begin = color.get_operation, color.begin_operation
    else:
        read, begin = get_operation, begin_operation
    async with start_lock:
        if project_id in closing:
            raise ReferenceError(409, "project_deleting", "Project deletion is in progress.")
        if analysis == "render":
            async with projects.operation_lock:
                current = await projects.storage_call(
                    color.reusable,
                    project_id,
                    expected_revision,
                    expected_plan_revision,
                    expected_audio_revision,
                    expected_caption_revision,
                    expected_framing_revision,
                    expected_sequence_revision,
                    expected_grading_revision,
                )
        elif analysis == "grading":
            async with projects.operation_lock:
                current = await projects.storage_call(color.reusable, project_id, grading_request)
        elif analysis == "assembly":
            async with projects.operation_lock:
                current = await projects.storage_call(color.reusable, project_id, assembly_request)
        elif analysis == "transcription":
            async with projects.operation_lock:
                current = await projects.storage_call(
                    color.reusable, project_id, transcription_request
                )
        else:
            current = await projects.storage_call(read, project_id, True)
        if current and current.status in {"running", "ready"}:
            return current
        if stopping or project_id in closing or active:
            raise ReferenceError(
                503, "reference_busy", "Another media job is running. Please retry later."
            )
        await projects.storage_call(check_quarantine)

        async def launch():
            global active
            async with projects.operation_lock:
                args = (
                    (
                        project_id,
                        expected_revision,
                        expected_plan_revision,
                        expected_audio_revision,
                        expected_caption_revision,
                        expected_framing_revision,
                        expected_sequence_revision,
                        expected_grading_revision,
                    )
                    if analysis == "render"
                    else (project_id, grading_request)
                    if analysis == "grading"
                    else (project_id, assembly_request)
                    if analysis == "assembly"
                    else (project_id, transcription_request)
                    if analysis == "transcription"
                    else (project_id,)
                )
                operation, url = await projects.storage_call(begin, *args)
            if url:
                stop = threading.Event()
                task = asyncio.create_task(
                    worker(project_id, operation.operation_id, url, stop, analysis)
                )
                active = (project_id, operation.operation_id, stop, task)
            return operation

        # A disconnected start request must not strand a persisted running row without its owner.
        launching = asyncio.create_task(launch())
        try:
            return await asyncio.shield(launching)
        except asyncio.CancelledError:
            while not launching.done():
                try:
                    await asyncio.shield(launching)
                except asyncio.CancelledError:
                    continue
            launching.result()
            raise


async def delete(project_id):
    projects.identifier(project_id)
    async with start_lock:
        closing.add(project_id)
        owned = active if active and active[0] == project_id else None
        if owned:
            owned[2].set()
    try:
        if owned:
            await asyncio.shield(owned[3])
        async with projects.operation_lock:
            with_cleanup = await projects.storage_call(get_operation, project_id)
            if with_cleanup.failure_code == "cleanup_failure":
                safe = await projects.storage_call(cleanup_confirmed, project_id)
                if not safe:
                    raise ReferenceError(
                        500,
                        "cleanup_failure",
                        "Owned processing could not be confirmed stopped; files were retained.",
                    )
            if with_cleanup.operation_id:
                await projects.storage_call(clean_stage, with_cleanup.operation_id)
            import color_analysis as color

            await projects.storage_call(color.prepare_delete, project_id)
            import pacing_analysis as pacing

            await projects.storage_call(pacing.prepare_delete, project_id)
            import footage_analysis as footage

            await projects.storage_call(footage.prepare_delete, project_id)
            import video_render as render

            await projects.storage_call(render.prepare_delete, project_id)
            import assembly

            await projects.storage_call(assembly.prepare_delete, project_id)
            import transcription

            await projects.storage_call(transcription.prepare_delete, project_id)
            import grading

            await projects.storage_call(grading.prepare_delete, project_id)
            await projects.storage_call(projects.delete_project, project_id)
    finally:
        closing.discard(project_id)


def cleanup_confirmed(project_id):
    with projects.database() as connection:
        return bool(
            connection.execute(
                "SELECT cleanup_safe FROM reference_operations WHERE project_id = ?", (project_id,)
            ).fetchone()[0]
        )


def check_quarantine():
    with projects.database() as connection:
        unsafe = connection.execute(
            "SELECT 1 FROM reference_operations WHERE cleanup_safe = 0 "
            "UNION ALL SELECT 1 FROM color_operations WHERE cleanup_safe = 0 "
            "UNION ALL SELECT 1 FROM pacing_operations WHERE cleanup_safe = 0 "
            "UNION ALL SELECT 1 FROM footage_color_operations WHERE cleanup_safe = 0 "
            "UNION ALL SELECT 1 FROM render_operations WHERE cleanup_safe = 0 "
            "UNION ALL SELECT 1 FROM grading_operations WHERE cleanup_safe = 0 "
            "UNION ALL SELECT 1 FROM assembly_operations WHERE cleanup_safe = 0 "
            "UNION ALL SELECT 1 FROM transcription_operations WHERE cleanup_safe = 0 LIMIT 1"
        ).fetchone()
    if (
        unsafe
        or any((projects.DATA_DIR / "framing-inspection").glob("*"))
        or any((projects.DATA_DIR / "preview-staging").glob("*"))
    ):
        raise ReferenceError(
            500,
            "cleanup_failure",
            "Unconfirmed owned processing needs manual review before another job.",
        )


async def shutdown():
    global stopping
    async with start_lock:
        stopping = True
        owned = active
        if owned:
            owned[2].set()
    if owned:
        await asyncio.shield(owned[3])


def recover():
    root = projects.DATA_DIR / "reference-staging"
    root.mkdir(exist_ok=True)
    with projects.database() as connection:
        records = connection.execute("SELECT * FROM reference_operations").fetchall()
    for row in records:
        project_id, operation_id = row["project_id"], row["operation_id"]
        if row["state"] == "running":
            compensate(project_id, operation_id)
            fail_operation(
                project_id,
                operation_id,
                engine.RetrievalFailure(
                    "interrupted", "Backend restarted during reference retrieval. Retry explicitly."
                ),
            )
        elif row["state"] == "ready":
            try:
                media = ReferenceMedia.model_validate_json(row["metadata"])
                path = destination(project_id, operation_id)
                with path.open("rb") as file:
                    if (
                        path.stat().st_size != media.size_bytes
                        or hashlib.file_digest(file, "sha256").hexdigest() != media.sha256
                    ):
                        raise ValueError("Invalid retained media")
            except (OSError, ValueError, ReferenceError):
                with projects.database() as connection:
                    connection.execute(
                        "UPDATE reference_operations SET state = "
                        "'failed', failure_code = 'media_unavailable', message = ? "
                        "WHERE project_id = ?",
                        ("Retained reference media is unavailable or corrupt.", project_id),
                    )
        if row["cleanup_safe"]:
            clean_stage(operation_id)
    known = {row["operation_id"] for row in records}
    for directory in root.iterdir():
        try:
            projects.identifier(directory.name)
        except ReferenceError:
            continue
        if directory.name not in known and directory.is_dir():
            clean_stage(directory.name)
