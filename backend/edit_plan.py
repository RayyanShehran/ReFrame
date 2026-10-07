"""Saved timing suggestions on the renderer's 30 fps grid; no semantic selection."""

from datetime import datetime
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Annotated, Literal

from pydantic import Field, ValidationError, model_validator

import color_analysis as color
import color_recipe as recipe
import footage_analysis as footage
import pacing_analysis as pacing
import projects
from references import ReferenceError

ALGORITHM = "reference-timing-grid-v1"
FPS = 30
MAX_SEGMENTS = 60


def frames(seconds, rounding=ROUND_HALF_UP):
    return int((Decimal(str(seconds)) * FPS).to_integral_value(rounding=rounding))


class Segment(color.Schema):
    source_start_frame: int = Field(ge=0, le=3600, strict=True)
    source_end_frame: int = Field(gt=0, le=3600, strict=True)
    output_start_frame: int = Field(ge=0, le=3600, strict=True)
    output_end_frame: int = Field(gt=0, le=3600, strict=True)


class Plan(color.Schema):
    schema_version: Literal[1] = 1
    generation_algorithm_version: str = ALGORITHM
    fps: Literal[30] = FPS
    pacing: recipe.ReferenceBinding
    footage: footage.Source
    footage_frames: int = Field(gt=0, le=3600, strict=True)
    requested_duration_seconds: float = Field(gt=0, le=120, allow_inf_nan=False)
    output_frames: int = Field(gt=0, le=3600, strict=True)
    segments: list[Segment] = Field(min_length=1, max_length=MAX_SEGMENTS)
    suggested_source_starts: list[int] = Field(min_length=1, max_length=MAX_SEGMENTS)
    merged_subframe_intervals: int = Field(ge=0)
    revision: int = Field(ge=1, strict=True)
    created_at: datetime
    generated_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def consistent(self):
        output_end = source_end = 0
        if len(self.suggested_source_starts) != len(self.segments):
            raise ValueError("Invalid suggested ranges")
        suggested_end = 0
        for segment, suggested in zip(self.segments, self.suggested_source_starts):
            length = segment.output_end_frame - segment.output_start_frame
            if (
                length <= 0
                or segment.output_start_frame != output_end
                or segment.source_end_frame - segment.source_start_frame != length
                or segment.source_start_frame < source_end
                or segment.source_end_frame > self.footage_frames
                or isinstance(suggested, bool)
                or suggested < suggested_end
                or suggested + length > self.footage_frames
            ):
                raise ValueError("Invalid ordered, nonoverlapping frame ranges")
            output_end, source_end = segment.output_end_frame, segment.source_end_frame
            suggested_end = suggested + length
        if output_end != self.output_frames or self.output_frames != frames(
            self.requested_duration_seconds, ROUND_FLOOR
        ):
            raise ValueError("Invalid output duration")
        return self


class Result(color.Schema):
    status: Literal["empty", "ready", "stale"]
    plan: Plan | None = None
    message: str | None = None
    max_duration_seconds: float | None = None


class GenerateRequest(color.Schema):
    expected_revision: int = Field(ge=0, strict=True)
    replace: bool = Field(default=False, strict=True)
    output_duration_seconds: float | None = Field(
        default=None, gt=0, le=120, allow_inf_nan=False, strict=True
    )


class SaveRequest(color.Schema):
    expected_revision: int = Field(ge=1, strict=True)
    source_starts_seconds: list[
        Annotated[float, Field(ge=0, le=120, allow_inf_nan=False, strict=True)]
    ] = Field(min_length=1, max_length=MAX_SEGMENTS)


