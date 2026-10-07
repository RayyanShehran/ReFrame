"""Explicit, frame-grid multi-source sequences. No automatic footage selection."""

import uuid
from decimal import ROUND_FLOOR
from typing import Literal

from pydantic import Field, model_validator

import clip_library
import color_analysis as color
import color_recipe as recipe
import edit_plan
import pacing_analysis
import projects
from references import ReferenceError

ALGORITHM = "reference-slots-30fps-v1"


class Assignment(color.Schema):
    id: uuid.UUID
    duration_frames: int = Field(ge=1, le=3600, strict=True)
    clip_id: uuid.UUID | None = None
    source_start_frame: int = Field(default=0, ge=0, le=3600, strict=True)
    reference_start_frame: int | None = Field(default=None, ge=0, le=3599, strict=True)
    reference_end_frame: int | None = Field(default=None, ge=1, le=3600, strict=True)

    @model_validator(mode="after")
    def reference_interval(self):
        if (self.reference_start_frame is None) != (self.reference_end_frame is None):
            raise ValueError("Provide both reference interval boundaries, or neither.")
        if (
            self.reference_start_frame is not None
            and self.reference_start_frame >= self.reference_end_frame
        ):
            raise ValueError("Reference interval must have positive duration.")
        return self


class Slot(Assignment):
    source_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    source_end_frame: int = Field(ge=1, le=7200, strict=True)
    output_start_frame: int = Field(ge=0, le=3600, strict=True)
    output_end_frame: int = Field(ge=1, le=3600, strict=True)

    @model_validator(mode="after")
    def coherent(self):
        if (
            self.source_end_frame != self.source_start_frame + self.duration_frames
            or self.output_end_frame != self.output_start_frame + self.duration_frames
        ):
            raise ValueError("Slot boundaries must match their duration.")
        if (self.clip_id is None) != (self.source_hash is None):
            raise ValueError("Assigned clips require a source hash.")
        return self


class Sequence(color.Schema):
    schema_version: Literal[1] = 1
    algorithm_version: Literal["reference-slots-30fps-v1"] = ALGORITHM
    revision: int = Field(ge=1, strict=True)
    pacing: recipe.ReferenceBinding
    slots: list[Slot] = Field(min_length=1, max_length=60)
    output_frames: int = Field(ge=1, le=3600, strict=True)
    created_at: str
    updated_at: str

    @model_validator(mode="after")
    def contiguous(self):
        cursor = 0
        ids = set()
        for slot in self.slots:
            if slot.id in ids or slot.output_start_frame != cursor:
                raise ValueError("Slots need unique IDs and contiguous output boundaries.")
            ids.add(slot.id)
            cursor = slot.output_end_frame
        if cursor != self.output_frames:
            raise ValueError("Sequence duration does not match its slots.")
        return self


class Result(color.Schema):
    status: Literal["empty", "incomplete", "ready", "stale"]
    sequence: Sequence | None = None
    message: str | None = None


class Generate(color.Schema):
    expected_revision: int = Field(ge=0, strict=True)
    replace: bool = False


class Save(color.Schema):
    expected_revision: int = Field(ge=1, strict=True)
    slots: list[Assignment] = Field(min_length=1, max_length=60)


def record(project_id):
    projects.get_project(project_id)
    with projects.database() as db:
        return db.execute("SELECT * FROM sequences WHERE project_id=?", (project_id,)).fetchone()


def pacing_binding(project_id):
    result = pacing_analysis.get_operation(project_id, True)
    if result.status != "ready" or not result.blueprint:
        raise ReferenceError(
            409, "pacing_required", "Analyze the retained reference's pacing first."
        )
    blueprint = result.blueprint
    return blueprint, recipe.ReferenceBinding(
        operation_id=result.operation_id,
        source=blueprint.source,
        schema_version=blueprint.schema_version,
        algorithm_version=blueprint.algorithm_version,
        analyzed_at=blueprint.analyzed_at,
    )


def validate(project_id, sequence, *, stop=None, deadline=None):
    blueprint, binding = pacing_binding(project_id)
    if binding != sequence.pacing:
        raise ReferenceError(
            409, "sequence_stale", "Reference pacing changed. Regenerate slots explicitly."
        )
    sources = {}
    for slot in sequence.slots:
        if slot.reference_end_frame is not None and slot.reference_end_frame > edit_plan.frames(
            blueprint.duration_seconds
        ):
            raise ReferenceError(
                422,
                "reference_interval_invalid",
                "Reference interval exceeds retained reference duration.",
            )
        if slot.clip_id is None:
            continue
        if slot.clip_id not in sources:
            sources[slot.clip_id] = clip_library.source(
                project_id, slot.clip_id, stop=stop, deadline=deadline
            )
        _, clip = sources[slot.clip_id]
        if clip.sha256 != slot.source_hash:
            raise ReferenceError(
                409,
                "sequence_stale",
                "An assigned clip changed. Replace its assignment explicitly.",
            )
        if slot.source_end_frame > edit_plan.frames(clip.metadata.duration_seconds, ROUND_FLOOR):
            raise ReferenceError(
                422,
                "range_too_short",
                "The source cannot supply the full slot. Choose another start, "
                "shorten the slot, or replace its clip.",
            )
    return sources


