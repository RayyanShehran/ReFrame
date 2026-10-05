"""One atomic project-owned font BLOB; font selection is part of saved captions."""

import hashlib
import json
import re
import shutil
import sys
import time
import unicodedata
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field

import color_analysis as color
import projects
import reference_engine as engine
from references import ReferenceError

MAX_BYTES = 2 * 1024 * 1024
BUNDLED = Path(__file__).resolve().parent / "fonts" / "DejaVuSans.ttf"
BUNDLED_HASH = "7da195a74c55bef988d0d48f9508bd5d849425c1770dba5d7bfc6ce9ed848954"


class Binding(color.Schema):
    kind: Literal["default", "custom"] = "default"
    font_id: UUID | None = None
    sha256: str = Field(default=BUNDLED_HASH, pattern=r"^[0-9a-f]{64}$")
    family: str = Field(default="DejaVu Sans", min_length=1, max_length=128)
    style: str = Field(default="Book", min_length=1, max_length=128)


class Metadata(color.Schema):
    schema_version: Literal[1] = 1
    binding: Binding
    size_bytes: int = Field(gt=0, le=MAX_BYTES)
    codepoints: list[int] = Field(max_length=65535)
    parser_version: str = Field(max_length=30)


class Result(color.Schema):
    revision: int = Field(ge=0)
    font: Binding | None = None
    available: bool = True
    message: str | None = None


def row(project_id):
    projects.identifier(project_id)
    with projects.database() as db:
        project = projects.row_project(db, project_id)
        if project["status"] != "active":
            raise ReferenceError(409, "project_deleting", "Retry deleting this project first.")
        return db.execute(
            "SELECT * FROM project_fonts WHERE project_id=?", (project_id,)
        ).fetchone()


def read(project_id):
    saved = row(project_id)
    if not saved:
        return Result(revision=0)
    metadata = Metadata.model_validate_json(saved["metadata"])
    valid = (
        len(saved["content"]) == metadata.size_bytes
        and hashlib.sha256(saved["content"]).hexdigest() == metadata.binding.sha256
    )
    return Result(
        revision=saved["revision"],
        font=metadata.binding,
        available=valid,
        message=None
        if valid
        else "Custom font bytes are unavailable or changed. Replace explicitly.",
    )


def selected(project_id, choice):
    if choice == "default":
        return Binding()
    saved = read(project_id)
    if not saved.font or not saved.available:
        raise ReferenceError(409, "caption_font_unavailable", "Upload a usable custom font first.")
    return saved.font


@lru_cache(maxsize=1)
def default_codepoints():
    from fontTools.ttLib import TTFont

    # The bundled, hash-checked asset is trusted; uploaded bytes use the owned subprocess.
    with TTFont(BUNDLED) as font:
        return frozenset(font.getBestCmap())


def validate(project_id, binding, cues, *, stop=None, deadline=None):
    deadline = deadline or time.monotonic() + 10
    color.guard(stop, deadline)
    if binding.kind == "default":
        if (
            binding != Binding()
            or not BUNDLED.is_file()
            or color.digest(BUNDLED, stop, deadline, max_bytes=MAX_BYTES) != BUNDLED_HASH
        ):
            raise ReferenceError(
                503, "caption_font_unavailable", "Bundled caption font is unavailable."
            )
        points = default_codepoints()
    else:
        saved = row(project_id)
        if not saved:
            raise ReferenceError(
                409, "caption_font_unavailable", "Selected custom font is unavailable."
            )
        metadata = Metadata.model_validate_json(saved["metadata"])
        if (
            metadata.binding != binding
            or len(saved["content"]) != metadata.size_bytes
            or hashlib.sha256(saved["content"]).hexdigest() != binding.sha256
        ):
            raise ReferenceError(
                409,
                "caption_font_unavailable",
                "Selected custom font changed. Save captions with the current font.",
            )
        points = frozenset(metadata.codepoints)
    absent = sorted(
        {
            ord(c)
            for cue in cues
            for c in cue.text
            if c != "\n" and unicodedata.category(c) != "Cf" and ord(c) not in points
        }
    )
    if absent and binding.kind == "custom":
        raise ReferenceError(
            422,
            "unsupported_font_glyph",
            "Custom font lacks "
            + ", ".join(f"U+{c:04X}" for c in absent[:6])
            + ". Choose another font or edit text; no substitution is used.",
        )
    return (
        ["Default font lacks some cue characters; system fallback or missing symbols may occur."]
        if absent
        else []
    )


def parse(path, directory, deadline, output=None, family=None):
    args = [sys.executable, str(Path(__file__).with_name("font_probe.py")), str(path)]
    if output:
        args += [str(output), family]
    result = engine.run_command(
        args,
        directory,
        "font-parse",
        deadline,
        10,
        output_limit=512 * 1024,
        temp_budget=16 * 1024 * 1024,
    )
    try:
        info = json.loads(result.stdout)
    except (ValueError, TypeError):
        raise ReferenceError(
            422, "invalid_font", "Font structure could not be validated."
        ) from None
    if result.returncode or "error" in info:
        raise ReferenceError(
            422,
            "invalid_font",
            "Unsupported or malformed static font. "
            "Use a Unicode outline TTF/OTF, at most 2 MiB and 20,000 glyphs.",
        )
    return info


