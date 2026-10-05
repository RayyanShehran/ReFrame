"""One manual/imported caption track bound to an explicit final output timeline."""

import re
import unicodedata
from datetime import datetime
from decimal import ROUND_CEILING, Decimal
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

import audio_settings
import color_analysis as color
import edit_plan
import font_assets
import footage_analysis as footage
import projects
from references import ReferenceError

SRT_LIMIT = 128 * 1024


class Cue(color.Schema):
    start: float = Field(ge=0, lt=120, strict=True, allow_inf_nan=False)
    end: float = Field(gt=0, le=120, strict=True, allow_inf_nan=False)
    text: str = Field(min_length=1, max_length=200, strict=True)

    @field_validator("text")
    @classmethod
    def plain_text(cls, value):
        if not value.strip():
            raise ValueError("Caption text cannot be empty")
        if value.count("\n") > 1:
            raise ValueError("Use at most two explicit lines per cue")
        if any(unicodedata.category(c) in {"Cc", "Cs"} and c != "\n" for c in value):
            raise ValueError("Caption text contains unsupported control characters")
        return value  # Markup-looking text remains literal, including braces/backslashes.

    @model_validator(mode="after")
    def interval(self):
        if self.start >= self.end:
            raise ValueError("Cue end must be after its start")
        return self


class Style(color.Schema):
    color: str = Field(default="white", pattern=r"^(white|yellow|#[0-9a-fA-F]{6})$")
    size: Literal["small", "medium", "large"] = "medium"
    placement: Literal["bottom-center", "center"] = "bottom-center"
    font: Literal["default", "custom"] = "default"
    size_percent: float | None = Field(default=None, ge=2, le=15, strict=True, allow_inf_nan=False)
    outline_color: str = Field(default="#000000", pattern=r"^#[0-9a-fA-F]{6}$")
    outline_percent: float | None = Field(
        default=None, ge=0, le=2, strict=True, allow_inf_nan=False
    )
    shadow_color: str = Field(default="#000000", pattern=r"^#[0-9a-fA-F]{6}$")
    shadow_percent: float = Field(default=0, ge=0, le=3, strict=True, allow_inf_nan=False)
    alignment: Literal["left", "center", "right"] = "center"
    horizontal: float | None = Field(default=None, ge=0, le=1, strict=True, allow_inf_nan=False)
    vertical: float | None = Field(default=None, ge=0, le=1, strict=True, allow_inf_nan=False)
    bold: bool = Field(default=False, strict=True)
    italic: bool = Field(default=False, strict=True)


class Choices(color.Schema):
    enabled: bool = Field(default=False, strict=True)
    cues: list[Cue] = Field(default_factory=list, max_length=200)
    style: Style = Field(default_factory=Style)
    provenance: Literal["manual", "srt_import", "automatic_transcription"] = "manual"
    automatic_proposal_id: UUID | None = None

    @model_validator(mode="after")
    def ordered(self):
        previous = 0
        for index, cue in enumerate(self.cues, 1):
            if cue.start < previous:
                raise ValueError(f"Cue {index} overlaps its predecessor or is out of order")
            previous = cue.end
        if self.enabled and not self.cues:
            raise ValueError("Add at least one cue before enabling captions")
        if (self.provenance == "automatic_transcription") != (
            self.automatic_proposal_id is not None
        ):
            raise ValueError("Automatic captions require their reviewed proposal identity")
        return self


class Timeline(color.Schema):
    mode: Literal["whole", "cuts"]
    footage: footage.Source
    duration_seconds: float = Field(gt=0, le=120, allow_inf_nan=False)
    plan_revision: int | None = Field(default=None, ge=1, strict=True)

    @model_validator(mode="after")
    def plan(self):
        if (self.mode == "cuts") != (self.plan_revision is not None):
            raise ValueError("Cut timelines require a plan revision")
        return self


class AutomaticBinding(color.Schema):
    timeline: Timeline
    audio: audio_settings.Settings


class Track(Choices):
    schema_version: Literal[1] = 1
    revision: int = Field(default=0, ge=0, strict=True)
    timeline: Timeline | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    automatic_binding: AutomaticBinding | None = None
    font_binding: font_assets.Binding = Field(default_factory=font_assets.Binding)

    @model_validator(mode="after")
    def bound(self):
        if self.style.font != self.font_binding.kind:
            raise ValueError("Selected font must match its saved identity")
        if self.revision and not self.timeline:
            raise ValueError("Saved captions require a timeline")
        if self.timeline:
            validate_duration(self.cues, self.timeline.duration_seconds)
        if (
            self.revision
            and self.provenance == "automatic_transcription"
            and not self.automatic_binding
        ):
            raise ValueError("Saved automatic captions require audio/timeline provenance")
        return self


