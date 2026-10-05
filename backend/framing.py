"""Saved output framing, independent of source analysis and timeline bindings."""

import math
from datetime import datetime
from fractions import Fraction
from typing import Literal

from pydantic import Field

import color_analysis as color
import projects
from references import ReferenceError

FORMATS = {"portrait": (720, 1280), "square": (720, 720), "landscape": (1280, 720)}


class Choices(color.Schema):
    format: Literal["original", "portrait", "square", "landscape"] = "original"
    fit: Literal["fit", "fill"] = "fit"
    horizontal: float = Field(default=0.5, ge=0, le=1, strict=True, allow_inf_nan=False)
    vertical: float = Field(default=0.5, ge=0, le=1, strict=True, allow_inf_nan=False)


class Settings(Choices):
    schema_version: Literal[1] = 1
    revision: int = Field(default=0, ge=0, strict=True)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class Result(color.Schema):
    status: Literal["default", "ready"]
    settings: Settings


class SaveRequest(Choices):
    expected_revision: int = Field(ge=0, strict=True)


def record(project_id, connection):
    projects.identifier(project_id)
    if projects.row_project(connection, project_id)["status"] != "active":
        raise ReferenceError(409, "project_deleting", "Retry project deletion first.")
    return connection.execute(
        "SELECT * FROM framing_settings WHERE project_id=?", (project_id,)
    ).fetchone()


def read(project_id):
    with projects.database() as connection:
        row = record(project_id, connection)
    return Result(
        status="ready" if row else "default",
        settings=Settings.model_validate_json(row["settings"]) if row else Settings(),
    )


def save(project_id, request):
    with projects.database() as connection:
        row = record(project_id, connection)
        if (row["revision"] if row else 0) != request.expected_revision:
            raise ReferenceError(
                409,
                "revision_conflict",
                "Framing changed elsewhere. Reload explicitly; your draft is retained.",
            )
        previous = Settings.model_validate_json(row["settings"]) if row else None
        timestamp = projects.now()
        settings = Settings(
            **request.model_dump(exclude={"expected_revision"}),
            revision=request.expected_revision + 1,
            created_at=previous.created_at if previous else timestamp,
            updated_at=timestamp,
        )
        connection.execute(
            "INSERT INTO framing_settings(project_id,revision,settings) VALUES (?,?,?) "
            "ON CONFLICT(project_id) DO UPDATE SET "
            "revision=excluded.revision,settings=excluded.settings",
            (project_id, settings.revision, settings.model_dump_json()),
        )
        connection.execute("UPDATE projects SET updated_at=? WHERE id=?", (timestamp, project_id))
    return read(project_id)


def display_dimensions(video):
    sar = Fraction(video.get("sample_aspect_ratio", "1:1").replace(":", "/"))
    if not 0 < sar <= 100:
        raise ValueError("Invalid pixel aspect ratio")
    width, height = float(video["width"] * sar), float(video["height"])
    angle = float(
        next(
            (s["rotation"] for s in video.get("side_data_list", []) if "rotation" in s),
            video.get("tags", {}).get("rotate", 0),
        )
    )
    if not math.isfinite(angle) or abs(angle / 90 - round(angle / 90)) > 0.001:
        raise ValueError("Only orthogonal display rotation is supported")
    if round(angle / 90) % 2:
        width, height = height, width
    if min(width, height) < 2:
        raise ValueError("Video dimensions are too small")
    return width, height


def geometry(video, settings):
    """Even square-pixel scale, final canvas and aligned crop position (0..1)."""
    width, height = display_dimensions(video)
    if settings.format == "original":
        maximum = (720, 1280) if height > width else (1280, 720)
        factor = min(1, maximum[0] / width, maximum[1] / height)
        scaled = (int(width * factor) // 2 * 2, int(height * factor) // 2 * 2)
        if min(scaled) < 2:
            raise ValueError("Video dimensions are too small")
        return scaled, scaled, ""
    canvas = FORMATS[settings.format]
    factor = (min if settings.fit == "fit" else max)(canvas[0] / width, canvas[1] / height)
    # Fit rounds down to avoid clipping; Fill rounds up to cover every canvas pixel.
    rounding = math.floor if settings.fit == "fit" else math.ceil
    scaled = tuple(max(2, rounding(size * factor / 2) * 2) for size in (width, height))
    if settings.fit == "fit":
        final = f"pad={canvas[0]}:{canvas[1]}:(ow-iw)/2:(oh-ih)/2:color=black"
    else:
        x = round((scaled[0] - canvas[0]) * settings.horizontal / 2) * 2
        y = round((scaled[1] - canvas[1]) * settings.vertical / 2) * 2
        final = f"crop={canvas[0]}:{canvas[1]}:{x}:{y}"
    return scaled, canvas, final
