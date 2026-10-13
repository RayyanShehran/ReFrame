import shutil
import subprocess
import threading
import time

import pytest

import automatic_captions as automatic
import captions
import color_analysis as color
import font_assets as fonts
from references import ReferenceError
from tests.test_video_render import local as local  # noqa: F401
from tests.test_video_render import prepared


def detection(text):
    return automatic.Detection(
        text, automatic.match.Rectangle(x=0.2, y=0.6, width=0.5, height=0.1), 90
    )


def test_adaptive_tracking_refines_changes_and_gaps_without_inventing_text():
    reads = []

    def read(frame):
        reads.append(frame)
        return [] if 7 <= frame < 10 else [detection("First" if frame < 7 else "Second")]

    result = automatic.track(read, [0, 15, 29], set())
    assert [(f, d.text if d else None) for f, d in result] == [
        (0, "First"),
        (7, None),
        (10, "Second"),
    ]
    assert len(reads) < 20


@pytest.mark.parametrize("raw", [b"", b"nonsense", b"level\ttext\n5\tHello"])
def test_malformed_ocr_is_not_a_no_caption_result(raw):
    with pytest.raises(ReferenceError, match="could not be read safely"):
        automatic.parse(raw, 640, 360)


def test_real_visible_text_timing_and_appearance(local):
    client, directory, _ = local
    fixture = directory / "visible-caption-input.mp4"
    stage = directory / "subtitle"
    stage.mkdir()
    first, second = fonts.builtin("poppins-bold"), fonts.builtin("anton-regular")
    track = captions.Track(
        schema_version=2,
        enabled=True,
        cues=[
            captions.Cue(
                start=0.2,
                end=1.1,
                text="FIRST VISIBLE WORDS",
                appearance=captions.Appearance(
                    style=captions.Style(font=first.choice, size_percent=10.0, color="white"),
                    font_binding=first,
                ),
            ),
            captions.Cue(
                start=1.3,
                end=2.8,
                text="SECOND VISIBLE WORDS",
                appearance=captions.Appearance(
                    style=captions.Style(font=second.choice, size_percent=10.0, color="yellow"),
                    font_binding=second,
                ),
            ),
        ],
    )
    ready = fonts.prepare_track("fixture", track, stage, time.monotonic() + 20)
    subtitle = captions.subtitle_filter(track, stage, 640, 360, ready[:2])
    subprocess.run(
        [
            shutil.which("ffmpeg"),
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x204020:s=640x360:r=30:d=3",
            "-vf",
            subtitle,
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(fixture),
        ],
        check=True,
        capture_output=True,
        timeout=20,
    )
    saved, _, _ = prepared(client, directory, fixture)
    pid = saved["id"]
    output = directory / "automatic-caption-stage"
    output.mkdir()
    stop = threading.Event()
    source = color.source(pid)
    capability = automatic.ocr.inspect_capability(output, time.monotonic() + 10)
    if not capability["available"]:
        pytest.skip("Pinned OCR executable/assets absent; deterministic checks still run")
    cues, notes, evidence = automatic.detect(pid, source, output, stop, time.monotonic() + 120)
    print(
        "REAL_AUTOMATIC_OCR",
        [(c.start, c.end, c.text, c.appearance.style.model_dump()) for c in cues],
        evidence,
    )
    assert len(cues) == 2
    assert [c.text for c in cues] == ["FIRST VISIBLE WORDS", "SECOND VISIBLE WORDS"]
    for cue, expected in zip(cues, [(0.2, 1.1), (1.3, 2.8)]):
        assert abs(cue.start - expected[0]) <= 1 / 30 + 0.001
        assert abs(cue.end - expected[1]) <= 1 / 30 + 0.001
    assert cues[0].appearance.style.color != cues[1].appearance.style.color
    assert all(8 <= cue.appearance.style.size_percent <= 12 for cue in cues)
    assert evidence["reads"] <= automatic.MAX_READS
    assert any("Closest available" in note for note in notes)


