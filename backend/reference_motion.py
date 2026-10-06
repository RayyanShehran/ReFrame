"""Conservative temporal glyph fitting, using reviewed text/font and owned local tools."""

import base64
import math
import shutil
import statistics as stats
import time
from typing import Literal

from pydantic import Field, model_validator

import caption_appearance as appearance
import caption_preview
import captions
import color_analysis as color
import font_assets as fonts
import font_match as match
import frame_preview as preview
import framing
import projects
import video_render as render
from references import ReferenceError

METHOD = "temporal-glyph-fit-v1"
FPS = 10
MAX_FRAMES = 40
EDGE = 512


class Request(appearance.Request):
    start: float = Field(ge=0, le=120, strict=True, allow_inf_nan=False)
    end: float = Field(gt=0, le=120, strict=True, allow_inf_nan=False)
    region: match.Rectangle

    @model_validator(mode="after")
    def interval(self):
        if not 0.4 <= self.end - self.start <= 4:
            raise ValueError("Select 0.4–4 seconds inside the reference")
        return self


class Values(color.Schema):
    mode: Literal["none", "fade", "pop", "slide-up"] | None = None
    entrance_seconds: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    exit_seconds: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    initial_scale: float | None = Field(default=None, ge=0.5, le=1, allow_inf_nan=False)
    displacement: float | None = Field(default=None, ge=0, le=0.25, allow_inf_nan=False)


class Observation(color.Schema):
    time: float = Field(ge=0, le=120, allow_inf_nan=False)
    contrast: float = Field(ge=0, le=255, allow_inf_nan=False)
    width: float = Field(ge=0, le=1, allow_inf_nan=False)
    height: float = Field(ge=0, le=1, allow_inf_nan=False)
    x: float = Field(ge=0, le=1, allow_inf_nan=False)
    y: float = Field(ge=0, le=1, allow_inf_nan=False)
    glyph_overlap: float = Field(ge=0, le=1, allow_inf_nan=False)
    clipped: bool = False


class Suggestion(color.Schema):
    schema_version: Literal[1] = 1
    method: str = METHOD
    request: Request
    selection: match.Selection
    appearance_token: str | None = None
    fitting_style: captions.Style
    outcome: Literal["static", "supported", "partial", "inconclusive"]
    values: Values
    cue_start: float | None = Field(default=None, ge=0, le=120, allow_inf_nan=False)
    cue_end: float | None = Field(default=None, gt=0, le=120.2, allow_inf_nan=False)
    observations: list[Observation] = Field(max_length=MAX_FRAMES)
    notes: list[str] = Field(max_length=16)
    generated_at: str
    ffmpeg_version: str


class Apply(Request):
    token: str = Field(pattern=r"^[0-9a-f]{64}$")


def bindings(project_id, request, deadline, current=None):
    revision, selection, token = current or appearance.current(project_id, deadline)
    if (revision, token) != (request.expected_selection_revision, request.expected_selection_token):
        raise ReferenceError(409, "motion_stale", "Reference text/region/font changed. Reload it.")
    selected = match.Selection.model_validate(selection)
    if selected.reviewed_font.choice != request.base_style.font:
        raise ReferenceError(
            409, "motion_prerequisite", "Choose the reviewed face in Caption font first."
        )
    row = appearance.record(project_id)
    saved = appearance.Suggestion.model_validate_json(row["suggestion"]) if row else None
    reused = bool(
        saved
        and saved.method == appearance.METHOD
        and saved.selection_revision == revision
        and saved.selection_token == token
        and saved.base_style.font == request.base_style.font
        and appearance.unsupported(saved.base_style) == appearance.unsupported(request.base_style)
    )
    style = (
        request.base_style.model_copy(update=saved.values.model_dump(exclude_none=True))
        if reused
        else request.base_style
    )
    return selected, style, match.fingerprint(saved.model_dump(mode="json")) if reused else None


def record(project_id):
    with projects.database() as db:
        projects.row_project(db, project_id)
        return db.execute(
            "SELECT * FROM caption_motion_suggestions WHERE project_id=?", (project_id,)
        ).fetchone()


