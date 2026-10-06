"""Additional retained footage; the original clip stays the analysis/legacy source."""

import json
import uuid

from pydantic import Field, field_validator
from starlette.responses import FileResponse

import clips
import color_analysis as color
import projects
from references import ReferenceError

MAX_CLIPS = 10
MAX_TOTAL = 500 * 1024 * 1024


class Clip(color.Schema):
    id: uuid.UUID
    name: str
    primary: bool
    available: bool
    metadata: clips.ClipDetails
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    saved_at: str


class Library(color.Schema):
    clips: list[Clip]
    total_bytes: int
    max_clips: int = MAX_CLIPS
    max_bytes: int = MAX_TOTAL


class Rename(color.Schema):
    name: str

    @field_validator("name")
    @classmethod
    def valid_name(cls, value):
        value = value.strip()
        if not 1 <= len(value) <= 80 or any(ord(c) < 32 for c in value):
            raise ValueError("Clip names must contain 1–80 printable characters.")
        return value


def records(project_id):
    projects.identifier(project_id)
    with projects.database() as db:
        project = projects.row_project(db, project_id)
        if project["status"] != "active":
            raise ReferenceError(409, "project_deleting", "Retry project deletion first.")
        primary = db.execute("SELECT * FROM clips WHERE project_id=?", (project_id,)).fetchone()
        name = db.execute(
            "SELECT name FROM clip_names WHERE project_id=?", (project_id,)
        ).fetchone()
        extras = db.execute(
            "SELECT * FROM footage_clips WHERE project_id=? ORDER BY saved_at,id", (project_id,)
        ).fetchall()
    result = []
    if primary and primary["filename"] and primary["metadata"]:
        row = dict(primary)
        row.update(
            id=str(uuid.UUID(primary["filename"][5:37])),
            primary=True,
            name=name["name"] if name else json.loads(primary["metadata"])["filename"],
        )
        result.append(row)
    result.extend(dict(row) | {"primary": False} for row in extras)
    return result


def read(project_id):
    result = []
    for row in records(project_id):
        metadata = clips.ClipDetails.model_validate_json(row["metadata"])
        path = projects.media_path(project_id, row["filename"])
        available = path.is_file() and path.stat().st_size == metadata.size_bytes
        result.append(
            Clip(
                id=row["id"],
                name=row["name"],
                primary=row["primary"],
                available=available,
                metadata=metadata,
                sha256=row["sha256"],
                saved_at=row["saved_at"],
            )
        )
    return Library(clips=result, total_bytes=sum(c.metadata.size_bytes for c in result))


def budget(project_id):
    library = read(project_id)
    if len(library.clips) >= MAX_CLIPS:
        raise ReferenceError(
            409,
            "clip_limit",
            "This project already has 10 footage clips. Remove an unassigned clip first.",
        )
    remaining = MAX_TOTAL - library.total_bytes
    if remaining <= 0:
        raise ReferenceError(
            413, "project_storage_limit", "Source footage is limited to 500 MiB per project."
        )
    return min(clips.MAX_FILE, remaining)


def source(project_id, clip_id, *, stop=None, deadline=None):
    clip_id = projects.identifier(str(clip_id))
    row = next((r for r in records(project_id) if r["id"] == clip_id), None)
    if row is None:
        raise ReferenceError(
            409, "clip_unavailable", "The assigned source clip is no longer available."
        )
    path = projects.media_path(project_id, row["filename"])
    metadata = clips.ClipDetails.model_validate_json(row["metadata"])
    if (
        not path.is_file()
        or path.stat().st_size != metadata.size_bytes
        or color.digest(path, stop, deadline, max_bytes=clips.MAX_FILE) != row["sha256"]
    ):
        raise ReferenceError(
            409,
            "clip_changed",
            "An assigned source is missing or changed. Replace the assignment explicitly.",
        )
    return path, Clip(
        id=clip_id,
        name=row["name"],
        primary=row["primary"],
        available=True,
        metadata=metadata,
        sha256=row["sha256"],
        saved_at=row["saved_at"],
    )


