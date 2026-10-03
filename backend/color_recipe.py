"""Editable creative settings; measured analyses remain immutable."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

import color_analysis as color
import footage_analysis as footage
import projects
from references import ReferenceError

ALGORITHM = "sampled-color-ratios-v1"


class Values(color.Schema):
    brightness: float = Field(default=0, ge=-0.2, le=0.2, allow_inf_nan=False, strict=True)
    contrast: float = Field(default=1, ge=0.5, le=1.5, allow_inf_nan=False, strict=True)
    saturation: float = Field(default=1, ge=0.5, le=1.5, allow_inf_nan=False, strict=True)


class SuggestedValues(Values):
    brightness: float = Field(ge=-0.1, le=0.1, allow_inf_nan=False, strict=True)
    contrast: float = Field(ge=0.8, le=1.2, allow_inf_nan=False, strict=True)
    saturation: float = Field(ge=0.8, le=1.2, allow_inf_nan=False, strict=True)


class ReferenceBinding(color.Schema):
    source: color.Source
    operation_id: UUID
    schema_version: int = Field(ge=1)
    algorithm_version: str
    analyzed_at: datetime


class FootageBinding(ReferenceBinding):
    source: footage.Source


class Recipe(color.Schema):
    schema_version: Literal[1] = 1
    suggestion_algorithm_version: str = ALGORITHM
    reference: ReferenceBinding
    footage: FootageBinding
    suggested: SuggestedValues
    selected: Values
    suggested_strength: Literal[0.5] = 0.5
    strength: float = Field(ge=0, le=1, allow_inf_nan=False, strict=True)
    explanations: list[str] = Field(max_length=2)
    revision: int = Field(ge=1, strict=True)
    created_at: datetime
    generated_at: datetime
    updated_at: datetime


class Result(color.Schema):
    status: Literal["empty", "ready", "stale"]
    recipe: Recipe | None = None
    message: str | None = None
    effective: Values | None = None


class GenerateRequest(color.Schema):
    expected_revision: int = Field(ge=0, strict=True)
    replace: bool = Field(default=False, strict=True)


class SaveRequest(color.Schema):
    expected_revision: int = Field(ge=1, strict=True)
    selected: Values
    strength: float = Field(ge=0, le=1, allow_inf_nan=False, strict=True)


def suggest(reference, clip):
    explanations = []

    def ratio(name, numerator, denominator):
        if denominator < 0.02:
            explanations.append(
                f"Insufficient footage {name} variation (below 0.02); multiplier is neutral."
            )
            return 1.0
        return max(0.8, min(1.2, numerator / denominator))

    return SuggestedValues(
        brightness=max(-0.1, min(0.1, reference.brightness_p50 - clip.brightness_p50)),
        contrast=ratio("contrast", reference.contrast_spread, clip.contrast_spread),
        saturation=ratio("saturation", reference.mean_hsv_saturation, clip.mean_hsv_saturation),
    ), explanations


def effective(selected, strength):
    return Values(
        brightness=strength * selected.brightness,
        contrast=1 + strength * (selected.contrast - 1),
        saturation=1 + strength * (selected.saturation - 1),
    )


def bindings(project_id, *, stop=None, deadline=None):
    reference = color.get_operation(project_id, True, stop=stop, deadline=deadline)
    clip = footage.get_operation(project_id, True, stop=stop, deadline=deadline)
    if reference.status != "ready" or clip.status != "ready":
        raise ReferenceError(
            409, "analyses_not_ready", "Analyze valid reference and footage colors first."
        )

    def bind(operation, model):
        b = operation.blueprint
        return model(
            source=b.source,
            operation_id=operation.operation_id,
            schema_version=b.schema_version,
            algorithm_version=b.algorithm_version,
            analyzed_at=b.analyzed_at,
        )

    return (
        reference.blueprint.color,
        clip.blueprint.color,
        bind(reference, ReferenceBinding),
        bind(clip, FootageBinding),
    )


def record(project_id):
    projects.identifier(project_id)
    with projects.database() as connection:
        project = projects.row_project(connection, project_id)
        if project["status"] != "active":
            raise ReferenceError(409, "project_deleting", "Retry project deletion first.")
        return connection.execute(
            "SELECT * FROM color_recipes WHERE project_id=?", (project_id,)
        ).fetchone()


def read(project_id):
    row = record(project_id)
    if not row:
        return Result(status="empty")
    recipe = Recipe.model_validate_json(row["recipe"])
    message = row["message"]
    if row["state"] == "ready":
        try:
            _, _, reference, clip = bindings(project_id)
            if (
                recipe.reference != reference
                or recipe.footage != clip
                or recipe.suggestion_algorithm_version != ALGORITHM
            ):
                raise ReferenceError(
                    409,
                    "recipe_stale",
                    "Sources or analysis versions changed. Regenerate explicitly.",
                )
        except ReferenceError as exc:
            if exc.status_code == 404:
                raise
            message = "Recipe is stale. " + exc.message
            with projects.database() as connection:
                connection.execute(
                    "UPDATE color_recipes SET state='stale',message=? "
                    "WHERE project_id=? AND revision=?",
                    (message, project_id, recipe.revision),
                )
    status = "stale" if message or row["state"] == "stale" else "ready"
    return Result(
        status=status,
        recipe=recipe,
        message=message,
        effective=effective(recipe.selected, recipe.strength) if status == "ready" else None,
    )


def revision_check(row, expected):
    if (row["revision"] if row else 0) != expected:
        raise ReferenceError(
            409,
            "revision_conflict",
            "Recipe changed elsewhere. Reload it before saving or regenerating.",
        )


def write(project_id, recipe, expected):
    with projects.database() as connection:
        project = projects.row_project(connection, project_id)
        if project["status"] != "active":
            raise ReferenceError(409, "project_deleting", "Retry project deletion first.")
        row = connection.execute(
            "SELECT revision FROM color_recipes WHERE project_id=?", (project_id,)
        ).fetchone()
        revision_check(row, expected)
        connection.execute(
            "INSERT INTO color_recipes(project_id,revision,state,recipe) VALUES (?,?,'ready',?) "
            "ON CONFLICT(project_id) DO UPDATE SET revision=excluded.revision,"
            "state='ready',recipe=excluded.recipe,message=NULL",
            (project_id, recipe.revision, recipe.model_dump_json()),
        )
        connection.execute(
            "UPDATE projects SET updated_at=? WHERE id=?", (projects.now(), project_id)
        )


def generate(project_id, request):
    row = record(project_id)
    revision_check(row, request.expected_revision)
    if row and not request.replace:
        raise ReferenceError(
            409, "recipe_exists", "Regenerate explicitly to replace the saved recipe and edits."
        )
    ref, clip, ref_binding, clip_binding = bindings(project_id)
    suggested, explanations = suggest(ref, clip)
    timestamp = projects.now()
    previous = Recipe.model_validate_json(row["recipe"]) if row else None
    recipe = Recipe(
        reference=ref_binding,
        footage=clip_binding,
        suggested=suggested,
        selected=Values(**suggested.model_dump()),
        strength=0.5,
        explanations=explanations,
        revision=request.expected_revision + 1,
        created_at=previous.created_at if previous else timestamp,
        generated_at=timestamp,
        updated_at=timestamp,
    )
    write(project_id, recipe, request.expected_revision)
    return read(project_id)


def save(project_id, request):
    row = record(project_id)
    revision_check(row, request.expected_revision)
    result = read(project_id)
    if result.status != "ready":
        raise ReferenceError(
            409,
            "recipe_stale",
            "Recipe is stale. Restore valid analyses and regenerate explicitly.",
        )
    recipe = result.recipe.model_copy(
        update={
            "selected": request.selected,
            "strength": request.strength,
            "revision": request.expected_revision + 1,
            "updated_at": datetime.fromisoformat(projects.now()),
        }
    )
    write(project_id, recipe, request.expected_revision)
    return read(project_id)