def test_adjacent_lines_are_saved_as_two_plain_lines():
    first = detection("First line")
    second = detection("Second line")
    second.rectangle = second.rectangle.model_copy(update={"y": 0.72})
    found = automatic.dominant([first, second], set())
    assert found.text == "First line\nSecond line"
    assert found.rectangle.height == pytest.approx(0.22)


def test_material_low_confidence_text_is_an_actionable_blocker():
    header = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
        "left\ttop\twidth\theight\tconf\ttext\n"
    )
    body = (
        "1\t1\t0\t0\t0\t0\t0\t0\t640\t360\t-1\t\n"
        "5\t1\t1\t1\t1\t1\t100\t250\t300\t30\t20\tUnreadable\n"
    )
    with pytest.raises(ReferenceError, match="unreadable"):
        automatic.parse((header + body).encode(), 640, 360)


def test_language_passes_choose_stronger_overlapping_reading_without_duplicating():
    english = detection("incorrect Latin")
    english.confidence = 40
    arabic = detection("\u0645\u0631\u062d\u0628\u0627")
    arabic.confidence = 85
    assert automatic.merge_readings([english], [arabic]) == [arabic]
    assert automatic.merge_readings([arabic], [english]) == [arabic]


def test_partial_language_fragment_cannot_erase_a_complete_caption_line():
    full = detection("FIRST VISIBLE WORDS")
    fragment = detection("\u0645")
    fragment.confidence = 99
    fragment.rectangle = fragment.rectangle.model_copy(update={"width": 0.1})
    merged = automatic.merge_readings([full], [fragment])
    assert automatic.dominant(merged, set()).text == "FIRST VISIBLE WORDS"


def test_tesseract_plain_quotes_do_not_merge_tsv_rows():
    header = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
        "left\ttop\twidth\theight\tconf\ttext\n"
    )
    body = (
        "1\t1\t0\t0\t0\t0\t0\t0\t640\t360\t-1\t\n"
        '5\t1\t1\t1\t1\t1\t100\t100\t100\t30\t95\t"Hello\n'
        "5\t1\t1\t1\t1\t2\t220\t100\t100\t30\t95\tworld\n"
    )
    result = automatic.parse((header + body).encode(), 640, 360)
    assert len(result) == 1 and result[0].text == '"Hello world'


def test_ambiguous_words_are_collected_without_erasing_reliable_lines():
    header = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
        "left\ttop\twidth\theight\tconf\ttext\n"
    )
    body = (
        "1\t1\t0\t0\t0\t0\t0\t0\t640\t360\t-1\t\n"
        "5\t1\t1\t1\t1\t1\t100\t100\t300\t30\t20\tUnreadable\n"
        "5\t1\t1\t1\t2\t1\t100\t150\t300\t30\t95\tReliable\n"
    )
    unknown = []
    found = automatic.parse((header + body).encode(), 640, 360, unknown)
    assert [d.text for d in found] == ["Reliable"] and len(unknown) == 1


def test_retry_crop_uses_sparse_colored_glyphs_instead_of_larger_solid_foreground():
    pytest.importorskip("PIL")
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (960, 540), (50, 50, 50))
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(fonts.BUNDLED), 40)
    draw.text((220, 220), "Readable caption", font=font, fill=(255, 235, 0))
    draw.rectangle((0, 430, 850, 480), fill=(255, 235, 0))
    crops = automatic.retry_crops(image, [])
    assert len(crops) == 2
    assert all(200 < box[1] < box[3] < 300 for _, box, _ in crops)


def test_ambiguous_letter_digit_or_isolated_fragment_is_not_repaired_as_a_word():
    for text in ["You know 1 want", "50", "all"]:
        assert not automatic.reliable_wording(detection(text))
    assert automatic.reliable_wording(detection("Boys you meet"))


