"""Revisioned audio choices; all reference provenance is resolved on the server."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

import color_analysis as color
import footage_analysis as footage
import projects
import reference_jobs as jobs
from references import ReferenceError

Mode = Literal["original", "reference", "mix", "mute"]


class Choices(color.Schema):
    mode: Mode = "original"
    original_volume: float = Field(default=100, ge=0, le=100, strict=True, allow_inf_nan=False)
    reference_volume: float = Field(default=100, ge=0, le=100, strict=True, allow_inf_nan=False)
    reference_offset_seconds: float = Field(
        default=0, ge=0, le=120, strict=True, allow_inf_nan=False
    )


class ReferenceBinding(color.Schema):
    source: color.Source
    operation_id: UUID


class Settings(Choices):
    schema_version: Literal[1] = 1
    revision: int = Field(default=0, ge=0, strict=True)
    reference: ReferenceBinding | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @model_validator(mode="after")
    def provenance(self):
        if (self.mode in {"reference", "mix"}) != (self.reference is not None):
            raise ValueError("Reference modes require a source binding")
        return self


class Availability(color.Schema):
    original_has_audio: bool
    reference_has_audio: bool
    reference_duration_seconds: float | None = None


class Result(color.Schema):
    status: Literal["default", "ready", "stale"]
    settings: Settings
    availability: Availability
    message: str | None = None


class SaveRequest(Choices):
    expected_revision: int = Field(ge=0, strict=True)
    refresh_sources: bool = Field(default=False, strict=True)


def availability(project_id):
    project = projects.get_project(project_id)
    reference = jobs.get_operation(project_id, True)
    ready = reference.status == "ready"
    return Availability(
        original_has_audio=bool(project.clip_status == "ready" and project.clip.has_audio),
        reference_has_audio=bool(ready and reference.media.has_audio),
        reference_duration_seconds=reference.media.duration_seconds if ready else None,
    )


def reference_source(project_id, stop=None, deadline=None):
    reference = jobs.get_operation(project_id, True)
    if reference.status != "ready" or not reference.media.has_audio:
        raise ReferenceError(
            409, "reference_audio_unavailable", "Retrieve ready reference media with audio first."
        )
    source = color.source(project_id, stop, deadline)
    binding = ReferenceBinding(
        source=source["identity"], operation_id=source["reference_operation_id"]
    )
    return source["path"], binding, reference.media.duration_seconds


def validate(project_id, choices, *, stop=None, deadline=None):
    binding = None
    if choices.mode in {"reference", "mix"}:
        _, binding, duration = reference_source(project_id, stop, deadline)
        if choices.reference_offset_seconds >= duration:
            raise ReferenceError(
                422,
                "audio_offset_out_of_range",
                "Reference offset must be inside the reference video timeline.",
            )
    if choices.mode == "mix":
        footage.source(project_id, stop, deadline)
        if not availability(project_id).original_has_audio:
            raise ReferenceError(
                409, "original_audio_unavailable", "Mix requires footage audio and reference audio."
            )
    return binding


def record(project_id):
    projects.identifier(project_id)
    with projects.database() as connection:
        project = projects.row_project(connection, project_id)
        if project["status"] != "active":
            raise ReferenceError(409, "project_deleting", "Retry project deletion first.")
        return connection.execute(
            "SELECT * FROM audio_settings WHERE project_id=?", (project_id,)
        ).fetchone()


def read(project_id):
    row = record(project_id)
    available = availability(project_id)
    if not row:
        return Result(status="default", settings=Settings(), availability=available)
    settings = Settings.model_validate_json(row["settings"])
    message = row["message"]
    if row["state"] == "ready":
        try:
            if validate(project_id, settings) != settings.reference:
                raise ReferenceError(409, "audio_stale", "Reference audio source changed.")
        except ReferenceError as exc:
            if exc.status_code == 404:
                raise
            message = exc.message
            with projects.database() as connection:
                connection.execute(
                    "UPDATE audio_settings SET state='stale',message=? "
                    "WHERE project_id=? AND revision=?",
                    (message, project_id, settings.revision),
                )
    return Result(
        status="stale" if message or row["state"] == "stale" else "ready",
        settings=settings,
        availability=available,
        message=message,
    )


def revision_check(row, expected):
    if (row["revision"] if row else 0) != expected:
        raise ReferenceError(
            409,
            "revision_conflict",
            "Audio changed elsewhere. Reload explicitly; your unsaved choices are retained.",
        )


def save(project_id, request):
    row = record(project_id)
    revision_check(row, request.expected_revision)
    previous = Settings.model_validate_json(row["settings"]) if row else None
    binding = validate(project_id, request)
    if (
        binding
        and previous
        and previous.reference
        and (previous.reference != binding or row["state"] == "stale")
        and not request.refresh_sources
    ):
        raise ReferenceError(
            409,
            "audio_stale",
            "Audio sources changed. Reload, then explicitly save with current sources.",
        )
    timestamp = projects.now()
    settings = Settings(
        **request.model_dump(exclude={"expected_revision", "refresh_sources"}),
        reference=binding,
        revision=request.expected_revision + 1,
        created_at=previous.created_at if previous else timestamp,
        updated_at=timestamp,
    )
    with projects.database() as connection:
        project = projects.row_project(connection, project_id)
        if project["status"] != "active":
            raise ReferenceError(409, "project_deleting", "Retry project deletion first.")
        current = connection.execute(
            "SELECT revision FROM audio_settings WHERE project_id=?", (project_id,)
        ).fetchone()
        revision_check(current, request.expected_revision)
        connection.execute(
            "INSERT INTO audio_settings(project_id,revision,state,settings) "
            "VALUES (?,?,'ready',?) ON CONFLICT(project_id) DO UPDATE SET "
            "revision=excluded.revision,state='ready',settings=excluded.settings,message=NULL",
            (project_id, settings.revision, settings.model_dump_json()),
        )
        connection.execute("UPDATE projects SET updated_at=? WHERE id=?", (timestamp, project_id))
    return read(project_id)
