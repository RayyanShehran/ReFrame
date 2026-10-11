"""Bounded visible-text tracking; never substitutes speech for visible reference words."""

import csv
import io
import math
import shutil
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, replace
from difflib import SequenceMatcher

import caption_appearance as appearance
import caption_ocr as ocr
import captions
import color_analysis as color
import font_assets as fonts
import font_match as match
import framing
import reference_engine as engine
import reference_motion as motion
import video_render as render
from references import ReferenceError

METHOD = "visible-tsv-adaptive-v1"
MAX_READS = 600
MAX_APPEARANCES = 8
TOTAL_SECONDS = 300


@dataclass
class Detection:
    text: str
    rectangle: match.Rectangle
    confidence: float
    ink: str = "#FFFFFF"
    sample_text: str | None = None
    sample_rectangle: match.Rectangle | None = None


def parse(raw, width, height):
    try:
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8")), delimiter="\t")
        required = {
            "level",
            "page_num",
            "block_num",
            "par_num",
            "line_num",
            "word_num",
            "left",
            "top",
            "width",
            "height",
            "conf",
            "text",
        }
        if not reader.fieldnames or set(reader.fieldnames) != required:
            raise ValueError("Missing detector columns")
        rows = list(reader)
        if not rows or int(rows[0]["level"]) != 1:
            raise ValueError("Missing detector page")
        if len(rows) > 2048:
            raise ValueError("Too many words")
        groups = {}
        for row in rows:
            if int(row["level"]) != 5 or not row["text"].strip():
                continue
            confidence = float(row["conf"])
            x, y, w, h = (int(row[k]) for k in ("left", "top", "width", "height"))
            if (
                not math.isfinite(confidence)
                or not -1 <= confidence <= 100
                or not 0 <= x < width
                or not 0 <= y < height
                or not 0 < w <= width - x
                or not 0 < h <= height - y
            ):
                raise ValueError("Invalid word geometry")
            key = tuple(int(row[k]) for k in ("block_num", "par_num", "line_num"))
            groups.setdefault(key, []).append((row["text"].strip(), x, y, w, h, confidence))
        if len(groups) > 32:
            raise ReferenceError(
                413, "caption_region_limit", "Too much visible text. Open Customize."
            )
        detections = []
        for words in groups.values():
            text = " ".join(word[0] for word in words)
            confidence = sum(word[5] for word in words) / len(words)
            # This is a heuristic rejection rule, not a calibrated confidence threshold.
            x, y = min(w[1] for w in words), min(w[2] for w in words)
            right, bottom = max(w[1] + w[3] for w in words), max(w[2] + w[4] for w in words)
            if confidence < 35 or sum(c.isalnum() for c in text) < 2:
                if (
                    10 <= confidence < 35
                    and (bottom - y) / height >= 0.025
                    and (right - x) / width >= 0.15
                    and y / height > 0.2
                ):
                    raise ReferenceError(
                        422,
                        "caption_unreadable",
                        "Likely caption text is unreadable. "
                        "Retry, choose Off or correct it in Customize.",
                    )
                continue
            captions.Cue(start=0.0, end=1.0, text=text)
            sample = max(
                (word for word in words if 4 <= len(word[0]) <= 12),
                key=lambda word: len(word[0]),
                default=None,
            )
            detections.append(
                Detection(
                    text,
                    match.Rectangle(
                        x=x / width,
                        y=y / height,
                        width=(right - x) / width,
                        height=(bottom - y) / height,
                    ),
                    confidence,
                    sample_text=sample[0] if sample else None,
                    sample_rectangle=match.Rectangle(
                        x=sample[1] / width,
                        y=sample[2] / height,
                        width=sample[3] / width,
                        height=sample[4] / height,
                    )
                    if sample
                    else None,
                )
            )
        return detections
    except (KeyError, TypeError, ValueError, UnicodeError):
        raise ReferenceError(
            422, "ocr_output_invalid", "Visible text could not be read safely."
        ) from None


def same(a, b):
    if a is None or b is None:
        return a is b
    return (
        SequenceMatcher(None, a.text.casefold(), b.text.casefold()).ratio() >= 0.9
        and abs(a.rectangle.y - b.rectangle.y) < 0.08
    )