def test_crop_padding_artifact_cannot_become_source_text_or_raise_a_geometry_error():
    d = detection("Readable words")
    d.rectangle = d.rectangle.model_copy(update={"x": 0.0, "y": 0.0})
    assert automatic.map_crop_detection(d, (0, 0, 100, 100), (232, 232), 2, (960, 540)) is None
    d.rectangle = automatic.match.Rectangle(
        x=16 / 232, y=16 / 232, width=100 / 232, height=30 / 232
    )
    mapped = automatic.map_crop_detection(d, (0, 0, 100, 100), (232, 232), 2, (960, 540))
    assert mapped.rectangle.x == 0 and mapped.rectangle.y == 0
    assert mapped.rectangle.width == pytest.approx(50 / 960)


def test_three_lines_follow_geometric_order_including_arabic():
    lines = [detection("First longer line"), detection("مرحبا بالعالم"), detection("Last line")]
    for i, line in enumerate(lines):
        line.rectangle = line.rectangle.model_copy(update={"y": 0.3 + i * 0.12})
    result = automatic.dominant(list(reversed(lines)), set())
    assert result.text == "First longer line\nمرحبا بالعالم\nLast line"
    assert result.glyph_height == pytest.approx(0.1)
    with pytest.raises(ValueError, match="three"):
        captions.Cue(start=0.0, end=1.0, text=result.text + "\nFourth")


def test_temporal_vote_preserves_actual_punctuation_and_not_digit_replacement():
    good = detection("You know I want you to know,")
    noisy = detection("You know 1 want you to know.")
    rows = automatic.consolidate([(0, good), (15, noisy), (30, good)])
    assert all(d.text == good.text for _, d in rows)
    only_digits = automatic.consolidate([(0, noisy), (15, noisy)])
    assert all(d.text == noisy.text for _, d in only_digits)
    assert not automatic.reliable_wording(noisy)


def test_temporal_vote_retains_true_added_line_and_does_not_bridge_absence():
    first = detection("First line")
    added = detection("First line\nSecond line")
    rows = automatic.consolidate([(0, first), (15, None), (30, added), (45, added)])
    assert [d.text if d else None for _, d in rows] == [first.text, None, added.text, added.text]


def test_temporal_reading_keeps_persistent_legitimate_caption_but_excludes_known_creator():
    caption = detection("Persistent legitimate caption")
    watermark = detection("@creator")
    result = automatic.track(lambda f: [caption, watermark], [0, 15, 30], {"@creator"})
    assert len(result) == 1 and result[0][1].text == caption.text


@pytest.mark.parametrize(
    "value",
    [
        b"bad",
        b"{}",
        b'{"engine":"local","results":[]}',
        b'{"engine":"local","results":[{"name":"owned.png","text":"words","score":NaN,"capped":false}]}',
    ],
)
def test_handwriting_output_requires_complete_finite_owned_results(value):
    with pytest.raises(ReferenceError) as error:
        automatic.validate_handwriting(value, ["owned.png"])
    assert error.value.code == "ocr_output_invalid"


def test_handwriting_disagreement_is_retained_as_review_not_verified_words():
    found = detection("Readable hypothesis")
    found.recognizer = "trocr"
    found.needs_review = True
    assert not automatic.reliable_wording(found)
    assert automatic.dominant([found], set()).text == found.text


def test_handwriting_assets_are_explicit():
    import handwriting_ocr as handwriting

    with pytest.raises(ValueError, match="explicitly install"):
        handwriting.setup(False)


def test_handwriting_source_crops_are_bounded():
    import handwriting_ocr as handwriting

    pytest.importorskip("PIL")
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (640, 360), (40, 40, 40))
    drawing = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(fonts.BUNDLED), 32)
    drawing.text((100, 100), "Different caption", font=font, fill=(255, 0, 255))
    crops = automatic.retry_crops(image, [])
    assert crops
    lines = handwriting.line_crops(image, *[crops[0][i] for i in (0, 1)])
    assert lines and len(lines) <= 3
    for rect, variants in lines:
        assert 0 <= rect[0] < 1 and rect[2] > 0
        assert len(variants) == 2
        assert all(v.width <= 2048 and v.height <= 256 for v in variants)