def read(project_id, deadline=None):
    row = record(project_id)
    value = Suggestion.model_validate_json(row["suggestion"]) if row else None
    ready, message, current_selection = False, None, None
    try:
        current = appearance.current(project_id, deadline or time.monotonic() + 10)
        revision, selected, selection_token = current
        current_selection = {"revision": revision, "token": selection_token, "selection": selected}
        if value:
            selected, style, token = bindings(
                project_id, value.request, deadline or time.monotonic() + 10, current
            )
            ready = value.method == METHOD and (selected, style, token) == (
                value.selection,
                value.fitting_style,
                value.appearance_token,
            )
    except ReferenceError as error:
        message = error.message
    return {
        "revision": row["revision"] if row else 0,
        "status": "ready" if ready else "stale" if value else "empty",
        "suggestion": value.model_dump(mode="json") if value else None,
        "token": match.fingerprint(value.model_dump(mode="json")) if value else None,
        "message": message,
        "current_selection": current_selection,
    }


def apply(project_id, request):
    saved = read(project_id)
    if (
        saved["status"] != "ready"
        or saved["revision"] != request.expected_revision
        or saved["token"] != request.token
    ):
        raise ReferenceError(
            409,
            "motion_stale",
            "Motion suggestion or source changed. Analyze again before applying.",
        )
    value = Suggestion.model_validate(saved["suggestion"])
    compare = request.model_dump(exclude={"expected_revision", "token"})
    if compare != value.request.model_dump(exclude={"expected_revision"}):
        raise ReferenceError(
            409, "motion_stale", "Interval, analysis region or appearance changed. Analyze again."
        )
    patch = value.values.model_dump(exclude_none=True)
    if not patch or value.outcome == "inconclusive":
        raise ReferenceError(
            422,
            "motion_inconclusive",
            "No supported settings to apply. Manual controls remain available.",
        )
    return {"patch": patch, "font_hash": value.selection.reviewed_font.sha256}


def overlap(reference, candidate):
    a, aw = reference
    b, bw = candidate
    if abs(math.log(aw / bw)) > 0.2:
        return 0.0
    b = bytes(b[y * bw + min(bw - 1, int(x * bw / aw))] for y in range(64) for x in range(aw))
    n = sum(bool(x) for x in a) + sum(bool(x) for x in b)
    return 2 * sum(bool(x and y) for x, y in zip(a, b)) / n if n else 0.0


def observe(raw, width, height, region, timestamp, template, polarity):
    x, y = int(region.x * width), int(region.y * height)
    w, h = (
        min(width - x, round(region.width * width)),
        min(height - y, round(region.height * height)),
    )
    crop = b"".join(raw[j * width + x : j * width + x + w] for j in range(y, y + h))
    try:
        normalized, nw, points, box = match.mask(crop, w, h, polarity, tight=False, details=True)
    except ReferenceError as error:
        if error.code != "no_useful_font_match":
            raise
        return Observation(time=timestamp, contrast=0, width=0, height=0, x=0, y=0, glyph_overlap=0)
    fg = stats.median(crop[i] for i in points)
    bg = stats.median(crop[i] for i in range(len(crop)) if i not in points)
    left, top, bw, bh = box
    return Observation(
        time=timestamp,
        contrast=abs(fg - bg),
        width=bw / width,
        height=bh / height,
        x=(x + left + bw / 2) / width,
        y=(y + top + bh / 2) / height,
        glyph_overlap=overlap(template, (normalized, nw)),
        clipped=left < 2 or top < 2 or left + bw > w - 2 or top + bh > h - 2,
    )