def prepare(project_id, binding, cues, directory, deadline):
    warnings = validate(project_id, binding, cues, deadline=deadline)
    if binding.kind == "default":
        return BUNDLED.parent, "DejaVu Sans", warnings
    saved = row(project_id)
    raw = directory / "font-source.bin"
    raw.write_bytes(saved["content"])
    fonts = directory / "selected-font"
    fonts.mkdir(exist_ok=True)
    internal = "ReFrameFont" + binding.sha256[:24]
    parse(raw, directory, deadline, fonts / "selected.ttf", internal)
    return fonts, internal, warnings


def upload(project_id, raw, expected_revision, expected_caption_revision, replace):
    import captions
    import frame_preview

    previous = row(project_id)
    if (previous["revision"] if previous else 0) != expected_revision:
        raise ReferenceError(409, "revision_conflict", "Font changed. Reload explicitly.")
    if previous and not replace:
        raise ReferenceError(
            409, "font_replace_required", "Confirm replacing the current custom font."
        )
    caption = captions.record(project_id)
    captions.revision_check(caption, expected_caption_revision)
    if not 0 < len(raw) <= MAX_BYTES:
        raise ReferenceError(413, "font_size_limit", "Use a nonempty font at most 2 MiB.")
    with frame_preview.operation() as (directory, deadline):
        path = directory / "font-input.bin"
        path.write_bytes(raw)
        fonts = directory / "native-font-check"
        fonts.mkdir()
        internal = "ReFrameFont" + hashlib.sha256(raw).hexdigest()[:24]
        info = parse(path, directory, deadline, fonts / "selected.ttf", internal)
        metadata = Metadata(
            binding=Binding(
                kind="custom",
                font_id=uuid4(),
                sha256=hashlib.sha256(raw).hexdigest(),
                family=info["family"],
                style=info["style"],
            ),
            size_bytes=len(raw),
            codepoints=info["codepoints"],
            parser_version=info["parser_version"],
        )
        char = (
            "A"
            if 65 in metadata.codepoints
            else next(
                (
                    chr(c)
                    for c in metadata.codepoints
                    if unicodedata.category(chr(c)).startswith("L")
                ),
                None,
            )
        )
        if not char:
            raise ReferenceError(422, "invalid_font", "Font contains no supported letters.")
        track = captions.Track(
            style=captions.Style(font="custom"),
            font_binding=metadata.binding,
            cues=[captions.Cue(start=0, end=1, text=char)],
        )
        subtitle = captions.subtitle_filter(track, directory, 320, 180, (fonts, internal))
        result = engine.run_command(
            [
                shutil.which("ffmpeg") or "ffmpeg",
                "-v",
                "info",
                "-nostdin",
                "-xerror",
                "-f",
                "lavfi",
                "-i",
                "color=s=320x180:d=0.1",
                "-vf",
                subtitle,
                "-frames:v",
                "1",
                "-f",
                "framehash",
                "-",
            ],
            directory,
            "font-native-check",
            deadline,
            10,
            output_limit=8192,
            temp_budget=16 * 1024 * 1024,
        )
        if (
            result.returncode
            or not result.stdout
            or not re.search(rf"fontselect:.*-> {internal}(?:,|\s)", result.stderr)
        ):
            raise ReferenceError(
                422,
                "unsupported_font",
                "FFmpeg/libass could not use this exact font. Previous font retained.",
            )
        # All publication is transactional; the previous usable BLOB survives any failure.
        with projects.database() as db:
            current = db.execute(
                "SELECT revision FROM project_fonts WHERE project_id=?", (project_id,)
            ).fetchone()
            if (current[0] if current else 0) != expected_revision:
                raise ReferenceError(409, "revision_conflict", "Font changed. Reload explicitly.")
            current_caption = db.execute(
                "SELECT * FROM caption_tracks WHERE project_id=?", (project_id,)
            ).fetchone()
            captions.revision_check(current_caption, expected_caption_revision)
            if current_caption:
                track = captions.Track.model_validate_json(current_caption["track"])
                if track.style.font == "custom":
                    # Explicit replacement preserves the timeline and provenance.
                    validate_text(metadata, track.cues)
                    track.font_binding = metadata.binding
                    track.revision += 1
                    track.updated_at = datetime.fromisoformat(projects.now())
                    db.execute(
                        "UPDATE caption_tracks SET revision=?,track=? WHERE project_id=?",
                        (track.revision, track.model_dump_json(), project_id),
                    )
            db.execute(
                "INSERT INTO project_fonts VALUES (?,?,?,?) ON CONFLICT(project_id) "
                "DO UPDATE SET revision=excluded.revision,metadata=excluded.metadata,"
                "content=excluded.content",
                (project_id, expected_revision + 1, metadata.model_dump_json(), raw),
            )
            db.execute("UPDATE projects SET updated_at=? WHERE id=?", (projects.now(), project_id))
    return read(project_id)


def validate_text(metadata, cues):
    points = frozenset(metadata.codepoints)
    if any(
        ord(c) not in points and c != "\n" and unicodedata.category(c) != "Cf"
        for cue in cues
        for c in cue.text
    ):
        raise ReferenceError(
            422,
            "unsupported_font_glyph",
            "Replacement font cannot cover the saved captions. Previous font retained.",
        )
