"""Bounded visible-text tracking; never substitutes speech for visible reference words."""

import csv
import hashlib
import io
import json
import math
import shutil
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from pathlib import Path

import caption_appearance as appearance
import caption_ocr as ocr
import caption_readings
import captions
import color_analysis as color
import font_assets as fonts
import font_match as match
import framing
import handwriting_ocr as handwriting
import reference_engine as engine
import reference_motion as motion
import video_render as render
from references import ReferenceError

METHOD = "visible-tsv-adaptive-v7"
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
    glyph_height: float | None = None
    minimum_confidence: float = 100.0
    sharpness: float = 0.0
    frame_index: int | None = None
    recognizer: str = "tesseract"
    needs_review: bool = False


def parse(raw, width, height, uncertain=None):
    try:
        reader = csv.DictReader(
            io.StringIO(raw.decode("utf-8")), delimiter="\t", quoting=csv.QUOTE_NONE
        )
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
        groups, line_boxes = {}, {}
        for row in rows:
            if int(row["level"]) == 4:
                x, y, w, h = (int(row[k]) for k in ("left", "top", "width", "height"))
                if not (
                    0 <= x < width
                    and 0 <= y < height
                    and 0 < w <= width - x
                    and 0 < h <= height - y
                ):
                    raise ValueError("Invalid line geometry")
                line_boxes[tuple(int(row[k]) for k in ("block_num", "par_num", "line_num"))] = (
                    x,
                    y,
                    x + w,
                    y + h,
                )
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
        detections = []
        for key, words in groups.items():
            text = " ".join(word[0] for word in words)
            confidence = sum(word[5] for word in words) / len(words)
            # This is a heuristic rejection rule, not a calibrated confidence threshold.
            x, y = min(w[1] for w in words), min(w[2] for w in words)
            right, bottom = max(w[1] + w[3] for w in words), max(w[2] + w[4] for w in words)
            x, y, right, bottom = line_boxes.get(key, (x, y, right, bottom))
            if confidence < 35 or sum(c.isalnum() for c in text) < 2:
                if (
                    10 <= confidence < 35
                    and (bottom - y) / height >= 0.025
                    and (right - x) / width >= 0.15
                    and y / height > 0.2
                ):
                    if uncertain is None:
                        raise ReferenceError(
                            422, "caption_unreadable", "Visible text needs review."
                        )
                    uncertain.append(
                        match.Rectangle(
                            x=x / width,
                            y=y / height,
                            width=(right - x) / width,
                            height=(bottom - y) / height,
                        )
                    )
                continue
            if (bottom - y) / height < 0.015 or (right - x) / width < 0.035:
                continue
            if len(detections) >= 32:
                raise ReferenceError(
                    413, "caption_region_limit", "Too much visible text needs review."
                )
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
                    minimum_confidence=min(
                        word[5] for word in words if any(c.isalnum() for c in word[0])
                    ),
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


def merge_readings(detections, candidates):
    result = list(detections)
    for candidate in candidates:
        c = candidate.rectangle
        overlaps = []
        for i, found in enumerate(result):
            r = found.rectangle
            intersection = max(0.0, min(r.x + r.width, c.x + c.width) - max(r.x, c.x)) * max(
                0.0, min(r.y + r.height, c.y + c.height) - max(r.y, c.y)
            )
            if intersection / (r.width * r.height + c.width * c.height - intersection) >= 0.5:
                overlaps.append(i)
        if not overlaps:
            result.append(candidate)
        elif candidate.confidence > max(result[i].confidence for i in overlaps):
            result = [d for i, d in enumerate(result) if i not in overlaps]
            result.append(candidate)
    return result


def same(a, b):
    if a is None or b is None:
        return a is b
    return (
        a.text.count("\n") == b.text.count("\n")
        and SequenceMatcher(None, a.text.casefold(), b.text.casefold()).ratio() >= 0.85
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
    lines = [selected]
    for d in sorted(eligible, key=lambda d: abs(d.rectangle.y - r.y)):
        if d is selected:
            continue
        c = d.rectangle
        if (
            abs(c.height - r.height) < r.height * 0.6
            and abs(c.x + c.width / 2 - r.x - r.width / 2) < 0.08
            and all(abs(c.y - line.rectangle.y) > r.height * 0.5 for line in lines)
            and any(abs(c.y - line.rectangle.y) <= r.height * 4.0 for line in lines)
        ):
            lines.append(d)
    if len(lines) > 3:
        raise ReferenceError(
            422, "caption_region_limit", "More than three caption lines need review."
        )
    lines.sort(key=lambda d: d.rectangle.y)
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
        minimum_confidence=min(d.minimum_confidence for d in lines),
        glyph_height=selected.glyph_height or selected.rectangle.height,
        sample_text=selected.sample_text or selected.text[:80],
        sample_rectangle=selected.sample_rectangle or selected.rectangle,
    )


def consolidate(observations):
    """Choose an actually observed block, never synthesize words from plausible sentences."""
    rows = list(observations)
    for i in range(1, len(rows) - 1):
        before, current, after = rows[i - 1][1], rows[i][1], rows[i + 1][1]
        if (
            before
            and after
            and same(before, after)
            and current
            and (
                same(current, before)
                or (
                    current.text in before.text
                    and abs(current.rectangle.y - before.rectangle.y) < 0.1
                )
            )
        ):
            rows[i] = (rows[i][0], before)
    start = 0
    while start < len(rows):
        end = start + 1
        while end < len(rows) and same(rows[start][1], rows[end][1]):
            end += 1
        readings = [d for _, d in rows[start:end] if d is not None]
        if readings:
            counts = Counter(d.text for d in readings)
            winner = max(
                readings,
                key=lambda d: (counts[d.text], reliable_wording(d), d.confidence, d.sharpness),
            )
            rows[start:end] = [(f, winner) for f, _ in rows[start:end]]
        start = end
    return rows


def track(read, frames, ignored):
    known = {}

    def observe(frame):
        detected = dominant(read(frame), ignored)
        known[frame] = detected
        return detected

    observations = consolidate([(f, observe(f)) for f in frames])

    def refine(left, a, right, b):
        if same(a, b):
            if right - left <= 2:
                return []
            middle = (left + right) // 2
            detected = observe(middle)
            if same(detected, a):
                return []
            return refine(left, a, middle, detected) + refine(middle, detected, right, b)
        if right - left <= 1:
            return [(right, b)]
        middle = (left + right) // 2
        detected = observe(middle)
        if same(detected, a):
            detected = a
        elif same(detected, b):
            detected = b
        return refine(left, a, middle, detected) + refine(middle, detected, right, b)

    refined = [observations[0]]
    try:
        for (left, a), (right, b) in zip(observations, observations[1:]):
            refined.extend(refine(left, a, right, b))
    except engine.ProbeTimeout:
        # Unknown frames are not absence: keep only completed pixel observations for review.
        refined = []
        for frame, detected in consolidate(sorted(known.items())):
            if not refined or not same(refined[-1][1], detected):
                refined.append((frame, detected))
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