def dominant(rows, ignored):
    eligible = [
        d
        for d in rows
        if d.text not in ignored and not d.text.startswith("@") and "http" not in d.text.lower()
    ]
    selected = max(eligible, key=lambda d: d.rectangle.height * len(d.text), default=None)
    if selected is None:
        return None
    r = selected.rectangle
    adjacent = [
        d
        for d in eligible
        if d is not selected
        and abs(d.rectangle.height - r.height) < r.height * 0.3
        and abs((d.rectangle.x + d.rectangle.width / 2) - (r.x + r.width / 2)) < 0.08
        and abs(d.rectangle.y - r.y) <= r.height * 1.8
        and d.rectangle.y != r.y
    ]
    if not adjacent:
        return selected
    other = min(adjacent, key=lambda d: abs(d.rectangle.y - r.y))
    lines = sorted([selected, other], key=lambda d: d.rectangle.y)
    text = "\n".join(d.text for d in lines)
    captions.Cue(start=0.0, end=1.0, text=text)
    left = min(d.rectangle.x for d in lines)
    top = min(d.rectangle.y for d in lines)
    right = max(d.rectangle.x + d.rectangle.width for d in lines)
    bottom = max(d.rectangle.y + d.rectangle.height for d in lines)
    return replace(
        selected,
        text=text,
        rectangle=match.Rectangle(x=left, y=top, width=right - left, height=bottom - top),
    )


def track(read, frames, ignored):
    observations = [(f, dominant(read(f), ignored)) for f in frames]

    def refine(left, a, right, b):
        if same(a, b):
            return []
        if right - left <= 1:
            return [(right, b)]
        middle = (left + right) // 2
        detected = dominant(read(middle), ignored)
        return refine(left, a, middle, detected) + refine(middle, detected, right, b)

    refined = [observations[0]]
    for (left, a), (right, b) in zip(observations, observations[1:]):
        refined.extend(refine(left, a, right, b))
    return refined


@contextmanager
def temporary(stage):
    stage.mkdir()
    safe = True
    try:
        yield stage
    except engine.ProcessCleanupError:
        safe = False
        raise
    except engine.RetrievalFailure as error:
        safe = error.cleanup_safe
        raise
    except ReferenceError as error:
        safe = error.code != "cleanup_failure"
        raise
    finally:
        if safe:
            try:
                shutil.rmtree(stage)
            except OSError:
                raise ReferenceError(
                    500, "cleanup_failure", "Caption staging needs manual cleanup."
                ) from None