def fit(rows, contaminated=False):
    notes = [
        "Sampling precision is 0.1 s; practical timing tolerance is ±0.15 s. "
        "These are measurements, not confidence percentages."
    ]

    def fail(message):
        return "inconclusive", Values(), None, None, notes + [message]

    visible = [r for r in rows if r.contrast >= 35]
    if contaminated:
        return fail("Strong background/camera changes contaminate tracking.")
    if len(visible) < 4:
        return fail("Not enough readable caption evidence; no animation is inferred.")
    if any(r.clipped for r in visible):
        return fail(
            "Tracking touches the analysis edge; expand the motion region "
            "without changing the static crop."
        )
    if any(r.glyph_overlap < 0.55 for r in visible):
        return fail("Confirmed text/font is inconsistent, changing, rotated or tracking is lost.")
    if max(r.glyph_overlap for r in visible) - min(r.glyph_overlap for r in visible) > 0.22:
        return fail("Glyph shape changes across the interval; review words and tracking.")
    peak = max(r.contrast for r in visible)
    maxw = max(r.width for r in visible)
    maxh = max(r.height for r in visible)
    steady = [
        i
        for i, r in enumerate(rows)
        if r.contrast >= peak * 0.9 and r.width >= maxw * 0.94 and r.height >= maxh * 0.9
    ]
    runs = []
    for i in steady:
        if runs and i == runs[-1][-1] + 1 and abs(rows[i].y - rows[runs[-1][-1]].y) < 0.008:
            runs[-1].append(i)
        else:
            runs.append([i])
    settled = max(runs, key=len, default=[])
    if len(settled) < 3:
        return fail("Insufficient settled frames; no preset is forced.")
    first, last = settled[0], settled[-1]
    center = stats.median(rows[i].y for i in settled)
    moving = [
        r
        for r in visible
        if r.width < maxw * 0.85 or abs(r.y - center) > 0.02 or r.contrast < peak * 0.75
    ]
    alpha = any(r.contrast < peak * 0.75 for r in visible)
    scaling = any(r.width < maxw * 0.85 for r in visible)
    sliding = any(r.y - center > 0.025 for r in visible)
    if sum((alpha, scaling, sliding)) > 1:
        return fail("Combined opacity/scale/movement is unsupported.")
    if any(abs(r.x - stats.median(rows[i].x for i in settled)) > 0.012 for r in visible):
        return fail("Horizontal movement or rotation is unsupported.")
    indexes = [i for i, r in enumerate(rows) if r.contrast >= 35]
    vi, vj = indexes[0], indexes[-1]
    if indexes != list(range(vi, vj + 1)):
        return fail("Tracking loss or repeated visibility changes are inconclusive.")
    entrance_seen = vi > 0 and first > vi
    exit_seen = vj < len(rows) - 1 and last < vj
    start = rows[vi].time if vi > 0 else None
    end = rows[vj].time + 1 / FPS if vj < len(rows) - 1 else None
    if not moving:
        return (
            "static",
            Values(mode="none"),
            start,
            end,
            notes
            + [
                "Sufficient stable text; no animation observed in this interval. "
                "Unseen entrance/exit is not estimated."
            ],
        )
    if not entrance_seen and not (alpha and exit_seen):
        return fail(
            "The interval omits the changing entrance/exit boundary. Expand it before fitting."
        )
    mode = "fade" if alpha else "pop" if scaling else "slide-up" if sliding else None
    if not mode:
        return fail("Observed motion does not match a supported family.")

    def metric(r):
        return (
            r.contrast / peak if alpha else r.width / maxw if scaling else 1 - (r.y - center) / 0.25
        )

    before = [metric(r) for r in rows[vi : first + 1]]
    after = [metric(r) for r in rows[last : vj + 1]]
    if any(b < a - 0.08 for a, b in zip(before, before[1:])) or any(
        b > a + 0.08 for a, b in zip(after, after[1:])
    ):
        return fail("Bounce/reversal or unstable tracking is unsupported.")
    if mode != "fade" and any(metric(r) < 0.9 for r in rows[last : vj + 1]):
        return fail("Pop/Slide exit motion is unsupported.")
    values = {"mode": mode}
    if entrance_seen:
        if alpha:
            ramp = [r for r in rows[vi : first + 1] if 0.15 < r.contrast / peak < 0.9]
            if len(ramp) < 2:
                return fail("Too few opacity ramp samples to estimate Fade timing.")
            a, b = ramp[0], ramp[-1]
            slope = (b.contrast - a.contrast) / peak / (b.time - a.time)
            if slope <= 0:
                return fail("Opacity ramp is inconsistent.")
            start = a.time - a.contrast / peak / slope
            duration = 1 / slope
        else:
            duration = rows[first].time - start
        if not 0.05 <= duration <= 1:
            return fail("Entrance exceeds the supported 0–1 second fitting range.")
        values["entrance_seconds"] = round(duration, 3)
        if scaling:
            ratio = rows[vi].width / maxw
            if ratio < 0.5:
                return fail("Pop starts below the supported 50% scale.")
            values["initial_scale"] = round(ratio, 3)
        if sliding:
            displacement = rows[vi].y - center
            if not 0 < displacement <= 0.25:
                return fail("Slide displacement exceeds the supported canvas range.")
            values["displacement"] = round(displacement, 3)
    else:
        notes.append("Entrance was not observed; its duration/scale/displacement remain unchanged.")
    if alpha and exit_seen:
        ramp = [r for r in rows[last : vj + 1] if 0.15 < r.contrast / peak < 0.9]
        if len(ramp) < 2:
            return fail("Too few exit opacity samples to estimate Fade timing.")
        a, b = ramp[0], ramp[-1]
        slope = (a.contrast - b.contrast) / peak / (b.time - a.time)
        if slope <= 0 or not 0.05 <= 1 / slope <= 1:
            return fail("Exit exceeds the supported fitting range.")
        values["exit_seconds"] = round(1 / slope, 3)
        end = b.time + b.contrast / peak / slope
    elif alpha:
        notes.append("Exit was not observed; its duration remains unchanged.")
    partial = not entrance_seen or alpha and not exit_seen
    return (
        "partial" if partial else "supported",
        Values(**values),
        max(0, start) if start is not None else None,
        end,
        notes,
    )