def read(project_id):
    row = record(project_id)
    if not row:
        return Result(status="empty")
    sequence = Sequence.model_validate_json(row["sequence"])
    try:
        validate(project_id, sequence)
    except ReferenceError as exc:
        return Result(status="stale", sequence=sequence, message=exc.message)
    ready = all(s.clip_id for s in sequence.slots)
    return Result(
        status="ready" if ready else "incomplete",
        sequence=sequence,
        message=None if ready else "Assign a source clip to every slot before rendering.",
    )


def check(row, expected):
    if (row["revision"] if row else 0) != expected:
        raise ReferenceError(
            409,
            "revision_conflict",
            "Sequence changed elsewhere. Reload explicitly; your unsaved draft is retained.",
        )


def write(project_id, sequence, expected):
    with projects.database() as db:
        project = projects.row_project(db, project_id)
        if project["status"] != "active":
            raise ReferenceError(409, "project_deleting", "Retry deletion first.")
        row = db.execute(
            "SELECT revision FROM sequences WHERE project_id=?", (project_id,)
        ).fetchone()
        check(row, expected)
        db.execute(
            "INSERT INTO sequences VALUES(?,?,?) ON CONFLICT(project_id) DO UPDATE SET "
            "revision=excluded.revision,sequence=excluded.sequence",
            (project_id, sequence.revision, sequence.model_dump_json()),
        )
        db.execute("UPDATE projects SET updated_at=? WHERE id=?", (projects.now(), project_id))


def generate(project_id, request):
    row = record(project_id)
    check(row, request.expected_revision)
    if row and not request.replace:
        raise ReferenceError(
            409, "sequence_exists", "Confirm regeneration before replacing saved slots and edits."
        )
    blueprint, binding = pacing_binding(project_id)
    segments, _, _, total = edit_plan.ranges(
        [s.duration_seconds for s in blueprint.shots],
        blueprint.duration_seconds,
        blueprint.duration_seconds,
    )
    previous = Sequence.model_validate_json(row["sequence"]) if row else None
    timestamp = projects.now()
    sequence = Sequence(
        revision=request.expected_revision + 1,
        pacing=binding,
        output_frames=total,
        slots=[
            Slot(
                id=uuid.uuid4(),
                duration_frames=s.output_end_frame - s.output_start_frame,
                source_start_frame=0,
                reference_start_frame=s.output_start_frame,
                reference_end_frame=s.output_end_frame,
                source_end_frame=s.output_end_frame - s.output_start_frame,
                output_start_frame=s.output_start_frame,
                output_end_frame=s.output_end_frame,
            )
            for s in segments
        ],
        created_at=previous.created_at if previous else timestamp,
        updated_at=timestamp,
    )
    write(project_id, sequence, request.expected_revision)
    return read(project_id)


def save(project_id, request):
    row = record(project_id)
    check(row, request.expected_revision)
    if not row:
        raise ReferenceError(409, "sequence_required", "Create slots from reference pacing first.")
    previous = Sequence.model_validate_json(row["sequence"])
    _, binding = pacing_binding(project_id)
    if binding != previous.pacing:
        raise ReferenceError(409, "sequence_stale", "Regenerate after reference pacing changes.")
    if (
        len({s.id for s in request.slots}) != len(request.slots)
        or sum(s.duration_frames for s in request.slots) > 3600
    ):
        raise ReferenceError(
            422, "invalid_sequence", "Use unique slots totaling at most 120 seconds."
        )
    slots, cursor = [], 0
    sources = {}
    for assignment in request.slots:
        # Legacy clients omit these fields; preserve provenance by stable slot ID, never index.
        old = next((s for s in previous.slots if s.id == assignment.id), None)
        if (
            old
            and not {"reference_start_frame", "reference_end_frame"} & assignment.model_fields_set
        ):
            assignment = assignment.model_copy(
                update={
                    "reference_start_frame": old.reference_start_frame,
                    "reference_end_frame": old.reference_end_frame,
                }
            )
        source_hash = None
        if assignment.clip_id:
            if assignment.clip_id not in sources:
                sources[assignment.clip_id] = clip_library.source(project_id, assignment.clip_id)[1]
            source_hash = sources[assignment.clip_id].sha256
        slots.append(
            Slot(
                **assignment.model_dump(),
                source_hash=source_hash,
                source_end_frame=assignment.source_start_frame + assignment.duration_frames,
                output_start_frame=cursor,
                output_end_frame=cursor + assignment.duration_frames,
            )
        )
        cursor += assignment.duration_frames
    try:
        sequence = Sequence(
            **(
                previous.model_dump()
                | {
                    "slots": slots,
                    "output_frames": cursor,
                    "revision": request.expected_revision + 1,
                    "updated_at": projects.now(),
                }
            )
        )
    except ValueError:
        raise ReferenceError(
            422, "invalid_sequence", "Use unique slots totaling at most 120 seconds."
        ) from None
    validate(project_id, sequence)
    write(project_id, sequence, request.expected_revision)
    return read(project_id)