def detect(project_id, source, directory, stop, deadline):
    engine._control.stop = stop
    available = ocr.inspect_capability(directory, deadline)
    if not available["available"]:
        raise ReferenceError(
            503,
            "ocr_unavailable",
            "Set up local English/Arabic text reading, or choose captions Off.",
        )
    video, _, duration = render.probe(
        source["path"], directory, deadline, temp_budget=16 * 1024 * 1024
    )
    color.inspect_colors(video)
    scale, canvas, _ = framing.geometry(video, framing.Settings())
    factor = 960 / max(canvas)
    width, height = (max(2, math.floor(v * factor / 2) * 2) for v in canvas)
    total = max(1, math.floor(duration * 30 + 0.00001))
    frames = sorted(set([0, *range(15, total, 15), total - 1]))
    cache = {}

    def read(index):
        color.guard(stop, deadline)
        if index in cache:
            return cache[index]
        if len(cache) >= MAX_READS:
            raise ReferenceError(
                413,
                "caption_work_limit",
                "Caption reading exceeded its work limit. Choose Off or Customize.",
            )
        stage = directory / f"read-{index}"
        with temporary(stage):
            target = stage / "frame.png"
            render.tool(
                [
                    shutil.which("ffmpeg") or "ffmpeg",
                    "-v",
                    "error",
                    "-nostdin",
                    "-xerror",
                    "-threads",
                    "2",
                    "-protocol_whitelist",
                    "file",
                    "-i",
                    str(source["path"]),
                    "-vf",
                    f"setpts=PTS-STARTPTS,select='gte(t,{index}/30)',trim=end_frame=1,"
                    f"{render.resize_filter(*scale, color.inspect_colors(video))},"
                    f"scale={width}:{height}:flags=area",
                    "-frames:v",
                    "1",
                    str(target),
                ],
                stage,
                "caption-frame",
                deadline,
                15,
                temp_budget=16 * 1024 * 1024,
            )
            from PIL import Image

            ocr_image = stage / "ocr-gray.png"
            with Image.open(target) as image:
                image.convert("L").save(ocr_image)
            result = engine.run_command(
                [
                    str(ocr.executable()),
                    str(ocr_image),
                    "stdout",
                    "--tessdata-dir",
                    str(ocr.DATA),
                    "-l",
                    "eng+ara",
                    "--oem",
                    "1",
                    "--psm",
                    "11",
                    "--dpi",
                    "300",
                    "-c",
                    "tessedit_create_tsv=1",
                ],
                stage,
                "caption-tsv",
                deadline,
                15,
                output_limit=128 * 1024,
                temp_budget=16 * 1024 * 1024,
            )
            if result.returncode:
                raise ReferenceError(
                    422,
                    "ocr_failed",
                    "Reference text reading failed. Retry, choose Off or Customize.",
                )
            detections = parse(result.stdout, width, height)
            if detections:
                pixels = appearance.rgb(target, width, height, stage, deadline)
                for detected in detections:
                    rect = detected.rectangle
                    x, y = int(rect.x * width), int(rect.y * height)
                    w, h = max(1, round(rect.width * width)), max(1, round(rect.height * height))
                    crop = bytes(
                        v
                        for row in range(y, min(height, y + h))
                        for v in pixels[
                            3 * (row * width + x) : 3 * (row * width + min(width, x + w))
                        ]
                    )
                    # Bright glyph pixels over the bounded OCR rectangle; not a font identity claim.
                    colors = [
                        tuple(crop[i : i + 3])
                        for i in range(0, len(crop), 3)
                        if max(crop[i : i + 3]) > 200
                    ]
                    if colors:
                        detected.ink = appearance.hex_color(Counter(colors).most_common(1)[0][0])
            cache[index] = detections
            return detections

    counts = Counter(d.text for f in frames for d in read(f))
    ignored = set()
    for text, count in counts.items():
        representative = next(d for values in cache.values() for d in values if d.text == text)
        rect = representative.rectangle
        if count >= len(frames) * 0.8 and (
            rect.y < 0.2 or rect.x < 0.08 or rect.x + rect.width > 0.92
        ):
            ignored.add(text)
    changes = track(read, frames, ignored)
    cues, notes = [], []
    if ignored:
        notes.append(
            "Persistent edge text was excluded as likely logos/usernames; check Customize."
        )
    styles = {}
    for i, (start, detected) in enumerate(changes):
        end = changes[i + 1][0] if i + 1 < len(changes) else total
        if detected is None or end <= start:
            continue
        if len(cues) >= 200:
            raise ReferenceError(
                413, "caption_cue_limit", "More than 200 captions were detected. Open Customize."
            )
        rectangle = detected.rectangle
        # Similar text height/anchor share a fit; color is estimated separately for each cue.
        key = (round(rectangle.height * 50), round(rectangle.y * 20), detected.ink)
        if key not in styles:
            if len(styles) >= MAX_APPEARANCES:
                raise ReferenceError(
                    413,
                    "caption_style_limit",
                    "More than eight caption appearances need review in Customize.",
                )
            fitted, messages = infer(
                project_id,
                source,
                detected,
                (start + end - 1) / 60,
                start / 30,
                end / 30,
                directory,
                stop,
                deadline,
                len(styles),
            )
            styles[key] = fitted
            notes.extend(messages)
        fitted = styles[key].model_copy(deep=True)
        # Every cue gets its own placement, even when reusing a fitted static face.
        fitted.style.color = detected.ink
        fitted.style.horizontal = rectangle.x + rectangle.width / 2
        fitted.style.vertical = rectangle.y + rectangle.height
        cues.append(
            captions.Cue(start=start / 30, end=end / 30, text=detected.text, appearance=fitted)
        )
    if not cues:
        notes.append("No readable changing reference captions were detected; none were invented.")
    notes.extend(
        [
            "Visible words/timing come from OCR. Check wording in Customize.",
            "Timing: 0.5-second discovery, observed changes refined to 1/30 second. "
            "Short or hidden captions can be missed.",
            "Closest available fonts/effects approximate the reference. "
            "Complex effects and shadow are not reconstructed.",
        ]
    )
    if color.source(project_id, stop, deadline) != source:
        raise ReferenceError(409, "source_changed", "Reference changed during caption reading.")
    return (
        cues,
        list(dict.fromkeys(notes)),
        {
            "method": METHOD,
            "reads": len(cache),
            "engine_version": available["engine_version"],
            "assets_commit": available["assets_commit"],
            "source": source["identity"].model_dump(mode="json"),
            "reference_operation_id": source["reference_operation_id"],
        },
    )