def analyze(project_id, request):
    with preview.operation() as (directory, deadline):
        previous = record(project_id)
        if (previous["revision"] if previous else 0) != request.expected_revision:
            raise ReferenceError(
                409, "revision_conflict", "Saved motion suggestion changed. Reload it."
            )
        selection, style, appearance_token = bindings(project_id, request, deadline)
        source = color.source(project_id, deadline=deadline)
        # ponytail: high-contrast glyph masks only; complex motion needs segmentation.
        video, _, duration = render.probe(
            source["path"], directory, deadline, temp_budget=preview.TEMP_BUDGET
        )
        if request.end > duration:
            raise ReferenceError(
                422, "invalid_interval", "Keep the interval inside the retained reference duration."
            )
        scale, canvas, _ = framing.geometry(video, framing.Settings())
        factor = min(1, EDGE / max(canvas))
        w, h = (max(2, math.floor(n * factor / 2) * 2) for n in canvas)
        target = directory / "sequence.gray"
        graph = (
            f"setpts=PTS-STARTPTS,{render.resize_filter(*scale, color.inspect_colors(video))},"
            f"scale={w}:{h}:flags=area,fps={FPS},"
            f"trim=start={request.start}:end={request.end},format=gray"
        )
        preview.tool(
            [
                shutil.which("ffmpeg") or "ffmpeg",
                "-v",
                "error",
                "-nostdin",
                "-xerror",
                "-threads",
                "2",
                "-filter_threads",
                "1",
                "-protocol_whitelist",
                "file",
                "-i",
                str(source["path"]),
                "-vf",
                graph,
                "-an",
                "-sn",
                "-frames:v",
                str(MAX_FRAMES),
                "-f",
                "rawvideo",
                str(target),
            ],
            directory,
            "motion-sample",
            deadline,
        )
        size = target.stat().st_size
        count = math.ceil(request.end * FPS - 1e-7) - math.ceil(request.start * FPS - 1e-7)
        if not 4 <= count <= MAX_FRAMES or size != w * h * count:
            raise ValueError("Incomplete temporal frame decoding")
        raw = target.read_bytes()
        target.unlink()
        prepared = fonts.prepare(
            project_id,
            selection.reviewed_font,
            [captions.Cue(start=0, end=1, text=selection.text)],
            directory,
            deadline,
        )
        neutral = style.model_copy(
            update={
                "color": "#FFFFFF",
                "outline_percent": 0.0,
                "shadow_percent": 0.0,
                "horizontal": 0.5,
                "vertical": 0.5,
            }
        )
        _, template = appearance.render_sample(
            neutral, selection.reviewed_font, selection.text, w, h, directory, prepared, deadline
        )
        rows = []
        outside = []
        for i in range(count):
            color.guard(None, deadline)
            frame = raw[i * w * h : (i + 1) * w * h]
            rows.append(
                observe(
                    frame,
                    w,
                    h,
                    request.region,
                    (math.ceil(request.start * FPS - 1e-7) + i) / FPS,
                    template,
                    selection.polarity,
                )
            )
            outside.append(
                bytes(
                    v
                    for j, v in enumerate(frame)
                    if j % w < request.region.x * w
                    or j % w > (request.region.x + request.region.width) * w
                    or j // w < request.region.y * h
                    or j // w > (request.region.y + request.region.height) * h
                )
            )
        contamination = any(
            sum(abs(a - b) > 24 for a, b in zip(x, y)) / max(1, len(x)) > 0.2
            for x, y in zip(outside, outside[1:])
        )
        outcome, values, start, end, notes = fit(rows, contamination)
        notes.append(
            "Saved appearance reused."
            if appearance_token
            else "Manual appearance used; valid matching saved appearance was unavailable."
        )
        if (
            bindings(project_id, request, deadline) != (selection, style, appearance_token)
            or color.source(project_id, deadline=deadline) != source
        ):
            raise ReferenceError(
                409, "motion_stale", "Reference/font/appearance changed before publication."
            )
        suggestion = Suggestion(
            request=request,
            selection=selection,
            appearance_token=appearance_token,
            fitting_style=style,
            outcome=outcome,
            values=values,
            cue_start=start,
            cue_end=end,
            observations=rows,
            notes=notes,
            generated_at=projects.now(),
            ffmpeg_version=caption_preview.version(directory, deadline),
        )
    # Publish only after owned staging cleanup succeeds, inside the existing project lock.
    color.guard(None, deadline)
    with projects.database() as db:
        projects.row_project(db, project_id)
        db.execute(
            "INSERT INTO caption_motion_suggestions VALUES (?,?,?) ON CONFLICT(project_id) "
            "DO UPDATE SET revision=excluded.revision,suggestion=excluded.suggestion",
            (project_id, request.expected_revision + 1, suggestion.model_dump_json()),
        )
    return {
        "revision": request.expected_revision + 1,
        "status": "ready",
        "suggestion": suggestion.model_dump(mode="json"),
        "token": match.fingerprint(suggestion.model_dump(mode="json")),
        "message": None,
        "current_selection": {
            "revision": request.expected_selection_revision,
            "token": request.expected_selection_token,
            "selection": selection.model_dump(mode="json"),
        },
    }