def reliable_wording(detection):
    # Ambiguous letter/digit readings are retained for review, never repaired by guessing.
    tokens = [word.strip(".,!?;:") for word in detection.text.split()]
    return (
        not detection.needs_review
        and detection.minimum_confidence >= 75
        and sum(c.isalnum() for c in detection.text) >= 4
        and not all(t.isdigit() for t in tokens)
        and not any(t in {"1", "0", "|"} for t in tokens)
    )


def glyph_mask(mask):
    import numpy as np
    from PIL import Image

    a = np.asarray(mask, dtype=np.uint8)
    h, w = a.shape
    parents = []
    boxes = []
    previous = []

    def find(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    for y, row in enumerate(a):
        edges = np.flatnonzero(np.diff(np.r_[False, row > 0, False])).tolist()
        current = []
        at = 0
        for left, right in zip(edges[::2], edges[1::2]):
            while at < len(previous) and previous[at][1] < left - 1:
                at += 1
            linked = []
            j = at
            while j < len(previous) and previous[j][0] <= right:
                linked.append(find(previous[j][2]))
                j += 1
            if linked:
                ident = linked[0]
                for other in linked[1:]:
                    if other != ident:
                        parents[other] = ident
                        b = boxes[other]
                        old = boxes[ident]
                        boxes[ident] = [
                            min(old[0], b[0]),
                            min(old[1], b[1]),
                            max(old[2], b[2]),
                            max(old[3], b[3]),
                        ]
                b = boxes[ident]
                boxes[ident] = [min(b[0], left), min(b[1], y), max(b[2], right), y + 1]
            else:
                if len(parents) >= 16384:
                    raise ReferenceError(
                        413,
                        "caption_region_limit",
                        "Caption component budget reached; review the reference.",
                    )
                ident = len(parents)
                parents.append(ident)
                boxes.append([left, y, right, y + 1])
            current.append((left, right, ident))
        previous = current
    removed = []
    for i, box in enumerate(boxes):
        if find(i) != i:
            continue
        x, y, right, bottom = box
        if bottom - y > h * 0.15 or right - x > w * 0.95:
            removed.append(box)
    clean = a.copy()
    # Reapply the original small glyphs after removing connected large scenery.
    for x, y, right, bottom in removed:
        clean[y:bottom, x:right] = 0
    for i, (x, y, right, bottom) in enumerate(boxes):
        if find(i) == i and (bottom - y <= h * 0.15 and right - x <= w * 0.95):
            clean[y:bottom, x:right] = a[y:bottom, x:right]
    return Image.fromarray(clean)


def color_masks(image):
    from PIL import ImageChops

    channels = image.convert("RGB").split()
    r, g, b = channels
    hue = image.convert("HSV").getchannel("H")
    bright = ImageChops.lighter(ImageChops.lighter(r, g), b).point(lambda v: 255 if v > 130 else 0)
    for i in range(3):
        first, second = channels[(i + 1) % 3], channels[(i + 2) % 3]
        for foreground, background in [
            (channels[i], ImageChops.lighter(first, second)),
            (ImageChops.darker(first, second), channels[i]),
        ]:
            mask = ImageChops.darker(
                ImageChops.subtract(foreground, background).point(lambda v: 255 if v > 55 else 0),
                bright,
            )
            yield glyph_mask(mask), False
            histogram = ImageChops.darker(hue, mask).histogram()
            buckets = [sum(histogram[j : j + 16]) for j in range(0, 256, 16)]
            buckets[0] -= mask.histogram()[0]
            for k in sorted(range(16), key=lambda k: buckets[k], reverse=True)[:3]:
                if buckets[k] < 20:
                    continue
                yield (
                    glyph_mask(
                        ImageChops.darker(
                            mask, hue.point(lambda v: 255 if k * 16 <= v < (k + 1) * 16 else 0)
                        )
                    ),
                    True,
                )


def retry_crops(image, uncertain):
    """Two bounded native-resolution crops, retaining coordinates for timeline/style binding."""
    from PIL import ImageOps

    width, height = image.size
    candidates = []
    for mask, partitioned in color_masks(image):
        bands, start, last = [], None, None
        for y in range(height):
            box = mask.crop((0, y, width, y + 1)).getbbox()
            if box and box[2] - box[0] >= width * 0.04:
                if start is None:
                    start = y
                last = y
            elif start is not None and y - last > 4:
                bands.append((start, last + 1))
                start = last = None
        if start is not None:
            bands.append((start, last + 1))
        boxes = []
        for top, bottom in bands:
            if not 0.015 * height <= bottom - top <= 0.15 * height or top > 0.94 * height:
                continue
            box = mask.crop((0, top, width, bottom)).getbbox()
            if not box or not 0.06 * width <= box[2] - box[0] <= 0.95 * width:
                continue
            region = mask.crop((box[0], top, box[2], bottom))
            density = region.histogram()[255] / (region.width * region.height)
            runs, active = 0, False
            for x in range(region.width):
                present = bool(region.crop((x, 0, x + 1, region.height)).getbbox())
                if present and not active:
                    runs += 1
                active = present
            if 0.02 <= density <= 0.35 and runs >= 3 and region.width / region.height >= 3:
                boxes.append((box[0], top, box[2], bottom))
                candidates.append(
                    (
                        region.width
                        * min(runs, 40)
                        * (1 - density)
                        * (0.1 if box[0] <= width * 0.01 or box[2] >= width * 0.99 else 1)
                        * (0.5 if partitioned else 1),
                        mask,
                        boxes,
                    )
                )
    if candidates:
        _, mask, boxes = max(candidates, key=lambda row: row[0])
    else:
        mask, boxes = None, []
    crops = []
    if boxes:
        # Preserve all lines in the selected block; up to three lines retain top-to-bottom ordering.
        best = max(boxes, key=lambda r: r[2] - r[0])
        near = [r for r in boxes if abs(r[1] - best[1]) < height * 0.18]
        box = (
            min(r[0] for r in near),
            min(r[1] for r in near),
            max(r[2] for r in near),
            max(r[3] for r in near),
        )
        lines = handwriting.line_crops(image, ImageOps.invert(mask.crop(box)), box)
        if lines:
            boxes = [
                (
                    round(r[0] * width),
                    round(r[1] * height),
                    round((r[0] + r[2]) * width),
                    round((r[1] + r[3]) * height),
                )
                for r, _ in lines
            ]
            box = (
                min(r[0] for r in boxes),
                min(r[1] for r in boxes),
                max(r[2] for r in boxes),
                max(r[3] for r in boxes),
            )
        margin = max(8, round(height * 0.01))
        box = (
            max(0, box[0] - margin),
            max(0, box[1] - margin),
            min(width, box[2] + margin),
            min(height, box[3] + margin),
        )
        crops.append((ImageOps.invert(mask.crop(box)), box, "eng+ara"))
    if crops:
        box = crops[0][1]
        crops.append((ImageOps.autocontrast(image.crop(box).convert("L")), box, "eng+ara"))
    elif uncertain:
        r = max(uncertain, key=lambda r: r.width * r.height)
        box = (
            max(0, int((r.x - 0.02) * width)),
            max(0, int((r.y - 0.02) * height)),
            min(width, int((r.x + r.width + 0.02) * width)),
            min(height, int((r.y + r.height + 0.02) * height)),
        )
        crops.append((ImageOps.autocontrast(image.crop(box).convert("L")), box, "eng+ara"))
    return crops[:2]


def map_crop_detection(detection, box, crop_size, factor, native_size):
    r = detection.rectangle
    left = box[0] + (r.x * crop_size[0] - 16) / factor
    top = box[1] + (r.y * crop_size[1] - 16) / factor
    right = left + r.width * crop_size[0] / factor
    bottom = top + r.height * crop_size[1] / factor
    # OCR can interpret the crop padding as text; it has no corresponding source pixels.
    if not (box[0] <= left < right <= box[2] and box[1] <= top < bottom <= box[3]):
        return None
    detection.rectangle = match.Rectangle(
        x=left / native_size[0],
        y=top / native_size[1],
        width=(right - left) / native_size[0],
        height=(bottom - top) / native_size[1],
    )
    detection.sample_text = None
    detection.sample_rectangle = None
    return detection


def frame_seek(video, index):
    try:
        start = float(video.get("start_time", "nan"))
    except (TypeError, ValueError):
        start = math.nan
    if math.isfinite(start) and start >= 0:
        # Absolute input timestamps avoid shifting the video when audio starts earlier.
        return ["-ss", str(start + index / 30), "-seek_timestamp", "1"], "setpts=PTS-STARTPTS,"
    return [], f"setpts=PTS-STARTPTS,select='gte(t,{index}/30)',trim=end_frame=1,"


def fit_review_width(project_id, cues, directory, stop, deadline):
    source = render.footage.source(project_id, stop, deadline)
    video, _, _ = render.probe(source["path"], directory, deadline, temp_budget=16 * 1024 * 1024)
    _, canvas, _ = framing.geometry(video, framing.read(project_id).settings)
    widths = {}
    result = []
    for i, cue in enumerate(cues):
        appearance_value = cue.appearance or captions.Appearance(
            style=captions.Style(), font_binding=fonts.Binding()
        )
        style = appearance_value.style.model_copy(deep=True)
        if style.font_origin == "assisted" and style.outline_percent is None:
            style.outline_percent = 0.0
        binding = appearance_value.font_binding or fonts.selected(project_id, style.font)
        appearance_value = appearance_value.model_copy(update={"font_binding": binding})
        if style.font != binding.choice:
            raise ReferenceError(
                422, "caption_font_unavailable", "Caption style and font identity do not match."
            )
        longest = max(cue.text.split("\n"), key=len)
        key = (appearance_value.font_binding.sha256, longest)
        if key not in widths:
            if len(widths) >= 24:
                raise ReferenceError(
                    413, "caption_appearance_limit", "Caption width calibration reached its bound."
                )
            with temporary(directory / f"review-width-{i}") as stage:
                prepared = fonts.prepare(
                    project_id, appearance_value.font_binding, [cue], stage, deadline
                )
                box, _ = appearance.render_sample(
                    style.model_copy(
                        update={
                            "size_percent": 10.0,
                            "color": "white",
                            "outline_percent": 0.0,
                            "shadow_percent": 0.0,
                            "horizontal": 0.5,
                            "vertical": 0.5,
                        }
                    ),
                    appearance_value.font_binding,
                    longest,
                    4096,
                    256,
                    stage,
                    prepared,
                    deadline,
                    with_mask=False,
                )
                widths[key] = box[2]
        anchor = style.horizontal if style.horizontal is not None else 0.5
        available = {"left": 1 - anchor, "right": anchor, "center": 2 * min(anchor, 1 - anchor)}[
            style.alignment
        ]
        if available <= 0:
            raise ReferenceError(
                422, "caption_appearance_limit", "Move the caption anchor inside the output canvas."
            )
        size = style.size_percent or {"small": 3.5, "medium": 5.0, "large": 7.0}[style.size]
        style.size_percent = fit_width(size, widths[key], min(0.9, available), canvas)
        result.append(
            cue.model_copy(
                update={"appearance": appearance_value.model_copy(update={"style": style})}
            )
        )
    return result


def handwriting_progress(stage, names):
    progress = stage / "progress.json"
    if not progress.is_file():
        return {"results": [], "engine": ""}
    if progress.stat().st_size > 128 * 1024:
        raise ReferenceError(422, "ocr_output_invalid", "Handwriting progress exceeds its bound.")
    return validate_handwriting(progress.read_bytes(), names, allow_partial=True)


def run_handwriting(args, stage, names, deadline, stage_limit=70, *, persist=None):
    try:
        result = engine.run_command(
            args,
            stage,
            "caption-handwriting",
            deadline,
            stage_limit,
            output_limit=128 * 1024,
            temp_budget=16 * 1024 * 1024,
        )
    except (engine.ProbeTimeout, engine.ProbeInterrupted) as error:
        # Owned termination is confirmed before reading or persisting its checkpoint.
        payload = handwriting_progress(stage, names)
        if persist:
            persist(payload)
        if isinstance(error, engine.ProbeInterrupted):
            raise
        return payload, True
    if result.returncode:
        raise ReferenceError(
            503,
            "handwriting_unavailable",
            "Local handwriting reading failed. "
            "Run its explicit setup or continue without captions.",
        )
    payload = validate_handwriting(result.stdout, names)
    if persist:
        persist(payload)
    return payload, False


def cached_handwriting(project_id, namespace, stage, names, deadline):
    saved_results, saved_engine = [], ""
    for name in names:
        value = caption_readings.read(project_id, "handwriting", namespace, name)
        if value is not None:
            saved = validate_handwriting(value, [name])
            saved_results.extend(saved["results"])
            saved_engine = saved["engine"]
    cached_names = {row["name"] for row in saved_results}
    pending = [name for name in names if name not in cached_names]

    def persist(payload):
        for row in payload["results"]:
            value = json.dumps({"engine": payload["engine"], "results": [row]}).encode()
            caption_readings.save(project_id, "handwriting", namespace, row["name"], value)

    if pending:
        request = stage / "request.json"
        request.write_text(json.dumps(pending), encoding="utf-8")
        payload, timed_out = run_handwriting(
            [
                str(handwriting.executable()),
                str(Path(handwriting.__file__)),
                "recognize",
                str(request),
            ],
            stage,
            pending,
            deadline,
            persist=persist,
        )
    else:
        payload, timed_out = {"results": [], "engine": saved_engine}, False
    payload["results"].extend(saved_results)
    return payload, timed_out, len(pending), len(saved_results)


def native_geometry(video):
    width, height = framing.display_dimensions(video)
    factor = min(1.0, 1920 / max(width, height))
    return tuple(max(2, math.floor(v * factor / 2) * 2) for v in (width, height))


def fit_width(size, measured_width, fraction, canvas):
    width, height = canvas
    limit = 10 * fraction * width * 256 / (measured_width * height)
    fitted = max(0.5, min(size, limit))
    if measured_width * height / 256 * fitted / 10 > width * 0.96:
        raise ReferenceError(
            422,
            "caption_appearance_limit",
            "Caption text cannot fit this canvas within supported sizes. "
            "Correct or shorten the cue.",
        )
    return fitted


def glyph_similarity(a, b):
    mass = sum(a) + sum(b)
    return 2 * sum(min(x, y) for x, y in zip(a, b)) / mass if mass else 0.0


def visible_shape(image, rectangle, ink):
    """Match observed glyph pixels independently of a fresh word recognition."""
    import colorsys

    import numpy as np
    from PIL import Image

    x, y = round(rectangle.x * image.width), round(rectangle.y * image.height)
    right = round((rectangle.x + rectangle.width) * image.width)
    bottom = round((rectangle.y + rectangle.height) * image.height)
    crop = image.crop((x, max(0, y - 5), right, min(image.height, bottom + 5)))
    hsv = np.asarray(crop.convert("HSV"))
    rgb = tuple(int(ink[i : i + 2], 16) / 255 for i in (1, 3, 5))
    hue, saturation, _ = colorsys.rgb_to_hsv(*rgb)
    if saturation > 0.2:
        distance = np.abs(hsv[:, :, 0].astype(float) - hue * 255)
        mask = (np.minimum(distance, 255 - distance) < 16) & (hsv[:, :, 1] > 40)
    else:
        mask = hsv[:, :, 1] < 40
    # Below this floor the pixels cannot establish readable glyph continuity.
    peak = float(np.quantile(hsv[:, :, 2][mask], 0.99)) if mask.any() else 0.0
    mask &= hsv[:, :, 2] > max(20, peak * 0.7)
    return Image.fromarray((mask * 255).astype("uint8")).resize((128, 32)).convert("RGB").tobytes()


def read_tracked(index, read_frame, cache, signatures, prototypes):
    found = read_frame(index)
    cache[index] = (
        recover_lines(index, signatures.get(index, []), prototypes, found) if prototypes else found
    )
    return cache[index]


def recover_lines(index, signatures, prototypes, fallback):
    recovered = []
    for rectangle, signature in signatures:
        candidates = [
            (f, d)
            for f, r, shape, d in prototypes
            if abs(f - index) <= 90
            and abs(rectangle.y - r.y) < 0.025
            and 0.8 <= rectangle.width / r.width <= 1.25
            and glyph_similarity(signature, shape) >= 0.9
        ]
        model = [(f, d) for f, d in candidates if d.recognizer == "trocr"]
        if len({f for f, _ in model}) >= 2:
            candidates = model
        if candidates:
            counts = Counter(d.text for _, d in candidates)
            chosen = max(
                (d for _, d in candidates),
                key=lambda d: (counts[d.text], d.confidence, d.sharpness),
            )
            conflicting = [
                d
                for d in fallback
                if overlaps_line(d.rectangle, rectangle)
                and reliable_wording(d)
                and word_key(d.text) != word_key(chosen.text)
            ]
            if conflicting:
                continue
            recovered.append(
                replace(
                    chosen,
                    rectangle=rectangle,
                    glyph_height=rectangle.height,
                    sample_rectangle=None,
                    sample_text=None,
                    frame_index=index,
                )
            )
    if not recovered:
        return fallback
    return recovered + [
        d for d in fallback if not any(overlaps_line(d.rectangle, r.rectangle) for r in recovered)
    ]


def select_handwriting(rows, limit, *, signatures=None, readings=None):
    if signatures is None:
        count = min(len(rows), limit)
        if count == 1:
            return [rows[len(rows) // 2]]
        return [rows[round(i * (len(rows) - 1) / (count - 1))] for i in range(count)]
    groups = []
    for row in rows:
        index, rect, _ = row
        rectangle = match.Rectangle(x=rect[0], y=rect[1], width=rect[2], height=rect[3])
        shapes = [
            shape for box, shape in signatures.get(index, []) if overlaps_line(box, rectangle)
        ]
        if not shapes:
            continue
        shape = shapes[0]
        group = next(
            (
                g
                for g in groups
                if abs(g[0][0] - index) <= 90
                and abs(g[0][1][1] - rect[1]) < 0.025
                and 0.9 <= rect[2] / g[0][1][2] <= 1.1
                and glyph_similarity(g[0][3], shape) >= 0.9
            ),
            None,
        )
        item = (*row, shape)
        if group is None:
            groups.append([item])
        else:
            group.append(item)

    def priority(group):
        ambiguous = any(
            any(
                not reliable_wording(d) and any(t in {"1", "0", "|"} for t in d.text.split())
                for d in (readings or {}).get(row[0], [])
            )
            for row in group
        )
        return ambiguous, len({row[0] for row in group})

    chosen = []
    for group in sorted(groups, key=priority, reverse=True):

        def sharp(row):
            return max((d.sharpness for d in (readings or {}).get(row[0], [])), default=0)

        first = max(group, key=sharp)
        others = [row for row in group if abs(row[0] - first[0]) >= 3]
        pair = [first] + ([max(others, key=sharp)] if others else [])
        if len(chosen) + len(pair) > limit:
            continue
        chosen.extend(tuple(row[:3]) for row in pair)
    return chosen


def word_key(text):
    import re

    return tuple(re.findall(r"[^\W_]+(?:['’][^\W_]+)*", text.casefold().replace("’", "'")))


def observed_punctuation(model, reader):
    """Use punctuation read from the same pixels only when word positions agree."""
    import re

    model_words = re.findall(r"[^\W_]+(?:['’][^\W_]+)*", model)
    reader_words = re.findall(r"[^\W_]+(?:['’][^\W_]+)*", reader)
    if len(model_words) != len(reader_words) or not all(
        a.casefold().replace("’", "'") == b.casefold().replace("’", "'")
        or (b in {"1", "0"} and len(a) == 1 and a.isalpha())
        for a, b in zip(model_words, reader_words)
    ):
        return model
    words = iter(model_words)
    return re.sub(r"[^\W_]+(?:['’][^\W_]+)*", lambda _: next(words), reader)


def overlaps_line(a, b):
    intersection = max(0, min(a.x + a.width, b.x + b.width) - max(a.x, b.x)) * max(
        0, min(a.y + a.height, b.y + b.height) - max(a.y, b.y)
    )
    return intersection / min(a.width * a.height, b.width * b.height) >= 0.5


def validate_handwriting(raw, names, *, allow_partial=False):
    try:
        value = json.loads(raw)
        rows = value["results"]
        if (
            not isinstance(rows, list)
            or len(rows) > len(names)
            or len({r["name"] for r in rows}) != len(rows)
            or not {r["name"] for r in rows}.issubset(names)
            or (not allow_partial and len(rows) != len(names))
            or not isinstance(value["engine"], str)
            or len(value["engine"]) > 180
        ):
            raise ValueError("Missing handwriting output")
        for row in rows:
            if (
                not isinstance(row["text"], str)
                or len(row["text"]) > 200
                or type(row["score"]) not in {int, float}
                or not math.isfinite(row["score"])
                or not 0 <= row["score"] <= 1
                or type(row["capped"]) is not bool
            ):
                raise ValueError("Invalid handwriting values")
            if row["text"].strip():
                captions.Cue(start=0.0, end=1.0, text=row["text"])
        return value
    except (KeyError, TypeError, ValueError, UnicodeError):
        raise ReferenceError(
            422, "ocr_output_invalid", "Handwriting output could not be read safely."
        ) from None


def detect(project_id, source, directory, stop, deadline):
    started = time.monotonic()
    overall_deadline = deadline
    deadline = min(deadline, time.monotonic() + TOTAL_SECONDS)
    engine._control.stop = stop
    reading_deadline = min(deadline, time.monotonic() + 220)
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
    footage_source = render.footage.source(project_id, stop, deadline)
    output_video, _, _ = render.probe(
        footage_source["path"], directory, deadline, temp_budget=16 * 1024 * 1024
    )
    _, output_canvas, _ = framing.geometry(output_video, framing.read(project_id).settings)
    canvas = native_geometry(video)
    scale = canvas
    factor = 960 / max(canvas)
    width, height = (max(2, math.floor(v * factor / 2) * 2) for v in canvas)
    total = max(1, math.floor(duration * 30 + 0.00001))
    frames = sorted(set([0, *range(15, total, 15), total - 1]))
    cache = {}
    reads = [0]
    unresolved = {}
    script_votes = Counter()
    crop_cache = {}
    source_identity = source["identity"].model_dump(mode="json")
    ocr_namespace = caption_readings.identity(
        source_identity,
        {
            "engine": available["engine_version"],
            "assets": ocr.MANIFEST,
            "method": "tsv-v1",
        },
    )
    model_namespace = caption_readings.identity(
        source_identity, {"assets": handwriting.MANIFEST, "algorithm": "fp32-greedy80-v1"}
    )
    cached_ocr_reads = 0
    cached_handwriting_requests = 0
    handwriting_ready = handwriting.available()
    handwriting_images, handwriting_rows = {}, []
    handwriting_bytes = 0
    handwriting_engine = None
    handwriting_requests = 0
    handwriting_completed_requests = 0
    signatures = {}
    prototypes = []
    discovery = set(frames)

    def cached_ocr(args, stage, name):
        nonlocal cached_ocr_reads
        key = hashlib.sha256(
            json.dumps([args[2:], hashlib.sha256(Path(args[1]).read_bytes()).hexdigest()]).encode()
        ).hexdigest()
        if key in crop_cache:
            return crop_cache[key]
        saved = caption_readings.read(project_id, "ocr", ocr_namespace, key)
        if saved is not None:
            cached_ocr_reads += 1
            import subprocess

            result = subprocess.CompletedProcess(args, 0, saved, b"")
            if len(crop_cache) >= 32:
                crop_cache.pop(next(iter(crop_cache)))
            crop_cache[key] = result
            return result
        reads[0] += 1
        result = engine.run_command(
            args,
            stage,
            name,
            reading_deadline,
            15,
            output_limit=128 * 1024,
            temp_budget=16 * 1024 * 1024,
        )
        if result.returncode == 0:
            from PIL import Image

            with Image.open(args[1]) as image:
                parse(result.stdout, image.width, image.height, [])
            caption_readings.save(project_id, "ocr", ocr_namespace, key, result.stdout)
            if len(crop_cache) >= 32:
                crop_cache.pop(next(iter(crop_cache)))
            crop_cache[key] = result
        return result

    def read_frame(index):
        nonlocal handwriting_bytes
        if index in cache:
            return cache[index]
        color.guard(stop, reading_deadline)
        if reads[0] >= MAX_READS or reading_deadline - time.monotonic() < 20:
            unresolved[index] = "Frame not observed: caption reading budget reached"
            raise engine.ProbeTimeout("Caption reading budget reached")
        stage = directory / f"read-{index}"
        with temporary(stage):
            ambiguous = False
            uncertain = []
            seek, timing_filter = frame_seek(video, index)
            for sampling in ("area", "bicubic") if factor > 1 else ("area",):
                target = stage / f"frame-{sampling}.png"
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
                        *seek,
                        "-i",
                        str(source["path"]),
                        "-vf",
                        timing_filter
                        + f"{render.resize_filter(*scale, color.inspect_colors(video))},"
                        f"scale={width}:{height}:flags={sampling}",
                        "-frames:v",
                        "1",
                        str(target),
                    ],
                    stage,
                    "caption-frame",
                    reading_deadline,
                    15,
                    temp_budget=16 * 1024 * 1024,
                )
                from PIL import Image

                ocr_image = stage / "ocr-gray.png"
                with Image.open(target) as image:
                    image.convert("L").save(ocr_image)
                detections = []
                with Image.open(target) as image:
                    colored_region = bool(retry_crops(image, []))
                # Prefer a detected colored-text crop to full-frame texture recognition.
                languages = () if colored_region else ("eng", "ara")
                ambiguous = colored_region
                if not colored_region and script_votes["latin"] > max(
                    12, 2 * script_votes["arabic"]
                ):
                    languages = ("eng",)
                elif not colored_region and script_votes["arabic"] > max(
                    12, 2 * script_votes["latin"]
                ):
                    languages = ("ara",)
                for language in languages:
                    color.guard(stop, reading_deadline)
                    if reads[0] >= MAX_READS:
                        unresolved[index] = "OCR work budget reached"
                        break
                    result = cached_ocr(
                        [
                            str(ocr.executable()),
                            str(ocr_image),
                            "stdout",
                            "--tessdata-dir",
                            str(ocr.DATA),
                            "-l",
                            language,
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
                        f"caption-tsv-{language}",
                    )
                    if result.returncode:
                        raise ReferenceError(
                            422,
                            "ocr_failed",
                            "Reference text reading failed. Retry, choose Off or Customize.",
                        )
                    try:
                        candidates = parse(result.stdout, width, height, uncertain)
                    except ReferenceError as error:
                        if error.code not in {
                            "caption_unreadable",
                            "caption_region_limit",
                            "ocr_output_invalid",
                        }:
                            raise
                        ambiguous = True
                        continue
                    detections = merge_readings(detections, candidates)
                if detections:
                    break
            # Native-resolution, contrast-separated crops recover thin/colored text lost in gray.
            from PIL import Image

            with Image.open(target) as image:
                native = image.convert("RGB")
            if (
                uncertain
                or not detections
                or any(not reliable_wording(d) for d in detections)
                or (handwriting_ready and colored_region)
            ):
                native_target = stage / "native.png"
                native_width = min(1920, canvas[0])
                native_height = max(2, round(canvas[1] * native_width / canvas[0]))
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
                        *seek,
                        "-i",
                        str(source["path"]),
                        "-vf",
                        timing_filter
                        + f"{render.resize_filter(*scale, color.inspect_colors(video))},"
                        f"scale={native_width}:{native_height}",
                        "-frames:v",
                        "1",
                        str(native_target),
                    ],
                    stage,
                    "caption-native-frame",
                    reading_deadline,
                    15,
                    temp_budget=16 * 1024 * 1024,
                )
                with Image.open(native_target) as image:
                    native = image.convert("RGB")
                for attempt, (crop, box, language) in enumerate(retry_crops(native, uncertain)):
                    if script_votes["latin"] > 2 * script_votes["arabic"]:
                        language = "eng"
                    elif script_votes["arabic"] > 2 * script_votes["latin"]:
                        language = "ara"
                    color.guard(stop, reading_deadline)
                    if reads[0] >= MAX_READS:
                        unresolved[index] = "OCR work budget reached"
                        break
                    factor_crop = min(2, 1600 / max(crop.size))
                    crop = crop.resize(
                        (
                            max(8, round(crop.width * factor_crop)),
                            max(8, round(crop.height * factor_crop)),
                        )
                    )
                    from PIL import ImageOps

                    crop = ImageOps.expand(crop, border=16, fill=255)
                    path = stage / f"retry-{attempt}.png"
                    crop.save(path)
                    result = cached_ocr(
                        [
                            str(ocr.executable()),
                            str(path),
                            "stdout",
                            "--tessdata-dir",
                            str(ocr.DATA),
                            "-l",
                            language,
                            "--oem",
                            "1",
                            "--psm",
                            "6",
                            "--dpi",
                            "300",
                            "-c",
                            "tessedit_create_tsv=1",
                        ],
                        stage,
                        "caption-crop-retry",
                    )
                    if result.returncode:
                        unresolved[index] = "OCR crop retry failed"
                        continue
                    low = []
                    try:
                        found = parse(result.stdout, crop.width, crop.height, low)
                    except ReferenceError as error:
                        if error.code not in {
                            "caption_unreadable",
                            "caption_region_limit",
                            "ocr_output_invalid",
                        }:
                            raise
                        unresolved[index] = "Detector output requires review"
                        continue
                    for d in found:
                        if d.confidence >= 70 and len(d.text) >= 8:
                            script_votes["latin"] += sum(
                                c.isascii() and c.isalpha() for c in d.text
                            )
                            script_votes["arabic"] += sum("\u0600" <= c <= "\u06ff" for c in d.text)
                        if map_crop_detection(d, box, crop.size, factor_crop, native.size) is None:
                            d.minimum_confidence = 0.0
                            unresolved[index] = "OCR padding artifact requires review"
                    reliable = [d for d in found if reliable_wording(d)]
                    if found or low:
                        ambiguous = len(reliable) != len(found) or bool(low)
                    readable = [
                        d
                        for d in found
                        if d.confidence >= 55
                        and d.minimum_confidence >= 35
                        and sum(c.isalnum() for c in d.text) >= 4
                    ]
                    if readable:
                        detections = readable
                    if len(reliable) == len(found) and reliable:
                        break
            if handwriting_ready or colored_region:
                candidates = retry_crops(native, [])
                if candidates:
                    mask, box, _ = candidates[0]
                    lines = handwriting.line_crops(native, mask, box)
                    if len(lines) > 3:
                        unresolved[index] = "More than three visible lines require review"
                        lines = []
                    for rect, variants in lines:
                        rectangle = match.Rectangle(
                            x=rect[0], y=rect[1], width=rect[2], height=rect[3]
                        )
                        if any(
                            any("\u0600" <= c <= "\u06ff" for c in d.text)
                            and overlaps_line(d.rectangle, rectangle)
                            for d in detections
                        ):
                            continue
                        from PIL import ImageOps

                        binary = variants[0]
                        shape = (
                            ImageOps.invert(
                                binary.crop((16, 16, binary.width - 16, binary.height - 16))
                            )
                            .resize((128, 32))
                            .tobytes()
                        )
                        signatures.setdefault(index, []).append((rectangle, shape))
                        if not handwriting_ready or index not in discovery:
                            continue
                        if any(
                            reliable_wording(d) and overlaps_line(d.rectangle, rectangle)
                            for d in detections
                        ):
                            continue
                        names = []
                        for crop in variants:
                            crop.thumbnail((2048, 256))
                            encoded = io.BytesIO()
                            crop.save(encoded, format="PNG")
                            raw = encoded.getvalue()
                            name = hashlib.sha256(raw).hexdigest() + ".png"
                            if name not in handwriting_images:
                                if (
                                    len(raw) > 256 * 1024
                                    or handwriting_bytes + len(raw) > 8 * 1024 * 1024
                                ):
                                    unresolved[index] = "Handwriting crop memory bound reached"
                                    break
                                handwriting_images[name] = raw
                                handwriting_bytes += len(raw)
                            names.append(name)
                        if len(names) == 2:
                            handwriting_rows.append((index, rect, names))
            # Do not export isolated texture-like glyphs or low-confidence word fragments.
            reliable = [d for d in detections if reliable_wording(d)]
            if uncertain and not reliable or ambiguous:
                unresolved[index] = "Text could not be read confidently"
            if len(detections) > 3:
                unresolved[index] = "More than three visible lines require review"
            detections = [
                d
                for d in detections
                if d.confidence >= 55
                and d.minimum_confidence >= 35
                and sum(c.isalnum() for c in d.text) >= 4
            ]
            if detections:
                pixels = appearance.rgb(target, width, height, stage, deadline)
                for detected in detections:
                    detected.frame_index = index
                    from PIL import ImageFilter, ImageStat

                    box = (
                        int(detected.rectangle.x * native.width),
                        int(detected.rectangle.y * native.height),
                        max(
                            1, int((detected.rectangle.x + detected.rectangle.width) * native.width)
                        ),
                        max(
                            1,
                            int((detected.rectangle.y + detected.rectangle.height) * native.height),
                        ),
                    )
                    detected.sharpness = ImageStat.Stat(
                        native.crop(box).convert("L").filter(ImageFilter.FIND_EDGES)
                    ).var[0]
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
                    chromatic = [c for c in colors if max(c) - min(c) > 70]
                    if len(chromatic) >= 20:
                        colors = chromatic
                    if colors:
                        detected.ink = appearance.hex_color(Counter(colors).most_common(1)[0][0])
                        glyph_rows = [
                            i // 3 // min(w, width - x)
                            for i in range(0, len(crop), 3)
                            if max(crop[i : i + 3]) > 200
                        ]
                        detected.glyph_height = (max(glyph_rows) - min(glyph_rows) + 1) / height
            if index in signatures:
                normalized = []
                for rectangle, shape in signatures[index]:
                    related = [d for d in detections if overlaps_line(d.rectangle, rectangle)]
                    if related:
                        ink = max(related, key=lambda d: d.confidence).ink
                        shape = visible_shape(native, rectangle, ink)
                    normalized.append((rectangle, shape))
                signatures[index] = normalized
            # Word failure does not imply disappearance: verify the same glyphs in this frame.
            for frame, rectangle, _, detected in prototypes:
                if abs(frame - index) <= 90 and reliable_wording(detected):
                    signatures.setdefault(index, []).append(
                        (rectangle, visible_shape(native, rectangle, detected.ink))
                    )
            cache[index] = detections
            return detections

    def read(index):
        try:
            return read_tracked(index, read_frame, cache, signatures, prototypes)
        except engine.ProbeTimeout:
            # The contained runner confirms termination before raising a timeout.
            unresolved[index] = "Frame not observed: caption reading deadline reached"
            raise

    for f in frames:
        try:
            read(f)
        except engine.ProbeTimeout:
            frames = [frame for frame in frames if frame in cache]
            break
    if not frames:
        raise ReferenceError(
            422, "caption_unreadable", "Caption frames could not be observed. Retry reading."
        )
    if handwriting_rows and script_votes["latin"] > script_votes["arabic"]:
        allowed = min(handwriting.MAX_LINES // 2, (MAX_READS - reads[0]) // 2)
        selected = select_handwriting(
            handwriting_rows, allowed, signatures=signatures, readings=cache
        )
        names = list(dict.fromkeys(name for _, _, variants in selected for name in variants))
        if names and reading_deadline - time.monotonic() > 30:
            with temporary(directory / "handwriting") as stage:
                for name in names:
                    (stage / name).write_bytes(handwriting_images[name])
                request = stage / "request.json"
                request.write_text(json.dumps(names), encoding="utf-8")
                try:
                    payload, timed_out, attempted, restored = cached_handwriting(
                        project_id,
                        model_namespace,
                        stage,
                        names,
                        reading_deadline,
                    )
                    handwriting_requests = attempted
                    cached_handwriting_requests += restored
                    reads[0] += attempted
                    if timed_out:
                        unresolved[0] = (
                            "Handwriting deadline reached; completed readings retained for review"
                        )
                    handwriting_completed_requests = len(payload["results"])
                    handwriting_engine = payload["engine"]
                    results = {row["name"]: row for row in payload["results"]}
                    observed = []
                    for index, rect, variants in selected:
                        choices = [results[name] for name in variants if name in results]
                        if not choices:
                            unresolved[index] = "Handwriting reading did not complete"
                            continue
                        chosen = max(choices, key=lambda row: row["score"])
                        text = chosen["text"]
                        if (
                            not text.strip()
                            or sum(c.isalnum() for c in text) < 4
                            or chosen["capped"]
                        ):
                            unresolved[index] = (
                                "Handwriting reading was empty or reached its token bound"
                            )
                            continue
                        d = Detection(
                            text,
                            match.Rectangle(x=rect[0], y=rect[1], width=rect[2], height=rect[3]),
                            chosen["score"] * 100,
                            minimum_confidence=100,
                            frame_index=index,
                            recognizer="trocr",
                            needs_review=len(choices) < len(variants)
                            or len({word_key(row["text"]) for row in choices}) != 1
                            or chosen["score"] < 0.85,
                        )
                        observed.append((index, d))
                    for index, d in observed:
                        agreement = len(
                            {
                                f
                                for f, other in observed
                                if word_key(other.text) == word_key(d.text)
                                and abs(other.rectangle.y - d.rectangle.y) < 0.08
                                and abs(f - index) <= 90
                            }
                        )
                        d.needs_review = d.needs_review or agreement < 2
                        related = [
                            old for old in cache[index] if overlaps_line(old.rectangle, d.rectangle)
                        ]
                        if not d.needs_review and related:
                            reader = max(related, key=lambda old: old.confidence)
                            d.text = observed_punctuation(d.text, reader.text)
                        if any(
                            reliable_wording(old)
                            and old.minimum_confidence >= 90
                            and old.text.casefold() == d.text.casefold()
                            for old in cache[index]
                        ):
                            continue
                        cache[index] = merge_readings(
                            [
                                existing
                                for existing in cache[index]
                                if not overlaps_line(existing.rectangle, d.rectangle)
                            ],
                            [d],
                        )
                        if d.needs_review or not reliable_wording(d):
                            unresolved[index] = "Handwriting variants or adjacent frames disagree"
                    # Selected frames retain their own geometry/color; uncertainty stays disclosed.
                    for index in {f for f, _ in observed}:
                        rows = cache[index]
                        if rows and all(reliable_wording(d) for d in rows):
                            unresolved.pop(index, None)
                        for d in rows:
                            if d.recognizer == "trocr":
                                nearest = [
                                    other
                                    for values in cache.values()
                                    for other in values
                                    if other.recognizer == "tesseract"
                                    and abs(other.rectangle.y - d.rectangle.y) < 0.08
                                ]
                                if nearest:
                                    d.ink = min(
                                        nearest,
                                        key=lambda other: abs(other.rectangle.x - d.rectangle.x),
                                    ).ink
                                d.glyph_height = d.rectangle.height
                except engine.ProbeTimeout:
                    unresolved[0] = "Handwriting reading reached its contained deadline"
        else:
            unresolved[0] = "Handwriting inference budget unavailable"
    handwriting_images.clear()
    for index, shapes in signatures.items():
        for rectangle, shape in shapes:
            for d in cache.get(index, []):
                if (
                    overlaps_line(d.rectangle, rectangle)
                    and d.rectangle.width / rectangle.width >= 0.8
                ):
                    prototypes.append((index, rectangle, shape, d))
    for index in cache:
        cache[index] = recover_lines(index, signatures.get(index, []), prototypes, cache[index])
    counts = Counter(d.text for f in frames for d in read(f))

    # Read nearby frames at their own timestamps; never assign recovered text to an earlier frame.
    nearby = sorted({min(total - 1, f + 3) for f in list(unresolved)[:8]} - set(frames))
    for f in nearby:
        try:
            read(f)
        except engine.ProbeTimeout:
            break
    frames = sorted(set(frames) | {f for f in nearby if f in cache})
    ignored = set()
    for values in cache.values():
        for d in values:
            if counts[d.text] < 2:
                unresolved[min(frames)] = "Unstable isolated readings require review"
    creator = source["identity"].canonical_url.split("/@")[-1].split("/")[0].casefold()
    for text in counts:
        compact = "".join(c for c in text.casefold() if c.isalnum())
        if creator and SequenceMatcher(None, compact, creator).ratio() >= 0.85:
            ignored.add(text)
    changes = track(read, frames, ignored)
    stable = Counter(
        d.text
        for _, d in consolidate(
            [(f, dominant(values, ignored)) for f, values in sorted(cache.items())]
        )
        if d
    )
    cues, notes = [], []
    if ignored:
        notes.append(
            "Persistent edge text was excluded as likely logos/usernames; check Customize."
        )
    styles = {}
    baselines = {}
    partial_fits = 0
    width_cache = {}
    for i, (start, detected) in enumerate(changes):
        end = changes[i + 1][0] if i + 1 < len(changes) else total
        if detected is None or end <= start:
            continue
        if end - start < 3 or stable[detected.text] < 2:
            unresolved[start] = "Brief or isolated wording needs review"
            continue
        if len(cues) >= 200:
            raise ReferenceError(
                413, "caption_cue_limit", "More than 200 captions were detected. Open Customize."
            )
        rectangle = detected.rectangle
        # Similar text height/anchor share a fit; color is estimated separately for each cue.
        key = (
            round(
                (detected.glyph_height or rectangle.height / (detected.text.count("\n") + 1)) * 50
            ),
            round(rectangle.y * 20),
            detected.ink,
        )
        if key not in styles:
            if unresolved:
                # Preserve partial text before spending time on font fitting.
                font = (
                    fonts.builtin("amiri-regular")
                    if any("\u0600" <= c <= "\u06ff" for c in detected.text)
                    else fonts.Binding()
                )
                styles[key] = captions.Appearance(
                    style=captions.Style(
                        font=font.choice,
                        font_origin="assisted",
                        color=detected.ink,
                        horizontal=rectangle.x + rectangle.width / 2,
                        vertical=min(1.0, rectangle.y + rectangle.height + 0.025),
                        size_percent=min(
                            15.0,
                            max(
                                2.0,
                                150
                                * (
                                    detected.glyph_height
                                    or rectangle.height / (detected.text.count("\n") + 1)
                                ),
                            ),
                        ),
                        outline_percent=0.0,
                    ),
                    font_binding=font,
                )
                baselines[key] = 0.025
                if (
                    partial_fits < 2
                    and sum(c.isalnum() for c in (detected.sample_text or detected.text)) >= 4
                    and deadline - time.monotonic() >= 40
                ):
                    partial_fits += 1
                    try:
                        styles[key], messages = infer(
                            project_id,
                            source,
                            detected,
                            (detected.frame_index or start) / 30,
                            start / 30,
                            end / 30,
                            directory,
                            stop,
                            min(deadline, time.monotonic() + 35),
                            len(styles),
                            motion_fit=False,
                        )
                        sample = detected.sample_rectangle or rectangle
                        baselines[key] = styles[key].style.vertical - sample.y - sample.height
                        notes.extend(messages)
                    except engine.ProbeTimeout:
                        notes.append(
                            "Appearance fitting reached its bound; supported default used."
                        )
                notes.append("Partial wording remains a proposal; font/placement are approximate.")
            elif len(styles) >= MAX_APPEARANCES:
                unresolved[start] = "Appearance budget reached; approximate style needs review"
                nearest = min(styles, key=lambda k: abs(k[0] - key[0]) + abs(k[1] - key[1]))
                styles[key] = styles[nearest]
                baselines[key] = baselines[nearest]
            else:
                fitted, messages = infer(
                    project_id,
                    source,
                    detected,
                    (
                        detected.frame_index / 30
                        if detected.frame_index is not None
                        else (start + end - 1) / 60
                    ),
                    start / 30,
                    end / 30,
                    directory,
                    stop,
                    deadline,
                    len(styles),
                )
                styles[key] = fitted
                sample = detected.sample_rectangle or rectangle
                baselines[key] = fitted.style.vertical - sample.y - sample.height
                notes.extend(messages)
        fitted = styles[key].model_copy(deep=True)
        # Every cue gets its own placement, even when reusing a fitted static face.
        fitted.style.color = detected.ink
        fitted.style.horizontal = rectangle.x + rectangle.width / 2
        fitted.style.vertical = min(1.0, max(0.0, rectangle.y + rectangle.height + baselines[key]))
        longest = max(detected.text.split("\n"), key=len)
        width_key = (fitted.font_binding.sha256, longest)
        if width_key not in width_cache:
            if len(width_cache) >= 24:
                raise ReferenceError(
                    413,
                    "caption_appearance_limit",
                    "Caption width calibration reached its bound. Review the partial captions.",
                )
            with temporary(directory / f"width-{i}") as stage:
                prepared = fonts.prepare(
                    project_id,
                    fitted.font_binding,
                    [captions.Cue(start=0.0, end=1.0, text=longest)],
                    stage,
                    deadline,
                )
                measured, _ = appearance.render_sample(
                    fitted.style.model_copy(
                        update={
                            "size_percent": 10.0,
                            "horizontal": 0.5,
                            "vertical": 0.5,
                            "color": "white",
                            "outline_percent": 0.0,
                            "shadow_percent": 0.0,
                        }
                    ),
                    fitted.font_binding,
                    longest,
                    4096,
                    256,
                    stage,
                    prepared,
                    deadline,
                    with_mask=False,
                )
                width_cache[width_key] = measured[2]
        fitted.style.size_percent = fit_width(
            fitted.style.size_percent, width_cache[width_key], rectangle.width, output_canvas
        )
        # Font proportions stay intact; a different output aspect can reduce glyph height.
        cues.append(
            captions.Cue(start=start / 30, end=end / 30, text=detected.text, appearance=fitted)
        )
    if not cues and not unresolved:
        notes.append("No readable changing reference captions were detected; none were invented.")
    if unresolved:
        notes.append(
            f"Partial caption extraction: {len(unresolved)} sampled frames need review; "
            "unreadable words were not supplied."
        )
    notes.extend(
        [
            "Visible words/timing come from OCR. Check wording in Customize.",
            "Timing: 0.5-second discovery, observed changes refined to 1/30 second. "
            "Short or hidden captions can be missed.",
            "Closest available fonts/effects approximate the reference. "
            "Complex effects and shadow are not reconstructed.",
        ]
    )
    if color.source(project_id, stop, overall_deadline) != source:
        raise ReferenceError(409, "source_changed", "Reference changed during caption reading.")
    return (
        cues,
        list(dict.fromkeys(notes)),
        {
            "unreadable_samples": len(unresolved),
            "method": METHOD,
            "reads": reads[0],
            "engine_version": available["engine_version"]
            + ("; " + handwriting_engine if handwriting_engine else ""),
            "assets_commit": available["assets_commit"],
            "native_frame_width": canvas[0],
            "native_frame_height": canvas[1],
            "handwriting_requests": handwriting_requests,
            "handwriting_completed_requests": handwriting_completed_requests,
            "cached_ocr_reads": cached_ocr_reads,
            "cached_handwriting_requests": cached_handwriting_requests,
            "elapsed_seconds": time.monotonic() - started,
            "source": source["identity"].model_dump(mode="json"),
            "reference_operation_id": source["reference_operation_id"],
        },
    )


def infer(
    project_id,
    source,
    detected,
    timestamp,
    start,
    end,
    directory,
    stop,
    deadline,
    index,
    *,
    motion_fit=True,
):
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
    font = (
        fonts.builtin("patrickhand-regular") if detected.recognizer == "trocr" else fonts.Binding()
    )
    if any("\u0600" <= c <= "\u06ff" for c in detected.text):
        font = fonts.builtin("amiri-bold")
    base = captions.Style(
        font=font.choice,
        font_origin="assisted",
        outline_percent=0.0,
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
        if detected.recognizer == "trocr" and not any(
            "\u0600" <= c <= "\u06ff" for c in detected.text
        ):
            font = fonts.builtin("patrickhand-regular")
            messages.append(
                "English handwriting uses the available Patrick Hand approximation; "
                "raw mask ranking can favor printed fonts."
            )
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
        calibration = stage / "height-calibration"
        calibration.mkdir()
        sample_text = detected.sample_text or detected.text.split("\n")[0][:80]
        prepared_font = fonts.prepare(
            project_id,
            font,
            [captions.Cue(start=0, end=1, text=sample_text)],
            calibration,
            deadline,
        )
        measured, _ = appearance.render_sample(
            base.model_copy(
                update={
                    "size_percent": 10.0,
                    "horizontal": 0.5,
                    "vertical": 0.5,
                    "color": "white",
                    "outline_percent": 0.0,
                }
            ),
            font,
            sample_text,
            640,
            360,
            calibration,
            prepared_font,
            deadline,
        )
        measured_size = min(
            15.0, max(2.0, 10.0 * (detected.glyph_height or r.height) * 360 / measured[3])
        )
        base.size_percent = measured_size
        base.vertical = min(
            1.0,
            max(
                0.0,
                r.y + r.height + (0.5 - (measured[1] + measured[3]) / 360) * (measured_size / 10),
            ),
        )
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
        if not 0.85 * measured_size <= base.size_percent <= 1.15 * measured_size:
            base.size_percent = measured_size
            messages.append(
                "Caption size uses observed glyph height; closest font width can differ."
            )
        if motion_fit and 0.4 <= end - start <= 4:
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
