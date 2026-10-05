"""Assisted shape comparison: user text and a light/dark glyph mask, no OCR."""

import base64
import hashlib
import json
import math
import shutil
import time
import unicodedata
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

import caption_preview
import captions
import color_analysis as color
import font_assets as fonts
import frame_preview as preview
import projects
from references import ReferenceError

METHOD = "glyph-mask-dice-v1"


class Rectangle(color.Schema):
    x: float = Field(ge=0, lt=1, strict=True, allow_inf_nan=False)
    y: float = Field(ge=0, lt=1, strict=True, allow_inf_nan=False)
    width: float = Field(gt=0, le=1, strict=True, allow_inf_nan=False)
    height: float = Field(gt=0, le=1, strict=True, allow_inf_nan=False)

    @model_validator(mode="after")
    def inside(self):
        if self.x + self.width > 1.000001 or self.y + self.height > 1.000001:
            raise ValueError("Keep the rectangle inside the decoded reference frame")
        return self


class Request(caption_preview.ReferenceRequest):
    expected_revision: int = Field(ge=0, strict=True)
    expected_source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    rectangle: Rectangle
    text: str = Field(min_length=1, max_length=80, strict=True)
    polarity: str = Field(default="light", pattern=r"^(light|dark)$")

    @model_validator(mode="after")
    def useful_text(self):
        captions.Cue(start=0, end=1, text=self.text)
        if sum(unicodedata.category(c)[0] in "LN" for c in self.text) < 4:
            raise ValueError("Confirm at least four visible letters or numbers for comparison")
        return self


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


class Selection(color.Schema):
    schema_version: Literal[1] = 1
    method: str = Field(max_length=64)
    source: color.Source
    reference_operation_id: UUID
    requested_timestamp_seconds: float = Field(ge=0, le=120.1, allow_inf_nan=False)
    timestamp_seconds: float = Field(ge=0, le=120.1, allow_inf_nan=False)
    rectangle: Rectangle
    frame_width: int = Field(ge=2, le=960)
    frame_height: int = Field(ge=2, le=960)
    text: str = Field(min_length=1, max_length=80)
    polarity: Literal["light", "dark"]
    catalog_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewed_font: fonts.Binding | None = None


def candidates(project_id, deadline=None):
    result = fonts.library()
    custom = fonts.read(project_id)
    if custom.font and custom.available:
        result.append(custom.font)
    if len(result) > 5:
        raise ReferenceError(
            413, "candidate_limit", "At most five candidate faces can be compared."
        )
    for font in result:
        fonts.validate(project_id, font, [], deadline=deadline)
    return result


def catalog_hash(project_id, deadline=None):
    return fingerprint([f.model_dump(mode="json") for f in candidates(project_id, deadline)])


def read(project_id, deadline=None):
    deadline = deadline or time.monotonic() + 10
    projects.identifier(project_id)
    with projects.database() as db:
        projects.row_project(db, project_id)
        row = db.execute(
            "SELECT * FROM caption_font_matches WHERE project_id=?", (project_id,)
        ).fetchone()
    if not row:
        return {"revision": 0, "status": "empty", "selection": None}
    selection = Selection.model_validate_json(row["selection"]).model_dump(mode="json")
    stale = True
    try:
        source = color.source(project_id, deadline=deadline)
        stale = (
            source["identity"].media_sha256 != selection["source"]["media_sha256"]
            or source["reference_operation_id"] != selection["reference_operation_id"]
            or catalog_hash(project_id, deadline) != selection["catalog_hash"]
            or selection["method"] != METHOD
        )
    except ReferenceError:
        pass
    return {
        "revision": row["revision"],
        "status": "stale" if stale else "ready",
        "selection": selection,
    }


def no_ranking():
    raise ReferenceError(
        422,
        "no_useful_font_match",
        "No useful text shape could be separated. Tighten the crop, confirm text "
        "and try light/dark text. This is not an identity result.",
    )