class Result(color.Schema):
    status: Literal["default", "ready", "stale"]
    track: Track
    message: str | None = None
    whole_duration_seconds: float | None = Field(default=None, gt=0, le=120, allow_inf_nan=False)
    font_warnings: list[str] = Field(default_factory=list)
    font_available: bool = True


class SaveRequest(Choices):
    expected_revision: int = Field(ge=0, strict=True)
    mode: Literal["whole", "cuts"]
    expected_plan_revision: int | None = Field(default=None, ge=1, strict=True)
    confirm_rebind: bool = Field(default=False, strict=True)


class ImportResult(color.Schema):
    cues: list[Cue] = Field(max_length=200)
    provenance: Literal["srt_import"] = "srt_import"


def validate_duration(cues, duration):
    for index, cue in enumerate(cues, 1):
        if cue.end > duration:
            raise ValueError(f"Cue {index} ends outside the {duration:g}-second output timeline")


def timeline(project_id, mode, expected_plan_revision=None, *, stop=None, deadline=None):
    source = footage.source(project_id, stop, deadline)
    duration = projects.get_project(project_id).clip.duration_seconds
    revision = None
    if mode == "cuts":
        result = edit_plan.read(project_id)
        if result.status != "ready":
            raise ReferenceError(
                409, "plan_stale", "Save a valid cut plan before binding captions."
            )
        revision = result.plan.revision
        if expected_plan_revision is not None and revision != expected_plan_revision:
            raise ReferenceError(
                409, "revision_conflict", "Cut plan changed. Reload before saving."
            )
        duration = result.plan.output_frames / 30
    elif expected_plan_revision is not None:
        raise ReferenceError(422, "invalid_timeline", "Whole-clip captions do not bind a cut plan.")
    return Timeline(
        mode=mode, footage=source["identity"], duration_seconds=duration, plan_revision=revision
    )


def record(project_id):
    projects.identifier(project_id)
    with projects.database() as connection:
        project = projects.row_project(connection, project_id)
        if project["status"] != "active":
            raise ReferenceError(409, "project_deleting", "Retry project deletion first.")
        return connection.execute(
            "SELECT * FROM caption_tracks WHERE project_id=?", (project_id,)
        ).fetchone()


def read(project_id):
    row = record(project_id)
    clip = projects.get_project(project_id).clip
    whole_duration = clip.duration_seconds if clip else None
    if not row:
        return Result(status="default", track=Track(), whole_duration_seconds=whole_duration)
    track = Track.model_validate_json(row["track"])
    message = row["message"]
    font_warnings = []
    font_unavailable = False
    try:
        font_warnings = font_assets.validate(project_id, track.font_binding, track.cues)
    except ReferenceError as exc:
        # Font problems are recoverable style errors, not sticky timeline invalidation.
        font_unavailable, message = True, exc.message
    if row["state"] == "ready":
        try:
            if timeline(project_id, track.timeline.mode) != track.timeline:
                raise ReferenceError(
                    409, "captions_stale", "Caption footage or output timeline changed."
                )
            if track.automatic_binding:
                current_audio = audio_settings.read(project_id)
                if (
                    track.automatic_binding.timeline != track.timeline
                    or current_audio.status == "stale"
                    or current_audio.settings != track.automatic_binding.audio
                ):
                    raise ReferenceError(
                        409,
                        "captions_stale",
                        "Audio/timeline changed. Disable and save captions, "
                        "render current settings, then regenerate automatic captions.",
                    )
        except ReferenceError as exc:
            if exc.status_code == 404:
                raise
            message = exc.message
            with projects.database() as connection:
                connection.execute(
                    "UPDATE caption_tracks SET state='stale',message=? "
                    "WHERE project_id=? AND revision=?",
                    (message, project_id, track.revision),
                )
    return Result(
        status="stale" if font_unavailable or message or row["state"] == "stale" else "ready",
        track=track,
        message=message,
        whole_duration_seconds=whole_duration,
        font_warnings=font_warnings,
        font_available=not font_unavailable,
    )


