"""Saved user-footage color, sharing the reference algorithm and lifecycle."""

import sys
from functools import partial
from uuid import UUID

from pydantic import Field

import clips
import color_analysis as color
import projects
from references import ReferenceError

ALGORITHM = color.ALGORITHM
TABLE = "footage_color_operations"
STAGING = "footage-color-staging"
TOTAL_SECONDS = color.TOTAL_SECONDS


class Source(color.Schema):
    project_id: UUID
    clip_id: str = Field(pattern=r"^clip-[0-9a-f]{32}\.(mp4|mov)$")
    media_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class Blueprint(color.Blueprint):
    source: Source


class Operation(color.Operation):
    blueprint: Blueprint | None = None


digest = partial(color.digest, max_bytes=clips.MAX_FILE)


def source(project_id, stop=None, deadline=None):
    try:
        project = projects.get_project(project_id)
    except OSError:
        raise ReferenceError(409, "source_unavailable", "Saved footage is unavailable.") from None
    if project.clip_status != "ready" or not project.clip:
        raise ReferenceError(409, "footage_not_ready", "Upload valid footage before analysis.")
    with projects.database() as connection:
        row = connection.execute("SELECT * FROM clips WHERE project_id=?", (project_id,)).fetchone()
    path = projects.media_path(project_id, row["filename"])
    if digest(path, stop, deadline) != project.clip.sha256:
        raise ReferenceError(409, "source_changed", "Saved footage no longer matches its hash.")
    return {
        "path": path,
        "reference_operation_id": row["filename"],  # Existing lifecycle's source-generation token.
        "identity": Source(
            project_id=project_id, clip_id=row["filename"], media_sha256=project.clip.sha256
        ),
    }


component = sys.modules[__name__]
staging = partial(color.staging, component=component)
clean_stage = partial(color.clean_stage, component=component)
fail_operation = partial(color.fail_operation, component=component)
get_operation = partial(color.get_operation, component=component)
begin_operation = partial(color.begin_operation, component=component)
commit = partial(color.commit, component=component)
prepare_delete = partial(color.prepare_delete, component=component)
recover = partial(color.recover, component=component)
pipeline = partial(color.pipeline, component=component)
compensate = color.compensate