def mask(raw, width, height, polarity="light", *, tight=True, details=False):
    if len(raw) != width * height or min(width, height) < 8:
        no_ranking()
    # ponytail: global threshold assumes one high-contrast caption; use foreground
    # segmentation if complex backgrounds need support.
    histogram = [0] * 256
    for v in raw:
        histogram[v] += 1
    total = len(raw)
    accumulated = count = 0
    weighted = sum(i * n for i, n in enumerate(histogram))
    best = threshold = 0
    for i, n in enumerate(histogram):
        count += n
        accumulated += i * n
        if not count or count == total:
            continue
        variance = (
            count
            * (total - count)
            * (accumulated / count - (weighted - accumulated) / (total - count)) ** 2
        )
        if variance > best:
            best, threshold = variance, i
    selected = [
        i for i, v in enumerate(raw) if (v > threshold if polarity == "light" else v <= threshold)
    ]
    if not 30 <= len(selected) <= total * 0.55 or (tight and len(selected) < total * 0.005):
        no_ranking()
    foreground = sum(raw[i] for i in selected) / len(selected)
    background = (weighted - sum(raw[i] for i in selected)) / (total - len(selected))
    if abs(foreground - background) < 35:
        no_ranking()
    xs, ys = [i % width for i in selected], [i // width for i in selected]
    left, top, right, bottom = min(xs), min(ys), max(xs), max(ys)
    if right - left < 8 or bottom - top < 5:
        no_ranking()
    points = set(selected)
    w, h = right - left + 1, bottom - top + 1
    if not 0.04 <= len(points) / (w * h) <= 0.75:
        no_ranking()
    normalized_w = round(w * 64 / h)
    if not 8 <= normalized_w <= 960:
        no_ranking()
    # Glyph bounding-box normalization removes source position/background/crop area.
    result = bytes(
        255
        if (top + min(h - 1, int(y * h / 64))) * width
        + left
        + min(w - 1, int(x * w / normalized_w))
        in points
        else 0
        for y in range(64)
        for x in range(normalized_w)
    )
    if details:
        return result, normalized_w, points, (left, top, w, h)
    return result, normalized_w


def similarity(reference, candidate):
    target, tw = reference
    raw, cw = candidate
    a = {(i % tw, i // tw) for i, v in enumerate(target) if v}
    score = 0
    for ratio in (0.95, 1, 1.05):
        width = round(cw * ratio)
        b = {(int(x * ratio), y) for i, v in enumerate(raw) if v for x, y in [(i % cw, i // cw)]}
        center = round((tw - width) / 2)
        for dy in (-2, 0, 2):
            for dx in (-3, 0, 3):
                shifted = {(x + center + dx, y + dy) for x, y in b}
                dice = 2 * len(a & shifted) / (len(a) + len(shifted))
                score = max(score, dice * math.exp(-abs(math.log(width / tw)) * 0.15))
    return round(score * 100, 3)


def gray(path, width, height, directory, deadline, name):
    target = directory / f"{name}.gray"
    preview.tool(
        [
            shutil.which("ffmpeg") or "ffmpeg",
            "-v",
            "error",
            "-nostdin",
            "-i",
            str(path),
            "-vf",
            f"scale={width}:{height}:flags=area,format=gray",
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            str(target),
        ],
        directory,
        name,
        deadline,
    )
    raw = target.read_bytes()
    if len(raw) != width * height:
        no_ranking()
    return raw


def sample_png(normalized, directory, deadline, name):
    raw, width = normalized
    pgm = directory / f"{name}.pgm"
    pgm.write_bytes(f"P5\n{width} 64\n255\n".encode() + raw)
    png = directory / f"{name}.png"
    preview.tool(
        [
            shutil.which("ffmpeg") or "ffmpeg",
            "-v",
            "error",
            "-nostdin",
            "-i",
            str(pgm),
            "-frames:v",
            "1",
            str(png),
        ],
        directory,
        name + "-png",
        deadline,
    )
    return preview.image(png, (width, 64))


def generate(project_id, request):
    with preview.operation() as (directory, deadline):
        previous = read(project_id, deadline)
        if previous["revision"] != request.expected_revision:
            raise ReferenceError(
                409, "revision_conflict", "The saved font comparison changed. Reload it."
            )
        reference = caption_preview.reference_frame(
            project_id, request, stage=(directory, deadline)
        )
        if reference.source.media_sha256 != request.expected_source_hash:
            raise ReferenceError(
                409, "source_changed", "Inspect the current reference before comparing."
            )
        catalog = candidates(project_id, deadline)
        catalog_digest = fingerprint([f.model_dump(mode="json") for f in catalog])
        path = directory / "reference.png"
        path.write_bytes(base64.b64decode(reference.image.png_base64))
        w, h = reference.image.width, reference.image.height
        r = request.rectangle
        x, y = math.floor(r.x * w), math.floor(r.y * h)
        cw, ch = min(w - x, round(r.width * w)), min(h - y, round(r.height * h))
        if cw < 16 or ch < 10:
            no_ranking()
        crop = directory / "region.png"
        preview.tool(
            [
                shutil.which("ffmpeg") or "ffmpeg",
                "-v",
                "error",
                "-nostdin",
                "-i",
                str(path),
                "-vf",
                f"crop={cw}:{ch}:{x}:{y}",
                "-frames:v",
                "1",
                str(crop),
            ],
            directory,
            "region",
            deadline,
        )
        scale = min(1, 512 / cw, 192 / ch)
        mw, mh = max(8, round(cw * scale)), max(8, round(ch * scale))
        reference_mask = mask(
            gray(crop, mw, mh, directory, deadline, "reference"), mw, mh, request.polarity
        )
        ranked, skipped = [], []
        cue = captions.Cue(start=0, end=1, text=request.text)
        for index, binding in enumerate(catalog):
            color.guard(None, deadline)
            try:
                warnings = fonts.validate(project_id, binding, [cue], deadline=deadline)
                if (
                    warnings
                ):  # Missing default glyphs must not be ranked via an unknown fallback face.
                    skipped.append(f"{binding.family} {binding.style}: unsupported text glyphs")
                    continue
            except ReferenceError as error:
                if error.code != "unsupported_font_glyph":
                    raise
                skipped.append(f"{binding.family} {binding.style}: unsupported text glyphs")
                continue
            stage = directory / f"candidate-{index}"
            stage.mkdir()
            prepared = fonts.prepare(project_id, binding, [cue], stage, deadline)
            track = captions.Track(
                style=captions.Style(
                    font=binding.choice, size_percent=12.5, outline_percent=0.0, placement="center"
                ),
                font_binding=binding,
                cues=[cue],
            )
            subtitle = captions.subtitle_filter(track, stage, 4096, 256, prepared[:2])
            target = stage / "candidate.png"
            preview.tool(
                [
                    shutil.which("ffmpeg") or "ffmpeg",
                    "-v",
                    "error",
                    "-nostdin",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=black:s=4096x256:d=0.1",
                    "-vf",
                    subtitle,
                    "-frames:v",
                    "1",
                    str(target),
                ],
                directory,
                f"candidate-{index}",
                deadline,
            )
            normalized = mask(
                gray(target, 4096, 256, stage, deadline, "candidate"), 4096, 256, tight=False
            )
            ranked.append(
                {
                    "font": binding.model_dump(mode="json"),
                    "visual_similarity": similarity(reference_mask, normalized),
                    "image": sample_png(normalized, stage, deadline, "sample").model_dump(),
                }
            )
        if len(ranked) < 2 or max(c["visual_similarity"] for c in ranked) < 35:
            no_ranking()
        current = color.source(project_id, deadline=deadline)
        if (
            current["identity"] != reference.source
            or current["reference_operation_id"] != str(reference.reference_operation_id)
            or catalog_hash(project_id, deadline) != catalog_digest
        ):
            raise ReferenceError(
                409, "source_changed", "Reference or candidate assets changed during comparison."
            )
        selection = {
            "schema_version": 1,
            "method": METHOD,
            "source": reference.source.model_dump(mode="json"),
            "reference_operation_id": str(reference.reference_operation_id),
            "requested_timestamp_seconds": request.timestamp_seconds,
            "timestamp_seconds": reference.timestamp_seconds,
            "rectangle": r.model_dump(),
            "frame_width": w,
            "frame_height": h,
            "text": request.text,
            "polarity": request.polarity,
            "catalog_hash": catalog_digest,
            "reviewed_font": None,
        }
        selection = Selection.model_validate(selection).model_dump(mode="json")
        token = fingerprint(selection)
        ranked.sort(
            key=lambda c: (
                -c["visual_similarity"],
                c["font"]["choice"] if "choice" in c["font"] else c["font"]["sha256"],
            )
        )
        result = {
            "project_id": project_id,
            "revision": previous["revision"] + 1,
            "selection": selection,
            "token": token,
            "crop": preview.image(crop, (cw, ch)).model_dump(),
            "ranked": ranked[:4],
            "skipped": skipped,
            "warnings": reference.warnings
            + [
                "Visual similarity is not exact-font identity. Background, outlines, compression, "
                "perspective, shaping and missing candidates can mislead."
            ],
        }
        color.guard(None, deadline)
        with projects.database() as db:
            projects.row_project(db, project_id)
            db.execute(
                "INSERT INTO caption_font_matches VALUES (?,?,?) ON CONFLICT(project_id) "
                "DO UPDATE SET revision=excluded.revision, selection=excluded.selection",
                (project_id, result["revision"], json.dumps(selection)),
            )
        return result


class Review(color.Schema):
    expected_revision: int = Field(ge=1, strict=True)
    token: str = Field(pattern=r"^[0-9a-f]{64}$")
    choice: str = Field(pattern=r"^(default|custom|amiri-regular|amiri-bold|anton-regular)$")
    expected_font_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


def review(project_id, request):
    saved = read(project_id)
    if saved["status"] != "ready" or saved["revision"] != request.expected_revision:
        raise ReferenceError(
            409, "match_stale", "Compare the current selection before applying a candidate."
        )
    selection = saved["selection"]
    if fingerprint(selection | {"reviewed_font": None}) != request.token:
        raise ReferenceError(
            409, "match_stale", "Crop or text changed. Compare again before applying."
        )
    binding = fonts.selected(project_id, request.choice)
    if binding.sha256 != request.expected_font_hash:
        raise ReferenceError(409, "match_stale", "Candidate font changed. Compare again.")
    fonts.validate(project_id, binding, [captions.Cue(start=0, end=1, text=selection["text"])])
    selection["reviewed_font"] = binding.model_dump(mode="json")
    with projects.database() as db:
        db.execute(
            "UPDATE caption_font_matches SET selection=? WHERE project_id=? AND revision=?",
            (json.dumps(selection), project_id, request.expected_revision),
        )
    return {
        "choice": binding.choice,
        "font": binding.model_dump(mode="json"),
        "label": "Suggested font, user selected",
    }