def test_handwriting_budget_uses_unique_rows_and_covers_late_frames():
    assert automatic.select_handwriting(list(range(6)), 48) == list(range(6))
    selected = automatic.select_handwriting(list(range(200)), 48)
    assert len(selected) == len(set(selected)) == 48
    assert selected[0] == 0 and selected[-1] == 199
    assert automatic.select_handwriting([], 48) == []
    assert automatic.select_handwriting([1, 2], 0) == []


def test_temporal_glyph_recovery_uses_observed_model_words_only_for_matching_pixels():
    known = detection("You know I want you to know,")
    known.recognizer = "trocr"
    known.needs_review = True
    wrong = detection("You know 1 want you to know,")
    shape = bytes([255, 0, 255, 0] * 1024)
    prototypes = [(0, known.rectangle, shape, known), (30, known.rectangle, shape, known)]
    recovered = automatic.recover_lines(15, [(wrong.rectangle, shape)], prototypes, [wrong])
    assert recovered[0].text == known.text and recovered[0].needs_review
    different = bytes([0, 255, 0, 255] * 1024)
    assert automatic.recover_lines(15, [(wrong.rectangle, different)], prototypes, [wrong]) == [
        wrong
    ]
    assert automatic.recover_lines(15, [], prototypes, []) == []
    assert automatic.recover_lines(180, [(wrong.rectangle, shape)], prototypes, [wrong]) == [wrong]


def test_multiline_recovery_cannot_add_a_line_without_visible_glyphs():
    lines = [detection("First line"), detection("Second line"), detection("مرحبا بالعالم")]
    for i, line in enumerate(lines):
        line.rectangle = line.rectangle.model_copy(update={"y": 0.3 + i * 0.12})
    shape = bytes([255, 0, 255, 0] * 1024)
    prototypes = [(0, d.rectangle, shape, d) for d in lines]
    recovered = automatic.recover_lines(15, [(d.rectangle, shape) for d in lines], prototypes, [])
    assert automatic.dominant(recovered, set()).text == "First line\nSecond line\nمرحبا بالعالم"
    missing = automatic.recover_lines(15, [(d.rectangle, shape) for d in lines[:2]], prototypes, [])
    assert automatic.dominant(missing, set()).text == "First line\nSecond line"


def test_real_three_line_fixture_preserves_regions_and_flags_arabic_uncertainty(local):
    client, directory, _ = local
    stage = directory / "three-line-caption"
    stage.mkdir()
    if not automatic.ocr.inspect_capability(stage, time.monotonic() + 10)["available"]:
        pytest.skip("Pinned English/Arabic OCR assets absent")
    font = fonts.builtin("amiri-bold")
    text = "A different story\nمرحبا\nAnother line"
    track = captions.Track(
        schema_version=2,
        enabled=True,
        style=captions.Style(
            font=font.choice, size_percent=15.0, placement="center", outline_percent=0.0
        ),
        font_binding=font,
        cues=[captions.Cue(start=0.0, end=1.0, text=text)],
    )
    ready = fonts.prepare_track("fixture", track, stage, time.monotonic() + 20)
    target = stage / "frame.png"
    subtitle = captions.subtitle_filter(track, stage, 1280, 720, ready[:2])
    subprocess.run(
        [
            shutil.which("ffmpeg"),
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=black:s=1280x720:r=30:d=1",
            "-vf",
            subtitle,
            "-frames:v",
            "1",
            str(target),
        ],
        check=True,
        capture_output=True,
        timeout=20,
    )
    readings = []
    for language in ("eng", "ara"):
        result = subprocess.run(
            [
                str(automatic.ocr.executable()),
                str(target),
                "stdout",
                "--tessdata-dir",
                str(automatic.ocr.DATA),
                "-l",
                language,
                "--psm",
                "6",
                "-c",
                "tessedit_create_tsv=1",
            ],
            check=True,
            capture_output=True,
            timeout=15,
        )
        readings = automatic.merge_readings(readings, automatic.parse(result.stdout, 1280, 720, []))
    found = automatic.dominant(readings, set())
    assert found is not None
    assert found.text.split("\n")[::2] == ["A different story", "Another line"]
    assert len(found.text.split("\n")) == 3
    # The installed reader misreads this Naskh word; it must remain unverified.
    assert not automatic.reliable_wording(found)