def save(project_id, request):
    row = record(project_id)
    revision_check(row, request.expected_revision)
    previous = Track.model_validate_json(row["track"]) if row else None
    disabling_automatic = bool(
        previous
        and not request.enabled
        and request.provenance == "automatic_transcription"
        and previous.automatic_proposal_id == request.automatic_proposal_id
    )
    # Disabled stale text keeps its original timing/provenance while new audio is rendered.
    binding = (
        previous.timeline
        if disabling_automatic
        else timeline(project_id, request.mode, request.expected_plan_revision)
    )
    if (
        not disabling_automatic
        and request.mode == "cuts"
        and request.expected_plan_revision is None
    ):
        raise ReferenceError(422, "invalid_timeline", "Include the saved cut-plan revision.")
    if (
        previous
        and not disabling_automatic
        and (previous.timeline != binding or row["state"] == "stale")
    ):
        if not request.confirm_rebind:
            raise ReferenceError(
                409,
                "caption_rebind_required",
                "Confirm rebinding to the selected timeline; cues will not shift.",
            )
    try:
        validate_duration(request.cues, binding.duration_seconds)
    except ValueError as exc:
        raise ReferenceError(422, "invalid_caption", str(exc)) from None
    timestamp = projects.now()
    font_binding = font_assets.selected(project_id, request.style.font)
    font_assets.validate(project_id, font_binding, request.cues)
    automatic_binding = None
    if request.provenance == "automatic_transcription":
        if previous and previous.automatic_proposal_id == request.automatic_proposal_id:
            automatic_binding = previous.automatic_binding
            current_audio = audio_settings.read(project_id)
            if request.enabled and (
                not automatic_binding
                or automatic_binding.timeline != binding
                or current_audio.status == "stale"
                or current_audio.settings != automatic_binding.audio
            ):
                raise ReferenceError(
                    409, "proposal_stale", "Regenerate for the current audio/timeline."
                )
        else:
            import transcription

            proposal = transcription.validated_proposal(project_id, request.automatic_proposal_id)
            automatic_binding = proposal.binding
            if automatic_binding.timeline != binding:
                raise ReferenceError(409, "proposal_stale", "Use the proposal's output timeline.")
    track = Track(
        **request.model_dump(
            exclude={"expected_revision", "mode", "expected_plan_revision", "confirm_rebind"}
        ),
        timeline=binding,
        revision=request.expected_revision + 1,
        created_at=previous.created_at if previous else timestamp,
        updated_at=timestamp,
        automatic_binding=automatic_binding,
        font_binding=font_binding,
    )
    with projects.database() as connection:
        project = projects.row_project(connection, project_id)
        if project["status"] != "active":
            raise ReferenceError(409, "project_deleting", "Retry project deletion first.")
        current = connection.execute(
            "SELECT revision FROM caption_tracks WHERE project_id=?", (project_id,)
        ).fetchone()
        revision_check(current, request.expected_revision)
        connection.execute(
            "INSERT INTO caption_tracks(project_id,revision,state,track) "
            "VALUES (?,?,'ready',?) ON CONFLICT(project_id) DO UPDATE SET "
            "revision=excluded.revision,state='ready',track=excluded.track,message=NULL",
            (project_id, track.revision, track.model_dump_json()),
        )
        connection.execute("UPDATE projects SET updated_at=? WHERE id=?", (timestamp, project_id))
    return read(project_id)


def revision_check(row, expected):
    if (row["revision"] if row else 0) != expected:
        raise ReferenceError(
            409,
            "revision_conflict",
            "Captions changed elsewhere. Reload explicitly; your draft is retained.",
        )


def parse_srt(raw):
    if len(raw) > SRT_LIMIT:
        raise ReferenceError(413, "srt_too_large", "SRT import is limited to 128 KiB UTF-8.")
    try:
        text = raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError:
        raise ReferenceError(422, "invalid_srt", "Use a UTF-8 SRT file.") from None
    blocks = re.split(r"\n[ \t]*\n", text.strip())
    cues = []
    pattern = r"(\d{2}):([0-5]\d):([0-5]\d),(\d{3}) --> (\d{2}):([0-5]\d):([0-5]\d),(\d{3})"
    for index, block in enumerate(blocks, 1):
        if index > 200:
            raise ReferenceError(422, "invalid_srt", "Use at most 200 cues.")
        lines = block.split("\n")
        if (
            len(lines) < 3
            or not lines[0].isdigit()
            or not (match := re.fullmatch(pattern, lines[1]))
        ):
            raise ReferenceError(
                422,
                "invalid_srt",
                f"SRT cue {index}: expected index, HH:MM:SS,mmm --> HH:MM:SS,mmm, and text.",
            )
        values = [int(v) for v in match.groups()]

        def seconds(v):
            return v[0] * 3600 + v[1] * 60 + v[2] + v[3] / 1000

        try:
            cues.append(
                Cue(start=seconds(values[:4]), end=seconds(values[4:]), text="\n".join(lines[2:]))
            )
        except ValueError as exc:
            # Pydantic input is not echoed: only the constraint's safe explanation.
            message = exc.errors()[0]["msg"] if hasattr(exc, "errors") else str(exc)
            raise ReferenceError(422, "invalid_srt", f"SRT cue {index}: {message}") from None
    try:
        Choices(cues=cues)
    except ValueError as exc:
        raise ReferenceError(422, "invalid_srt", exc.errors()[0]["msg"]) from None
    return ImportResult(cues=cues)