def infer(project_id, source, detected, timestamp, start, end, directory, stop, deadline, index):
    stage = directory / f"fit-{index}"
    stage.mkdir()
    safe = True
    color.guard(stop, deadline)
    messages = []
    r = detected.sample_rectangle or detected.rectangle
    # Give outlines a narrow margin without requiring a user crop.
    left, top = max(0.0, r.x - 0.02), max(0.0, r.y - 0.02)
    region = match.Rectangle(
        x=left, y=top, width=min(1 - left, r.width + 0.04), height=min(1 - top, r.height + 0.04)
    )
    font = fonts.Binding()
    if any("\u0600" <= c <= "\u06ff" for c in detected.text):
        font = fonts.builtin("amiri-bold")
    base = captions.Style(
        font=font.choice,
        font_origin="assisted",
        size_percent=min(15.0, max(2.0, r.height * 140)),
        horizontal=r.x + r.width / 2,
        vertical=r.y + r.height,
    )
    animation = captions.Animation()
    try:
        selection = match.read(project_id, deadline)
        result = match.generate(
            project_id,
            match.Request(
                expected_revision=selection["revision"],
                expected_reference_operation_id=source["reference_operation_id"],
                expected_source_hash=source["identity"].media_sha256,
                timestamp_seconds=timestamp,
                rectangle=region,
                text=detected.sample_text or detected.text[:80],
            ),
            stage=(stage, deadline),
        )
        font = fonts.Binding.model_validate(result["ranked"][0]["font"])
        match.review(
            project_id,
            match.Review(
                expected_revision=result["revision"],
                token=result["token"],
                choice=font.choice,
                expected_font_hash=font.sha256,
            ),
        )
        base.font = font.choice
        revision, _, token = appearance.current(project_id, deadline)
        fit_stage = stage / "appearance"
        fit_stage.mkdir()
        row = appearance.record(project_id)
        request = appearance.Request(
            expected_revision=row["revision"] if row else 0,
            expected_selection_revision=revision,
            expected_selection_token=token,
            base_style=base,
        )
        fitted = appearance.generate(project_id, request, stage=(fit_stage, deadline))
        suggestion = appearance.Suggestion.model_validate(fitted["suggestion"])
        base = base.model_copy(update=suggestion.values.model_dump(exclude_none=True))
        if 0.4 <= end - start <= 4:
            motion_stage = stage / "motion"
            motion_stage.mkdir()
            previous = motion.record(project_id)
            analyzed = motion.analyze(
                project_id,
                motion.Request(
                    **request.model_dump(exclude={"expected_revision"}),
                    expected_revision=previous["revision"] if previous else 0,
                    start=start,
                    end=end,
                    region=region,
                ),
                stage=(motion_stage, deadline),
            )
            suggested = motion.Suggestion.model_validate(analyzed["suggestion"])
            if suggested.outcome in {"supported", "static"} and suggested.values.mode is not None:
                animation = captions.Animation.model_validate(
                    suggested.values.model_dump(exclude_none=True)
                )
            else:
                messages.append(
                    "Caption motion was inconclusive; a static supported appearance was used."
                )
    except engine.ProcessCleanupError:
        safe = False
        raise
    except engine.RetrievalFailure as error:
        safe = error.cleanup_safe
        raise
    except ReferenceError as error:
        if error.code == "cleanup_failure":
            safe = False
        if error.code not in {"no_useful_font_match", "appearance_unusable", "invalid_interval"}:
            raise
        messages.append("A caption fit was unreliable; supported font/placement approximated.")
    finally:
        if safe:
            try:
                shutil.rmtree(stage)
            except OSError:
                raise ReferenceError(
                    500, "cleanup_failure", "Caption fitting staging needs cleanup."
                ) from None
    return captions.Appearance(style=base, animation=animation, font_binding=font), messages