def bindings(project_id, *, stop=None, deadline=None):
    operation = pacing.get_operation(project_id, True, stop=stop, deadline=deadline)
    if operation.status != "ready":
        raise ReferenceError(409, "pacing_not_ready", "Analyze valid reference pacing first.")
    source = footage.source(project_id, stop, deadline)
    clip = projects.get_project(project_id).clip
    blueprint = operation.blueprint
    binding = recipe.ReferenceBinding(
        source=blueprint.source,
        operation_id=operation.operation_id,
        schema_version=blueprint.schema_version,
        algorithm_version=blueprint.algorithm_version,
        analyzed_at=blueprint.analyzed_at,
    )
    return blueprint, binding, source["identity"], clip.duration_seconds


def ranges(lengths, requested, footage_duration):
    """Floor total duration; merge subframes before half-up cumulative boundaries."""
    total = frames(requested, ROUND_FLOOR)
    if total < 1:
        raise ReferenceError(422, "duration_too_short", "Choose at least one output frame.")
    remaining, groups, pending, merged = Decimal(str(requested)), [], Decimal(0), 0
    for value in lengths:
        if remaining <= 0:
            break
        length = min(Decimal(str(value)), remaining)
        remaining -= length
        if length * FPS < 1:
            merged += 1
            if groups:
                groups[-1] += length
            else:
                pending += length
        else:
            groups.append(pending + length)
            pending = Decimal(0)
    if pending:
        groups.append(pending)
    # Floating analysis durations may sum within a few ulps of the requested end.
    if remaining > Decimal("0.00000001"):
        raise ReferenceError(409, "pacing_invalid", "Pacing does not cover the requested duration.")
    boundaries, elapsed = [0], Decimal(0)
    for length in groups:
        elapsed += length
        boundary = min(total, int((elapsed * FPS).to_integral_value(rounding=ROUND_HALF_UP)))
        if boundary > boundaries[-1]:
            boundaries.append(boundary)
        else:
            merged += 1
    if boundaries[-1] != total:
        boundaries.append(total)
    count = len(boundaries) - 1
    if count > MAX_SEGMENTS:
        raise ReferenceError(
            422,
            "plan_too_complex",
            "This duration needs more than 60 segments. Choose a shorter duration.",
        )
    available = frames(footage_duration, ROUND_FLOOR)
    unused = available - total
    if unused < 0:
        raise ReferenceError(422, "duration_out_of_range", "Output cannot exceed footage duration.")
    segments = []
    for i, (start, end) in enumerate(zip(boundaries, boundaries[1:])):
        source_start = start + (unused // 2 if count == 1 else i * unused // (count - 1))
        segments.append(
            Segment(
                source_start_frame=source_start,
                source_end_frame=source_start + end - start,
                output_start_frame=start,
                output_end_frame=end,
            )
        )
    return segments, merged, available, total


def record(project_id):
    projects.identifier(project_id)
    with projects.database() as connection:
        project = projects.row_project(connection, project_id)
        if project["status"] != "active":
            raise ReferenceError(409, "project_deleting", "Retry project deletion first.")
        return connection.execute(
            "SELECT * FROM edit_plans WHERE project_id=?", (project_id,)
        ).fetchone()


def read(project_id):
    row = record(project_id)
    plan = Plan.model_validate_json(row["plan"]) if row else None
    message = row["message"] if row else None
    maximum = None
    try:
        blueprint, binding, source, duration = bindings(project_id)
        maximum = min(blueprint.duration_seconds, duration)
        if plan and (
            plan.pacing != binding
            or plan.footage != source
            or plan.generation_algorithm_version != ALGORITHM
        ):
            raise ReferenceError(
                409, "plan_stale", "Sources or pacing changed. Regenerate explicitly."
            )
    except ReferenceError as exc:
        if exc.status_code == 404:
            raise
        message = exc.message
        if row:
            with projects.database() as connection:
                connection.execute(
                    "UPDATE edit_plans SET state='stale',message=? "
                    "WHERE project_id=? AND revision=?",
                    (message, project_id, plan.revision),
                )
    status = "empty" if not row else "stale" if message or row["state"] == "stale" else "ready"
    return Result(status=status, plan=plan, message=message, max_duration_seconds=maximum)


def revision_check(row, expected):
    if (row["revision"] if row else 0) != expected:
        raise ReferenceError(
            409,
            "revision_conflict",
            "Cut plan changed elsewhere. Your unsaved edits are retained; reload explicitly.",
        )


def write(project_id, plan, expected):
    with projects.database() as connection:
        project = projects.row_project(connection, project_id)
        if project["status"] != "active":
            raise ReferenceError(409, "project_deleting", "Retry project deletion first.")
        row = connection.execute(
            "SELECT revision FROM edit_plans WHERE project_id=?", (project_id,)
        ).fetchone()
        revision_check(row, expected)
        connection.execute(
            "INSERT INTO edit_plans(project_id,revision,state,plan) VALUES (?,?,'ready',?) "
            "ON CONFLICT(project_id) DO UPDATE SET revision=excluded.revision,"
            "state='ready',plan=excluded.plan,message=NULL",
            (project_id, plan.revision, plan.model_dump_json()),
        )
        connection.execute(
            "UPDATE projects SET updated_at=? WHERE id=?", (projects.now(), project_id)
        )


def generate(project_id, request):
    row = record(project_id)
    revision_check(row, request.expected_revision)
    if row and not request.replace:
        raise ReferenceError(
            409, "plan_exists", "Regenerate explicitly to replace the saved cut plan and edits."
        )
    from grading import read_settings

    if read_settings(project_id).mode == "basic" and recipe.read(project_id).status != "ready":
        raise ReferenceError(
            409, "recipe_not_ready", "Save a valid color recipe before generating cuts."
        )
    blueprint, binding, source, duration = bindings(project_id)
    maximum = min(blueprint.duration_seconds, duration)
    requested = (
        request.output_duration_seconds if request.output_duration_seconds is not None else maximum
    )
    if requested > maximum:
        raise ReferenceError(
            422,
            "duration_out_of_range",
            "Output duration cannot exceed reference or footage duration.",
        )
    segments, merged, available, total = ranges(
        [s.duration_seconds for s in blueprint.shots], requested, duration
    )
    timestamp = projects.now()
    previous = Plan.model_validate_json(row["plan"]) if row else None
    plan = Plan(
        pacing=binding,
        footage=source,
        footage_frames=available,
        requested_duration_seconds=requested,
        output_frames=total,
        segments=segments,
        suggested_source_starts=[s.source_start_frame for s in segments],
        merged_subframe_intervals=merged,
        revision=request.expected_revision + 1,
        created_at=previous.created_at if previous else timestamp,
        generated_at=timestamp,
        updated_at=timestamp,
    )
    write(project_id, plan, request.expected_revision)
    return read(project_id)


def save(project_id, request):
    row = record(project_id)
    revision_check(row, request.expected_revision)
    result = read(project_id)
    if result.status != "ready":
        raise ReferenceError(
            409, "plan_stale", "Restore valid sources and regenerate the cut plan explicitly."
        )
    plan = result.plan
    if len(request.source_starts_seconds) != len(plan.segments):
        raise ReferenceError(422, "invalid_ranges", "Provide one source start for each segment.")
    segments = []
    try:
        for segment, seconds in zip(plan.segments, request.source_starts_seconds):
            start = frames(seconds)
            segments.append(
                segment.model_dump()
                | {
                    "source_start_frame": start,
                    "source_end_frame": start
                    + segment.output_end_frame
                    - segment.output_start_frame,
                }
            )
        updated = Plan.model_validate(
            plan.model_dump()
            | {
                "segments": segments,
                "revision": request.expected_revision + 1,
                "updated_at": projects.now(),
            }
        )
    except (ValidationError, ValueError, ArithmeticError):
        raise ReferenceError(
            422,
            "invalid_ranges",
            "Source ranges must fit the footage in chronological order without overlap.",
        ) from None
    write(project_id, updated, request.expected_revision)
    return read(project_id)