def literal_ass(text):
    # libass-specific escaped braces; a word joiner prevents literal \N/\n/\h escapes.
    return "".join({"\\": "\\\u2060", "{": r"\{", "}": r"\}", "\n": r"\N"}.get(c, c) for c in text)


def ass_time(seconds):
    # Exact membership at 30 fps: first frame >= boundary, represented in ASS centiseconds.
    frame = int((Decimal(str(seconds)) * 30).to_integral_value(rounding=ROUND_CEILING))
    centiseconds = frame * 100 // 30
    return (
        f"{centiseconds // 360000}:{centiseconds // 6000 % 60:02}:"
        f"{centiseconds // 100 % 60:02}.{centiseconds % 100:02}"
    )


def filter_path(path):
    # AVOption escaping, then filter-graph escaping. No user text enters the filter string.
    value = path.resolve().as_posix()
    value = "".join("\\" + c if c in "\\':" else c for c in value)
    return "".join("\\" + c if c in "\\'[],;" else c for c in value)


def subtitle_filter(track, directory, width, height, prepared_font=None):
    style = track.style
    size = height * (
        style.size_percent / 100
        if style.size_percent is not None
        else {"small": 0.035, "medium": 0.05, "large": 0.07}[style.size]
    )

    def ink(value):
        value = {"white": "#FFFFFF", "yellow": "#FFFF00"}.get(value, value)[1:]
        return "&H00" + value[4:6] + value[2:4] + value[:2]

    alignment = {"left": 1, "center": 2, "right": 3}[style.alignment] + (
        0 if style.placement == "bottom-center" else 3
    )
    fonts, family = prepared_font or (font_assets.BUNDLED.parent, "DejaVu Sans")
    if track.style.font == "custom" and not prepared_font:
        raise ReferenceError(
            409, "caption_font_unavailable", "Prepare the selected custom font before rendering."
        )
    outline = (
        max(0.5, height * 0.003)
        if style.outline_percent is None
        else height * style.outline_percent / 100
    )
    position = ""
    if style.horizontal is not None or style.vertical is not None:
        x = (style.horizontal if style.horizontal is not None else 0.5) * width
        y = (
            style.vertical
            if style.vertical is not None
            else (0.94 if style.placement == "bottom-center" else 0.5)
        ) * height
        position = f"{{\\pos({x:.3f},{y:.3f})}}"  # Server-generated numeric override only.
    header = (
        f"[Script Info]\nScriptType: v4.00+\nPlayResX: {width}\nPlayResY: {height}\n"
        "WrapStyle: 0\nScaledBorderAndShadow: yes\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{family},{size:.3f},{ink(style.color)},{ink(style.color)},"
        f"{ink(style.outline_color)},{ink(style.shadow_color)},"
        f"{-int(style.bold)},{-int(style.italic)},0,0,100,100,0,0,1,{outline:.3f},"
        f"{height * style.shadow_percent / 100:.3f},{alignment},"
        f"{round(width * 0.04)},{round(width * 0.04)},{round(height * 0.06)},-1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, "
        "MarginL, MarginR, MarginV, Effect, Text\n"
    )
    content = header + "".join(
        f"Dialogue: 0,{ass_time(c.start)},{ass_time(c.end)},Default,,0,0,0,,"
        f"{position}{literal_ass(c.text)}\n"
        for c in track.cues
        if ass_time(c.start) != ass_time(c.end)
    )
    encoded = content.encode("utf-8")
    if len(encoded) > 512 * 1024:
        raise ReferenceError(422, "caption_data_limit", "Subtitle data exceeds its 512 KiB bound.")
    target = directory / "captions.ass"
    target.write_bytes(encoded)
    if not fonts.is_dir() or (
        track.style.font == "default" and not (fonts / "DejaVuSans.ttf").is_file()
    ):
        raise ReferenceError(
            503, "caption_font_unavailable", "Bundled caption font is unavailable."
        )
    return f"ass=filename={filter_path(target)}:fontsdir={filter_path(fonts)}:shaping=complex"