def test_caption_width_fits_final_canvas_without_stretching_or_silent_clipping():
    assert automatic.fit_width(8.0, 400, 0.5, (478, 850)) == pytest.approx(1.7995294)
    assert automatic.fit_width(8.0, 100, 0.8, (1280, 720)) == 8.0
    with pytest.raises(ReferenceError, match="cannot fit"):
        automatic.fit_width(8.0, 10000, 0.5, (478, 850))


def test_caption_native_geometry_keeps_source_detail_instead_of_export_downscaling():
    video = {"width": 1920, "height": 1080, "sample_aspect_ratio": "1:1"}
    assert automatic.native_geometry(video) == (1920, 1080)
    assert automatic.native_geometry(video | {"width": 3840, "height": 2160}) == (1920, 1080)
    assert automatic.native_geometry(
        video | {"sample_aspect_ratio": "2:1", "tags": {"rotate": "90"}}
    ) == (540, 1920)


def test_real_wide_caption_measurement_is_not_rejected_as_a_font_matching_word(local):
    client, directory, _ = local
    stage = directory / "wide-caption-measurement"
    stage.mkdir()
    text = "A long visible caption is measured across the complete output canvas"
    binding = fonts.Binding()
    prepared = fonts.prepare(
        "fixture",
        binding,
        [captions.Cue(start=0.0, end=1.0, text=text)],
        stage,
        time.monotonic() + 20,
    )
    box, mask = automatic.appearance.render_sample(
        captions.Style(size_percent=10.0, outline_percent=0.0, placement="center"),
        binding,
        text,
        4096,
        256,
        stage,
        prepared,
        time.monotonic() + 20,
        with_mask=False,
    )
    assert box[2] / box[3] > 15 and box[2] < 4096
    size = automatic.fit_width(8.0, box[2], 0.6, (478, 850))
    assert box[2] * 850 / 256 * size / 10 <= 478 * 0.6 + 0.001
    assert mask is None


def test_color_contrast_can_measure_readable_glyphs_with_equal_luminance_background():
    pixels = bytes(
        v
        for y in range(16)
        for x in range(20)
        for v in (
            (200, 160, 0) if 4 <= x < 13 and 4 <= y < 12 and (x + y) % 2 == 0 else (154, 154, 154)
        )
    )
    points, box, ink = automatic.appearance.fill(pixels, 20, 16, "light")
    assert len(points) == 36 and box == (4, 4, 9, 8) and ink == (200, 160, 0)


def test_refined_model_readings_are_recorded_for_stability_instead_of_dropped():
    from collections import Counter

    correct = detection("You know I want you to know.")
    correct.recognizer = "trocr"
    correct.needs_review = True
    raw = detection("You know 1 want you to know,")
    shape = bytes([255, 0, 255, 0] * 1024)
    prototypes = [(0, correct.rectangle, shape, correct), (30, correct.rectangle, shape, correct)]
    cache = {}
    signatures = {f: [(correct.rectangle, shape)] for f in range(31)}

    def read(frame):
        return automatic.read_tracked(frame, lambda _: [raw], cache, signatures, prototypes)

    changes = automatic.track(read, [0, 15, 30], set())
    stable = Counter(automatic.dominant(rows, set()).text for rows in cache.values())
    assert changes[0][1].text == correct.text
    assert stable[correct.text] == len(cache) and stable[raw.text] == 0


def test_unknown_outline_does_not_add_a_dark_border_to_assisted_handwriting(tmp_path, monkeypatch):
    def unavailable(*args):
        raise ReferenceError(422, "no_useful_font_match", "No reliable outline evidence.")

    monkeypatch.setattr(automatic.match, "read", unavailable)
    observed = detection("Readable words")
    observed.recognizer = "trocr"
    appearance, notices = automatic.infer(
        "fixture",
        {},
        observed,
        1.0,
        0.5,
        2.0,
        tmp_path,
        threading.Event(),
        time.monotonic() + 10,
        0,
    )
    assert appearance.style.outline_percent == 0.0
    assert appearance.style.font == "patrickhand-regular"
    assert notices and not (tmp_path / "fit-0").exists()


