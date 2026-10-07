"""Single-user local projects. SQLite transactions do not make file moves atomic."""

import asyncio
import hashlib
import json
import logging
import re
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import Request
from pydantic import BaseModel, field_validator

from clips import ClipDetails, finish_thread, staged_clip
from references import ReferenceDetails, ReferenceError, inspect_reference

logger = logging.getLogger(__name__)
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
# ponytail: one local process; use per-project locks and cross-process coordination before scaling.
operation_lock = asyncio.Lock()


def now() -> str:
    return datetime.now(UTC).isoformat()


def identifier(value: str) -> str:
    try:
        if str(uuid.UUID(value)) == value:
            return value
    except ValueError:
        pass
    raise ReferenceError(422, "invalid_project_id", "The project ID is invalid.")


class ProjectRequest(BaseModel):
    name: str
    reference_url: str

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        value = value.strip()
        if not 1 <= len(value) <= 80:
            raise ValueError("Project names must be 1–80 characters.")
        return value


class RetainedClip(ClipDetails):
    storage_status: Literal["retained"] = "retained"
    sha256: str
    saved_at: str


class ProjectDetails(BaseModel):
    id: str
    name: str
    created_at: str
    updated_at: str
    reference: ReferenceDetails
    reference_inspected_at: str
    status: Literal["active", "deleting"]
    clip_status: Literal["empty", "staging", "validated", "ready", "unavailable"]
    clip: RetainedClip | None