def retain(project_id, path, details, digest):
    budget(project_id)
    destination = projects.media_path(project_id, path.name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    clip_id = str(uuid.UUID(path.name[5:37]))
    try:
        path.replace(destination)
        with projects.database() as db:
            db.execute(
                "INSERT INTO footage_clips VALUES(?,?,?,?,?,?,?)",
                (
                    project_id,
                    clip_id,
                    path.name,
                    details.model_dump_json(),
                    digest,
                    projects.now(),
                    details.filename,
                ),
            )
            db.execute("UPDATE projects SET updated_at=? WHERE id=?", (projects.now(), project_id))
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


async def upload(project_id, request):
    if projects.operation_lock.locked():
        raise ReferenceError(
            503, "inspection_busy", "A project operation is running. Retry shortly."
        )
    async with projects.operation_lock:
        remaining = await projects.storage_call(budget, project_id)
        # ponytail: serialize staged uploads with retained-budget checks in this local process.
        library = await projects.storage_call(read, project_id)
        if not library.clips:
            await projects.storage_call(projects.begin_upload, project_id)
            try:
                async with clips.staged_clip(
                    request, projects.DATA_DIR / "project-staging", remaining
                ) as (details, path, digest):
                    await projects.storage_call(
                        projects.retain_clip, project_id, path, details, digest
                    )
            except BaseException:
                await projects.storage_call(projects.abort_upload, project_id)
                raise
        else:
            async with clips.staged_clip(
                request, projects.DATA_DIR / "project-staging", remaining
            ) as (details, path, digest):
                await projects.storage_call(retain, project_id, path, details, digest)
        return await projects.storage_call(read, project_id)


def rename(project_id, clip_id, request):
    row = next((r for r in records(project_id) if r["id"] == projects.identifier(clip_id)), None)
    if row is None:
        raise ReferenceError(404, "clip_not_found", "This clip is unavailable.")
    with projects.database() as db:
        if row["primary"]:
            db.execute(
                "INSERT INTO clip_names VALUES(?,?) ON CONFLICT(project_id) "
                "DO UPDATE SET name=excluded.name",
                (project_id, request.name),
            )
        else:
            db.execute(
                "UPDATE footage_clips SET name=? WHERE project_id=? AND id=?",
                (request.name, project_id, clip_id),
            )
    return read(project_id)


def remove(project_id, clip_id):
    row = next((r for r in records(project_id) if r["id"] == projects.identifier(clip_id)), None)
    if row is None:
        raise ReferenceError(404, "clip_not_found", "This clip is unavailable.")
    with projects.database() as db:
        sequence = db.execute(
            "SELECT sequence FROM sequences WHERE project_id=?", (project_id,)
        ).fetchone()
        if sequence and any(
            s.get("clip_id") == clip_id for s in json.loads(sequence["sequence"])["slots"]
        ):
            raise ReferenceError(
                409, "clip_assigned", "Remove or replace this clip's saved slot assignments first."
            )
        if (
            row["primary"]
            and db.execute("SELECT 1 FROM edit_plans WHERE project_id=?", (project_id,)).fetchone()
        ):
            raise ReferenceError(
                409,
                "clip_assigned",
                "The original clip is used by the legacy cut plan. Keep it or delete the project.",
            )
        path = projects.media_path(project_id, row["filename"])
        path.unlink(missing_ok=True)
        if row["primary"]:
            db.execute("DELETE FROM clips WHERE project_id=?", (project_id,))
            db.execute("DELETE FROM clip_names WHERE project_id=?", (project_id,))
        else:
            db.execute(
                "DELETE FROM footage_clips WHERE project_id=? AND id=?", (project_id, clip_id)
            )
    return read(project_id)


class VideoResponse(FileResponse):
    def __init__(self, project_id, clip_id):
        self.project_id, self.clip_id = project_id, projects.identifier(clip_id)
        super().__init__("", media_type="video/mp4")

    async def __call__(self, scope, receive, send):
        async with projects.operation_lock:
            path, clip = await projects.storage_call(source, self.project_id, self.clip_id)
            self.path = path
            self.media_type = "video/quicktime" if path.suffix == ".mov" else "video/mp4"
            self.headers["content-type"] = self.media_type
            self.headers["Cache-Control"] = "no-store"
            await super().__call__(scope, receive, send)