def test_unresolved_checkpoint_cannot_download_or_be_selected(tmp_path, monkeypatch):
    import handwriting_ocr as handwriting

    preserved = tmp_path / "model.bin"
    preserved.write_bytes(b"existing user asset")
    monkeypatch.setattr(
        handwriting, "MANIFEST", {"license_status": "unresolved", "files": {"model.bin": {}}}
    )
    monkeypatch.setattr(handwriting, "MODEL", tmp_path)
    monkeypatch.setattr(handwriting, "executable", lambda: preserved)
    monkeypatch.setattr(
        handwriting.subprocess, "run", lambda *args, **kwargs: pytest.fail("No dependency setup")
    )
    monkeypatch.setattr(
        handwriting.urllib.request,
        "urlopen",
        lambda *args, **kwargs: pytest.fail("No model download"),
    )
    assert not handwriting.available()
    with pytest.raises(ValueError, match="permissions remain unresolved"):
        handwriting.setup(True)
    assert preserved.read_bytes() == b"existing user asset"


def test_colored_caption_is_separated_from_a_tall_sign_and_remote_specks():
    pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (960, 540), (35, 60, 50))
    draw = ImageDraw.Draw(image)
    draw.rectangle((750, 10, 959, 520), fill=(230, 160, 50))
    font = ImageFont.truetype(str(fonts.BUNDLED), 28)
    draw.text((270, 250), "A separate message", font=font, fill=(240, 220, 20))
    for x in range(20, 90, 10):
        draw.rectangle((x, 255, x + 2, 257), fill=(240, 220, 20))
    found = automatic.retry_crops(image, [])
    assert found
    left, top, right, bottom = found[0][1]
    assert left >= 250 and right < 750 and 240 < top < bottom < 310
    assert len(automatic.handwriting.line_crops(image, found[0][0], found[0][1])) == 1


def test_component_limit_fails_explicitly_instead_of_returning_a_partial_mask():
    np = pytest.importorskip("numpy")
    Image = pytest.importorskip("PIL.Image")
    pixels = np.zeros((400, 400), dtype=np.uint8)
    pixels[::3, ::3] = 255
    with pytest.raises(ReferenceError, match="component budget"):
        automatic.glyph_mask(Image.fromarray(pixels))


def test_handwriting_timeout_retains_only_completed_owned_readings(tmp_path):
    import json
    import sys

    names = ["a" * 64 + ".png", "b" * 64 + ".png"]
    helper = tmp_path / "helper.py"
    payload_json = json.dumps(
        {
            "engine": "local-test",
            "results": [
                {"name": names[0], "text": "Observed words", "score": 0.9, "capped": False}
            ],
        }
    )
    helper.write_text(
        "import time\nfrom pathlib import Path\n"
        + f"Path({str(tmp_path / 'progress.json')!r})"
        + f".write_text({payload_json!r}, encoding='utf-8')\n"
        + "while True: time.sleep(.01)\n",
        encoding="utf-8",
    )
    payload, timed_out = automatic.run_handwriting(
        [sys.executable, str(helper)],
        tmp_path,
        names,
        time.monotonic() + 5,
        1.0,
    )
    assert timed_out and [row["name"] for row in payload["results"]] == names[:1]
    assert payload["results"][0]["text"] == "Observed words"
    with pytest.raises(ReferenceError, match="could not be read safely"):
        automatic.validate_handwriting(json.dumps(payload).encode(), names)


@pytest.mark.parametrize(
    "results",
    [
        [{"name": "unowned", "text": "Unknown", "score": 0.9, "capped": False}],
        [{"name": "owned", "text": "Unknown", "score": float("nan"), "capped": False}],
        [{"name": "owned", "text": "Unknown", "score": 0.9, "capped": False}] * 2,
    ],
)
def test_partial_handwriting_progress_rejects_unowned_duplicate_or_invalid_rows(results):
    import json

    with pytest.raises(ReferenceError, match="could not be read safely"):
        automatic.validate_handwriting(
            json.dumps({"engine": "local-test", "results": results}).encode(),
            ["owned"],
            allow_partial=True,
        )