@contextmanager
def database():
    # Each operation owns its connection on the same worker thread.
    connection = sqlite3.connect(DATA_DIR / "reframe.sqlite3", autocommit=True, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()


def media_path(project_id: str, filename: str) -> Path:
    identifier(project_id)
    # Stored names are generated, and checked again before any filesystem operation.
    if not isinstance(filename, str) or not re.fullmatch(r"clip-[0-9a-f]{32}\.(mp4|mov)", filename):
        raise ReferenceError(500, "storage_failure", "Project storage is unavailable.")
    return DATA_DIR / "projects" / project_id / filename


def row_project(connection, project_id):
    row = connection.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if row is None:
        raise ReferenceError(404, "project_not_found", "This project no longer exists.")
    return row


def get_project(project_id: str) -> ProjectDetails:
    identifier(project_id)
    with database() as connection:
        row = row_project(connection, project_id)
        clip = connection.execute(
            "SELECT * FROM clips WHERE project_id = ?", (project_id,)
        ).fetchone()
        state = clip["state"] if clip else "empty"
        details = None
        if clip and state == "ready":
            try:
                path = media_path(project_id, clip["filename"])
                metadata = json.loads(clip["metadata"])
                details = RetainedClip(**metadata, sha256=clip["sha256"], saved_at=clip["saved_at"])
                if not path.is_file() or path.stat().st_size != details.size_bytes:
                    state = "unavailable"
            except (ValueError, TypeError, ReferenceError):
                logger.exception("Retained metadata is corrupt")
                state = "unavailable"
            if state == "unavailable":
                details = None
                connection.execute(
                    "UPDATE clips SET state = ? WHERE project_id = ?", (state, project_id)
                )
        return ProjectDetails(
            id=row["id"],
            name=row["name"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            reference=ReferenceDetails.model_validate_json(row["reference"]),
            reference_inspected_at=row["reference_inspected_at"],
            status=row["status"],
            clip_status=state,
            clip=details,
        )


def list_projects() -> list[ProjectDetails]:
    with database() as connection:
        ids = [
            row[0]
            for row in connection.execute("SELECT id FROM projects ORDER BY updated_at DESC, id")
        ]
    return [get_project(project_id) for project_id in ids]


def insert_project(name: str, reference: ReferenceDetails) -> ProjectDetails:
    project_id, timestamp = str(uuid.uuid4()), now()
    with database() as connection:
        if connection.execute("SELECT count(*) FROM projects").fetchone()[0] >= 10:
            raise ReferenceError(
                409, "project_limit", "You can save up to 10 projects. Delete one first."
            )
        connection.execute(
            "INSERT INTO projects VALUES (?, ?, ?, ?, ?, ?, 'active')",
            (project_id, name, timestamp, timestamp, reference.model_dump_json(), timestamp),
        )
    return get_project(project_id)


def begin_upload(project_id: str):
    with database() as connection:
        row = row_project(connection, project_id)
        if row["status"] != "active":
            raise ReferenceError(
                409, "project_deleting", "Retry deleting this project before uploading."
            )
        if connection.execute("SELECT 1 FROM clips WHERE project_id = ?", (project_id,)).fetchone():
            raise ReferenceError(
                409, "clip_exists", "This project already has a clip. Replacement comes later."
            )
        connection.execute(
            "INSERT INTO clips(project_id, state) VALUES (?, 'staging')", (project_id,)
        )


def mark_ready(project_id: str):
    with database() as connection:
        connection.execute("UPDATE clips SET state = 'ready' WHERE project_id = ?", (project_id,))
        connection.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (now(), project_id))


def retain_clip(project_id: str, path: Path, details: ClipDetails, digest: str):
    metadata = details.model_dump(exclude={"storage_status"})
    destination = media_path(project_id, path.name)
    with database() as connection:
        row_project(connection, project_id)
        connection.execute(
            "UPDATE clips SET state = 'validated', filename = ?, metadata = ?, "
            "sha256 = ?, saved_at = ? WHERE project_id = ?",
            (path.name, json.dumps(metadata), digest, now(), project_id),
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    path.replace(destination)
    mark_ready(project_id)


def abort_upload(project_id: str):
    with database() as connection:
        clip = connection.execute(
            "SELECT * FROM clips WHERE project_id = ?", (project_id,)
        ).fetchone()
        if not clip or clip["state"] == "ready":
            return
        if clip["filename"]:
            try:
                media_path(project_id, clip["filename"]).unlink(missing_ok=True)
            except OSError:
                logger.exception("Unfinished retained clip cleanup failed")
                connection.execute(
                    "UPDATE clips SET state = 'unavailable' WHERE project_id = ?", (project_id,)
                )
                # Commit the visible failed state, then report failure outside this transaction.
                failed = True
            else:
                failed = False
        else:
            failed = False
        if not failed:
            connection.execute("DELETE FROM clips WHERE project_id = ?", (project_id,))
    if failed:
        raise ReferenceError(
            500,
            "cleanup_failure",
            "The unfinished clip could not be removed. Retry deleting the project.",
        )


def delete_project(project_id: str):
    with database() as connection:
        row_project(connection, project_id)
        connection.execute("UPDATE projects SET status = 'deleting' WHERE id = ?", (project_id,))
    directory = DATA_DIR / "projects" / identifier(project_id)
    if not directory.resolve().is_relative_to((DATA_DIR / "projects").resolve()):
        raise ReferenceError(500, "cleanup_failure", "Project storage cannot be removed safely.")
    try:
        if directory.exists():
            shutil.rmtree(directory)
    except OSError:
        logger.exception("Project deletion cleanup failed")
        raise ReferenceError(
            500, "cleanup_failure", "The project files could not be removed. Retry deletion."
        ) from None
    with database() as connection:
        connection.execute("DELETE FROM projects WHERE id = ?", (project_id,))


def initialize():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "projects").mkdir(exist_ok=True)
    staging = DATA_DIR / "project-staging"
    staging.mkdir(exist_ok=True)
    with database() as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19}:
            raise RuntimeError("Unsupported ReFrame database schema")
        connection.execute("""CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL, reference TEXT NOT NULL, reference_inspected_at TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('active','deleting')))""")
        connection.execute("""CREATE TABLE IF NOT EXISTS clips (
            project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
            state TEXT NOT NULL CHECK(state IN ('staging','validated','ready','unavailable')),
            filename TEXT, metadata TEXT, sha256 TEXT, saved_at TEXT)""")
        connection.execute("""CREATE TABLE IF NOT EXISTS reference_operations (
            project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
            operation_id TEXT NOT NULL UNIQUE, state TEXT NOT NULL
                CHECK(state IN ('running','ready','failed')),
            started_at TEXT NOT NULL, finished_at TEXT, failure_code TEXT, message TEXT,
            filename TEXT, metadata TEXT, cleanup_safe INTEGER NOT NULL DEFAULT 1)""")
        for table in (
            "color_operations",
            "pacing_operations",
            "footage_color_operations",
            "transcription_operations",
            "assembly_operations",
        ):
            connection.execute(f"""CREATE TABLE IF NOT EXISTS {table} (
                project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
                operation_id TEXT NOT NULL UNIQUE,
                state TEXT NOT NULL CHECK(state IN ('running','ready','failed')),
                started_at TEXT NOT NULL, finished_at TEXT, failure_code TEXT, message TEXT,
                source_hash TEXT NOT NULL, algorithm_version TEXT NOT NULL, blueprint TEXT,
                cleanup_safe INTEGER NOT NULL DEFAULT 1)""")
        connection.execute("""CREATE TABLE IF NOT EXISTS color_recipes (
            project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
            revision INTEGER NOT NULL CHECK(revision >= 1),
            state TEXT NOT NULL CHECK(state IN ('ready','stale')),
            recipe TEXT NOT NULL, message TEXT)""")
        connection.execute("""CREATE TABLE IF NOT EXISTS render_operations (
            project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
            operation_id TEXT NOT NULL UNIQUE,
            state TEXT NOT NULL CHECK(state IN ('running','ready','failed')),
            started_at TEXT NOT NULL, finished_at TEXT, failure_code TEXT, message TEXT,
            spec TEXT NOT NULL, cleanup_safe INTEGER NOT NULL DEFAULT 1)""")
        connection.execute("""CREATE TABLE IF NOT EXISTS render_outputs (
            project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
            output_id TEXT NOT NULL UNIQUE, metadata TEXT NOT NULL)""")
        connection.execute("""CREATE TABLE IF NOT EXISTS edit_plans (
            project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
            revision INTEGER NOT NULL CHECK(revision >= 1),
            state TEXT NOT NULL CHECK(state IN ('ready','stale')),
            plan TEXT NOT NULL, message TEXT)""")
        connection.execute("""CREATE TABLE IF NOT EXISTS audio_settings (
            project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
            revision INTEGER NOT NULL CHECK(revision >= 1),
            state TEXT NOT NULL CHECK(state IN ('ready','stale')),
            settings TEXT NOT NULL, message TEXT)""")
        connection.execute("""CREATE TABLE IF NOT EXISTS caption_tracks (
            project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
            revision INTEGER NOT NULL CHECK(revision >= 1),
            state TEXT NOT NULL CHECK(state IN ('ready','stale')),
            track TEXT NOT NULL, message TEXT)""")
        connection.execute("""CREATE TABLE IF NOT EXISTS framing_settings (
            project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
            revision INTEGER NOT NULL CHECK(revision >= 1), settings TEXT NOT NULL)""")
        connection.execute("""CREATE TABLE IF NOT EXISTS project_fonts (
            project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
            revision INTEGER NOT NULL CHECK(revision >= 1),
            metadata TEXT NOT NULL, content BLOB NOT NULL)""")
        connection.execute("""CREATE TABLE IF NOT EXISTS caption_font_matches (
            project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
            revision INTEGER NOT NULL CHECK(revision >= 1), selection TEXT NOT NULL)""")
        connection.execute("""CREATE TABLE IF NOT EXISTS caption_appearance_suggestions (
            project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
            revision INTEGER NOT NULL CHECK(revision >= 1), suggestion TEXT NOT NULL
        )""")
        connection.execute("""CREATE TABLE IF NOT EXISTS caption_motion_suggestions (
            project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
            revision INTEGER NOT NULL CHECK(revision >= 1), suggestion TEXT NOT NULL)""")
        connection.execute("""CREATE TABLE IF NOT EXISTS footage_clips (
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            id TEXT NOT NULL, filename TEXT NOT NULL, metadata TEXT NOT NULL,
            sha256 TEXT NOT NULL, saved_at TEXT NOT NULL, name TEXT NOT NULL,
            PRIMARY KEY(project_id,id))""")
        connection.execute("""CREATE TABLE IF NOT EXISTS clip_names (
            project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
            name TEXT NOT NULL)""")
        connection.execute("""CREATE TABLE IF NOT EXISTS sequences (
            project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
            revision INTEGER NOT NULL, sequence TEXT NOT NULL)""")
        connection.execute("""CREATE TABLE IF NOT EXISTS candidate_cache (
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            source_hash TEXT NOT NULL, algorithm TEXT NOT NULL, analysis TEXT NOT NULL,
            PRIMARY KEY(project_id,source_hash,algorithm))""")
        if "completed" not in {
            r[1] for r in connection.execute("PRAGMA table_info(assembly_operations)")
        }:
            connection.execute(
                "ALTER TABLE assembly_operations ADD COLUMN completed INTEGER NOT NULL DEFAULT 0"
            )
            connection.execute(
                "ALTER TABLE assembly_operations ADD COLUMN total INTEGER NOT NULL DEFAULT 0"
            )
        connection.execute("""CREATE TABLE IF NOT EXISTS assembly_proposals (
            project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
            proposal TEXT NOT NULL)""")
        connection.execute("""CREATE TABLE IF NOT EXISTS visual_cache (
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            source_hash TEXT NOT NULL, algorithm TEXT NOT NULL, analysis TEXT NOT NULL,
            PRIMARY KEY(project_id,source_hash,algorithm))""")
        connection.execute("PRAGMA user_version = 19")
    # Only generated staging names are ours. Unknown files are left untouched.
    for path in staging.glob("clip-*"):
        try:
            media_path(str(uuid.uuid4()), path.name)
            path.unlink()
        except ReferenceError:
            continue
        except OSError:
            logger.exception("Could not clean abandoned project staging file")
            raise ReferenceError(
                500, "cleanup_failure", "Abandoned upload cleanup failed."
            ) from None
    with database() as connection:
        connection.execute("DELETE FROM clips WHERE state = 'staging'")
        records = connection.execute("SELECT * FROM clips").fetchall()
    for clip in records:
        valid = False
        try:
            path = media_path(clip["project_id"], clip["filename"])
            metadata = json.loads(clip["metadata"])
            ClipDetails(**metadata)
            if path.is_file() and path.stat().st_size == metadata["size_bytes"]:
                with path.open("rb") as file:
                    valid = hashlib.file_digest(file, "sha256").hexdigest() == clip["sha256"]
        except (OSError, ValueError, TypeError, ReferenceError):
            logger.exception("Retained clip unavailable during reconciliation")
        with database() as connection:
            connection.execute(
                "UPDATE clips SET state = ? WHERE project_id = ?",
                ("ready" if valid else "unavailable", clip["project_id"]),
            )
    # Interrupted deletion is visible and retryable; never conceal cleanup failures.
    # No orphan directory is exposed as a ready clip.