VIDEO_BYTES = 3 * 1024 * 1024


class Clip(color.Schema):
    width: int = Field(ge=2, le=640)
    height: int = Field(ge=2, le=640)
    duration_seconds: float = Field(gt=0, le=4, allow_inf_nan=False)
    decoded_frames: int = Field(ge=1, le=120)
    size_bytes: int = Field(gt=0, le=VIDEO_BYTES)
    video_base64: str = Field(max_length=4 * VIDEO_BYTES // 3)
    silent: Literal[True] = True


def video_clip(inputs, graph, count, size, directory, deadline, name):
    target = directory / f"{name}.mp4"
    preview.tool(
        [
            shutil.which("ffmpeg") or "ffmpeg",
            "-v",
            "error",
            "-nostdin",
            "-xerror",
            "-threads",
            "2",
            "-filter_threads",
            "1",
            *inputs,
            "-vf",
            graph,
            "-an",
            "-sn",
            "-dn",
            "-frames:v",
            str(count),
            "-c:v",
            "libx264",
            "-threads",
            "2",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-fs",
            str(VIDEO_BYTES + 1),
            "-movflags",
            "+faststart",
            str(target),
        ],
        directory,
        name,
        deadline,
    )
    length = target.stat().st_size
    if not 0 < length <= VIDEO_BYTES:
        raise ReferenceError(413, "motion_limit", "Comparison exceeds its three-MiB video limit.")
    v, a, d = render.probe(target, directory, deadline, temp_budget=preview.TEMP_BUDGET)
    if a or (v["width"], v["height"]) != size or d > 4 or abs(d - count / 30) > 0.05:
        raise ValueError("Invalid comparison video")
    hashes = preview.tool(
        [
            shutil.which("ffmpeg") or "ffmpeg",
            "-v",
            "error",
            "-nostdin",
            "-xerror",
            "-i",
            str(target),
            "-map",
            "0:v:0",
            "-f",
            "framehash",
            "-hash",
            "sha256",
            "-",
        ],
        directory,
        name + "-decode",
        deadline,
    )
    frames, audio = render.decode_counts(hashes, False)
    if frames != count or audio:
        raise ValueError("Incomplete comparison decoding")
    return Clip(
        width=size[0],
        height=size[1],
        duration_seconds=d,
        decoded_frames=frames,
        size_bytes=length,
        video_base64=base64.b64encode(target.read_bytes()).decode(),
    )


def reference_video(project_id, request, directory, deadline):
    selection, style, token = bindings(project_id, request, deadline)
    source = color.source(project_id, deadline=deadline)
    v, _, duration = render.probe(
        source["path"], directory, deadline, temp_budget=preview.TEMP_BUDGET
    )
    if request.end > duration:
        raise ReferenceError(
            422, "invalid_interval", "Keep the interval inside the reference duration."
        )
    scale, canvas, _ = framing.geometry(v, framing.Settings())
    factor = min(1, EDGE / max(canvas))
    size = tuple(max(2, math.floor(n * factor / 2) * 2) for n in canvas)
    first = math.ceil(request.start * 30 - 1e-7)
    count = math.ceil(request.end * 30 - 1e-7) - first
    graph = (
        f"setpts=PTS-STARTPTS,{render.resize_filter(*scale, color.inspect_colors(v))},"
        f"scale={size[0]}:{size[1]}:flags=area,fps=30,"
        f"trim=start_frame={first}:end_frame={first + count},setpts=PTS-STARTPTS"
    )
    video = video_clip(
        ["-protocol_whitelist", "file", "-i", str(source["path"])],
        graph,
        count,
        size,
        directory,
        deadline,
        "reference-interval",
    )
    return video, source, selection, style, token, first


def interval_preview(project_id, request):
    with preview.operation() as (directory, deadline):
        video, source, selection, style, token, _ = reference_video(
            project_id, request, directory, deadline
        )
        if color.source(project_id, deadline=deadline) != source or bindings(
            project_id, request, deadline
        ) != (selection, style, token):
            raise ReferenceError(
                409, "motion_stale", "Reference/font/appearance changed during preview."
            )
        return {
            "reference": video.model_dump(),
            "request": request.model_dump(),
            "selection": selection.model_dump(mode="json"),
        }


def comparison(project_id, request):
    with preview.operation() as (directory, deadline):
        saved = read(project_id)
        if (
            saved["status"] != "ready"
            or saved["revision"] != request.expected_revision
            or saved["token"] != request.token
        ):
            raise ReferenceError(409, "motion_stale", "Motion suggestion changed. Reload it.")
        suggestion = Suggestion.model_validate(saved["suggestion"])
        if request.model_dump(
            exclude={"expected_revision", "token"}
        ) != suggestion.request.model_dump(exclude={"expected_revision"}):
            raise ReferenceError(
                409, "motion_stale", "Interval/region/appearance changed. Analyze again."
            )
        if suggestion.outcome == "inconclusive":
            raise ReferenceError(
                422,
                "motion_inconclusive",
                "No supported reconstruction; review the reference or use manual animation.",
            )
        reference, source, selection, style, token, first = reference_video(
            project_id, request, directory, deadline
        )
        # Unseen transitions are neutral, never fabricated midway through the interval.
        animation = captions.Animation.model_validate(
            {
                "entrance_seconds": 0.0,
                "exit_seconds": 0.0,
                "initial_scale": 1.0,
                "displacement": 0.0,
                **suggestion.values.model_dump(exclude_none=True),
            }
        )
        cue = captions.Cue(
            start=suggestion.cue_start if suggestion.cue_start is not None else 0.0,
            end=min(
                120.0, suggestion.cue_end if suggestion.cue_end is not None else request.end + 2
            ),
            text=selection.text,
        )
        prepared = fonts.prepare(project_id, selection.reviewed_font, [cue], directory, deadline)
        track = captions.Track(
            style=style, font_binding=selection.reviewed_font, cues=[cue], animation=animation
        )
        subtitle = captions.subtitle_filter(
            track, directory, reference.width, reference.height, prepared[:2]
        )
        graph = f"setpts=PTS+{first / 30:.9f}/TB,{subtitle},setpts=PTS-STARTPTS"
        reconstructed = video_clip(
            [
                "-f",
                "lavfi",
                "-i",
                f"color=c=0x808080:s={reference.width}x{reference.height}:r=30:d={reference.duration_seconds:.9f}",
            ],
            graph,
            reference.decoded_frames,
            (reference.width, reference.height),
            directory,
            deadline,
            "motion-reconstruction",
        )
        if (
            bindings(project_id, request, deadline) != (selection, style, token)
            or color.source(project_id, deadline=deadline) != source
        ):
            raise ReferenceError(
                409, "motion_stale", "Comparison source/font changed before publication."
            )
        return {
            "revision": saved["revision"],
            "token": saved["token"],
            "reference": reference.model_dump(),
            "reconstruction": reconstructed.model_dump(),
            "notes": [
                "Neutral background; confirmed reference text/font/style. "
                "Unseen transitions are omitted.",
                "Reference timeline phase is preserved; videos are silent "
                "and start only on request.",
            ],
        }