def test_interruption_persists_completed_reads_before_propagating(tmp_path):
    import json
    import sys

    names = ["a" * 64 + ".png"]
    raw = json.dumps(
        {
            "engine": "local-test",
            "results": [
                {"name": names[0], "text": "Observed words", "score": 0.9, "capped": False}
            ],
        }
    )
    helper = tmp_path / "helper.py"
    path = tmp_path / "progress.json"
    helper.write_text(
        "import time\nfrom pathlib import Path\n"
        + f"Path({str(path)!r}).write_text({raw!r})\n"
        + "while True: time.sleep(.01)\n"
    )
    stop = threading.Event()
    previous = getattr(automatic.engine._control, "stop", None)
    automatic.engine._control.stop = stop
    collected = []

    def cancel():
        while not path.exists() and not stop.wait(0.01):
            pass
        stop.set()

    thread = threading.Thread(target=cancel)
    thread.start()
    try:
        with pytest.raises(automatic.engine.ProbeInterrupted):
            automatic.run_handwriting(
                [sys.executable, str(helper)],
                tmp_path,
                names,
                time.monotonic() + 5,
                persist=collected.append,
            )
        assert len(collected) == 1 and collected[0]["results"][0]["text"] == "Observed words"
    finally:
        stop.set()
        thread.join(timeout=5)
        automatic.engine._control.stop = previous


def test_same_words_do_not_bridge_a_visible_gap_or_fade():
    def read(frame):
        return [] if 6 <= frame < 10 or frame >= 25 else [detection("Same words")]

    result = automatic.track(read, [0, 15, 29], set())
    assert [(f, d.text if d else None) for f, d in result] == [
        (0, "Same words"),
        (6, None),
        (10, "Same words"),
        (25, None),
    ]


def test_visible_shape_follows_fade_but_not_black_or_different_glyphs():
    from PIL import Image, ImageDraw

    rect = automatic.match.Rectangle(x=0.1, y=0.2, width=0.7, height=0.3)
    image = Image.new("RGB", (200, 100))
    draw = ImageDraw.Draw(image)
    for x in range(24, 150, 16):
        draw.line((x, 24, x, 45), fill=(240, 220, 20), width=3)
    original = automatic.visible_shape(image, rect, "#f0dc14")
    faded = image.point(lambda v: round(v * 0.2))
    assert (
        automatic.glyph_similarity(original, automatic.visible_shape(faded, rect, "#f0dc14")) > 0.99
    )
    assert (
        automatic.glyph_similarity(
            original, automatic.visible_shape(Image.new("RGB", image.size), rect, "#f0dc14")
        )
        == 0
    )
    other = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    assert (
        automatic.glyph_similarity(original, automatic.visible_shape(other, rect, "#f0dc14")) < 0.9
    )


def test_handwriting_selection_pairs_distinct_frames_within_budget():
    d = detection("You know 1 want")
    rect = (d.rectangle.x, d.rectangle.y, d.rectangle.width, d.rectangle.height)
    shape = bytes([255, 0] * 2048)
    rows = [(f, rect, [str(f)]) for f in [0, 1, 15, 30]]
    selected = automatic.select_handwriting(
        rows, 2, signatures={f: [(d.rectangle, shape)] for f, _, _ in rows}, readings={0: [d]}
    )
    assert len(selected) == 2 and abs(selected[0][0] - selected[1][0]) >= 3


def test_punctuation_requires_agreeing_pixel_readings_and_does_not_guess_words():
    assert (
        automatic.observed_punctuation("You know I want", "You know 1 want,") == "You know I want,"
    )
    assert (
        automatic.observed_punctuation("You know I want", "You know 1 went,") == "You know I want"
    )
    assert automatic.observed_punctuation("There are 10", "There are IO!") == "There are 10"