async def storage_call(function, *args):
    try:
        return await finish_thread(function, *args)
    except (sqlite3.Error, OSError):
        logger.exception("Local project storage operation failed")
        raise ReferenceError(
            500, "storage_failure", "Local project storage is unavailable. Please retry."
        ) from None


async def create_project(request: ProjectRequest):
    reference = await inspect_reference(request.reference_url)
    async with operation_lock:
        return await storage_call(insert_project, request.name, reference)


async def upload_clip(project_id: str, request: Request):
    identifier(project_id)
    if operation_lock.locked():
        raise ReferenceError(
            503, "inspection_busy", "A project operation is running. Please retry shortly."
        )
    async with operation_lock:
        from clip_library import budget

        remaining = await storage_call(budget, project_id)
        try:
            await storage_call(begin_upload, project_id)
        except asyncio.CancelledError:
            await storage_call(abort_upload, project_id)
            raise
        try:
            async with staged_clip(request, DATA_DIR / "project-staging", remaining) as (
                details,
                path,
                digest,
            ):
                await storage_call(retain_clip, project_id, path, details, digest)
            return await storage_call(get_project, project_id)
        except BaseException:
            await storage_call(abort_upload, project_id)
            raise


async def remove_project(project_id: str):
    identifier(project_id)
    async with operation_lock:
        await storage_call(delete_project, project_id)
