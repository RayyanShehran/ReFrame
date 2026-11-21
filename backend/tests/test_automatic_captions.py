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


def test_limited_budget_refines_observed_changes_before_probing_unchanged_intervals():
    discovery = list(range(0, 151, 15))
    extra = []

    def read(frame):
        if frame not in discovery:
            if len(extra) >= 7:
                raise automatic.engine.ProbeTimeout("Caption reading budget reached")
            extra.append(frame)
        return [detection("First" if frame < 97 else "Second")]

    result = automatic.track(read, discovery, set())
    # Refined to the actual change frame, not snapped to the 15-frame (0.5 s) discovery grid.
    assert [(f, d.text) for f, d in result] == [(0, "First"), (97, "Second")]


@pytest.mark.parametrize("raw", [b"", b"nonsense", b"level\ttext\n5\tHello"])
def test_malformed_ocr_is_not_a_no_caption_result(raw):
    with pytest.raises(ReferenceError, match="could not be read safely"):
        automatic.parse(raw, 640, 360)


@pytest.mark.parametrize("preserve_analysis", [False, True])
def test_real_visible_text_timing_and_appearance(local, monkeypatch, preserve_analysis):
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
                    style=captions.Style(
                        font=first.choice, size_percent=10.0, color="#306cff", vertical=0.2
                    ),
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
    if preserve_analysis:
        monkeypatch.setattr(automatic.match, "read", lambda *args: {"revision": 1})

        def cannot_overwrite(*args, **kwargs):
            pytest.fail("Automatic caption extraction must not overwrite saved appearance analysis")

        monkeypatch.setattr(automatic, "infer", cannot_overwrite)
        monkeypatch.setattr(automatic.match, "generate", cannot_overwrite)
        monkeypatch.setattr(automatic.match, "review", cannot_overwrite)
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
    if preserve_analysis:
        assert any("Saved appearance analyses are preserved" in note for note in notes)
    else:
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
        assert len(variants) == 3
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
    pytest.importorskip("numpy")
    pytest.importorskip("PIL")
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


def test_tracking_deadline_preserves_observed_words_and_does_not_invent_disappearance():
    observed = detection("Recognized I stays in the checkpoint")

    def read(frame):
        if frame == 22:
            raise automatic.engine.ProbeTimeout("No observation completed")
        return [] if 7 <= frame < 10 else [observed]

    changes = automatic.track(read, [0, 15, 30], set())
    assert [(f, d.text if d else None) for f, d in changes] == [
        (0, observed.text),
        (7, None),
        (10, observed.text),
    ]


def test_reliable_completed_model_block_beats_more_frequent_noisy_refinement():
    reliable = detection("You know I want\nyou can talk\nAs you get older")
    reliable.recognizer = "trocr"
    noisy = detection("You know I want\nyou can talk\n\\S You get older")
    noisy.minimum_confidence = 60
    observations = [(f, reliable if f in {0, 15, 30} else noisy) for f in range(31)]
    assert all(d.text == reliable.text for _, d in automatic.consolidate(observations))


def test_block_does_not_inherit_one_good_lines_review_status():
    first = detection("A trusted model line")
    first.recognizer = "trocr"
    other = detection("A line needing review")
    other.rectangle = other.rectangle.model_copy(update={"y": 0.72})
    other.needs_review = True
    block = automatic.dominant([first, other], set())
    assert block.needs_review and block.recognizer == "tesseract"


def test_cached_pairs_do_not_consume_the_unfinished_inference_budget():
    d = detection("You know 1")
    r = (d.rectangle.x, d.rectangle.y, d.rectangle.width, d.rectangle.height)
    rows = [(f, r, [f"{f}-a", f"{f}-b"]) for f in [0, 15, 150, 165]]
    cached = {name for _, _, names in rows[:2] for name in names}
    shape = bytes([255, 0] * 2048)
    selected = automatic.select_handwriting(
        rows,
        2,
        signatures={f: [(d.rectangle, shape)] for f, _, _ in rows},
        readings={f: [d] for f, _, _ in rows},
        cached_names=cached,
    )
    assert {f for f, _, _ in selected} == {0, 15, 150, 165}
    assert len({name for _, _, names in selected for name in names} - cached) == 4


def test_neutral_title_crop_excludes_dark_railing_like_scenery():
    pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (960, 540), (80, 130, 140))
    draw = ImageDraw.Draw(image)
    draw.text(
        (330, 240), "Visible title", font=ImageFont.truetype(str(fonts.BUNDLED), 42), fill="white"
    )
    for x in range(0, 960, 80):
        draw.line((x, 410, x + 40, 539), fill="black", width=8)
    crops = automatic.retry_crops(image, [])
    assert crops
    left, top, right, bottom = crops[0][1]
    assert 310 < left < right < 640 and 230 < top < bottom < 310


def test_visibility_reuse_keeps_the_actual_word_sample_for_appearance_calibration():
    prototype = detection("A real sampled word")
    prototype.frame_index = 15
    prototype.sample_text = "sampled"
    prototype.sample_rectangle = prototype.rectangle
    shape = bytes([255, 0] * 2048)
    result = automatic.recover_lines(
        30, [(prototype.rectangle, shape)], [(15, prototype.rectangle, shape, prototype)], []
    )
    assert result[0].sample_text == "sampled"
    assert result[0].sample_rectangle == prototype.sample_rectangle and result[0].frame_index == 15


def test_crop_mapping_retains_a_valid_word_sample_in_source_coordinates():
    d = detection("A valid word sample")
    d.rectangle = automatic.match.Rectangle(x=0.2, y=0.2, width=0.4, height=0.2)
    d.sample_rectangle = automatic.match.Rectangle(x=0.3, y=0.3, width=0.1, height=0.05)
    d.sample_text = "sample"
    mapped = automatic.map_crop_detection(d, (0, 0, 100, 100), (232, 232), 2, (960, 540))
    assert mapped.sample_text == "sample"
    assert mapped.sample_rectangle.x == pytest.approx((0.3 * 232 - 16) / 2 / 960)
    assert mapped.sample_rectangle.width == pytest.approx(0.1 * 232 / 2 / 960)


def test_same_ink_reply_can_join_nearby_lines_with_different_horizontal_anchors():
    first = detection("The upper reply")
    second = detection("An additional response")
    first.ink = second.ink = "#eed120"
    first.rectangle = first.rectangle.model_copy(
        update={"x": 0.6, "width": 0.3, "height": 0.05, "y": 0.3}
    )
    second.rectangle = second.rectangle.model_copy(
        update={"x": 0.15, "width": 0.3, "height": 0.05, "y": 0.46}
    )
    assert automatic.dominant([first, second], set()).text == first.text + "\n" + second.text


def test_partial_faded_reappearance_is_not_certified_as_a_complete_new_sentence():
    before = detection("But if you do remember okay")
    short = detection("But if")
    assert automatic.partial_reappearance(before, short)
    assert not automatic.partial_reappearance(before, detection("A different reply"))
    short.rectangle = short.rectangle.model_copy(update={"y": 0.2})
    assert not automatic.partial_reappearance(before, short)


def test_tracking_records_refined_visibility_brackets_and_observed_absence():
    boundaries, absence = {}, []
    result = automatic.track(
        lambda f: [detection("Observed words")] if 7 <= f < 22 else [],
        [0, 15, 29, 44],
        set(),
        boundaries=boundaries,
        absence=absence,
    )
    assert [(f, d.text if d else None) for f, d in result] == [
        (0, None),
        (7, "Observed words"),
        (22, None),
    ]
    assert boundaries[7] == (6 / 30, 7 / 30) and boundaries[22] == (21 / 30, 22 / 30)
    assert any(left <= 29 / 30 and right >= 44 / 30 for left, right in absence)


def test_budget_failure_cannot_become_observed_absence():
    def read(f):
        if f not in {0, 15}:
            raise automatic.engine.ProbeTimeout("Fixture budget exhausted")
        return [detection("Observed words")] if f == 15 else []

    boundaries, absence = {}, []
    result = automatic.track(read, [0, 15], set(), boundaries=boundaries, absence=absence)
    assert [(f, d.text if d else None) for f, d in result] == [(0, None), (15, "Observed words")]
    assert boundaries[15] == (0, 0.5) and absence == []


def test_ocr_silence_without_supported_visibility_cannot_remove_old_cues():
    absence = []
    automatic.track(lambda f: [], [0, 15, 29], set(), absence=absence, verified_absence=set())
    assert absence == []


def test_isolated_reply_gets_an_independent_frame_without_resampling_stable_lines():
    rows = [
        (30, (0.6, 0.2, 0.3, 0.05), []),
        (45, (0.6, 0.2, 0.3, 0.05), []),
        (30, (0.1, 0.5, 0.3, 0.05), []),
    ]
    assert automatic.handwriting_companions(rows, 0, 60) == [33]
    crowded = [(f, (0.1, 0.1 + f * 0.01, 0.3, 0.05), []) for f in range(0, 100, 5)]
    selected = automatic.handwriting_companions(crowded, 0, 103)
    assert len(selected) == 8 and selected[0] == 3 and selected[-1] == 98


def test_discovery_is_bounded_and_spreads_observations_across_the_entire_reference():
    frames = list(range(0, 1200, 15))
    order = automatic.discovery_order(frames)
    assert sorted(order) == frames and len(set(order)) == len(frames)
    assert order[:5] == [0, 1185, 600, 300, 900]


def test_context_crop_keeps_pixels_outside_incomplete_color_mask():
    pytest.importorskip("PIL")
    pytest.importorskip("numpy")
    from PIL import Image, ImageDraw

    import handwriting_ocr as handwriting

    image = Image.new("RGB", (320, 180), (30, 30, 30))
    drawing = ImageDraw.Draw(image)
    drawing.rectangle((70, 70, 190, 95), fill=(240, 200, 20))
    mask = Image.new("L", (320, 180), 255)
    ImageDraw.Draw(mask).rectangle((85, 70, 175, 95), fill=0)
    rows = handwriting.line_crops(image, mask, (0, 0, 320, 180))
    assert len(rows) == 1
    binary, gray, context = rows[0][1]
    assert context.width > gray.width
    assert context.height <= 256 and context.width <= 2048
    # Original source colors outside the mask contribute to the context, not invented pixels.
    assert context.convert("L").histogram()[0] > gray.convert("L").histogram()[0]


def test_visible_shape_preserves_deep_fade_but_rejects_black():
    pytest.importorskip("PIL")
    pytest.importorskip("numpy")
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (320, 180))
    ImageDraw.Draw(image).text((65, 95), "Fade", fill=(240, 200, 20), font_size=24)
    rect = automatic.match.Rectangle(x=0.18, y=0.5, width=0.4, height=0.2)
    original = automatic.visible_shape(image, rect, "#f0c814")
    faded = image.point(lambda v: round(v * 0.04))
    assert (
        automatic.glyph_similarity(original, automatic.visible_shape(faded, rect, "#f0c814")) > 0.95
    )
    assert (
        automatic.glyph_similarity(
            original, automatic.visible_shape(Image.new("RGB", image.size), rect, "#f0c814")
        )
        == 0
    )


def test_same_words_keep_distinct_position_and_color_phases():
    from dataclasses import replace

    first = detection("Repeated caption")
    assert automatic.same(first, replace(first))
    assert not automatic.same(first, replace(first, ink="#2040f0"))
    moved = replace(
        first, rectangle=automatic.match.Rectangle(x=0.55, y=0.6, width=0.4, height=0.1)
    )
    assert not automatic.same(first, moved)


def test_multiline_block_accepts_distinct_anchors_and_unequal_glyph_heights():
    from dataclasses import replace

    first = detection("Upper readable line")
    first.rectangle = automatic.match.Rectangle(x=0.55, y=0.2, width=0.4, height=0.06)
    second = replace(
        first,
        text="Lower reply",
        rectangle=automatic.match.Rectangle(x=0.1, y=0.36, width=0.3, height=0.035),
    )
    block = automatic.dominant([first, second], set())
    assert block.text == "Upper readable line\nLower reply"
    second.needs_review = True
    assert automatic.dominant([first, second], set()).text == first.text


def test_bold_blue_caption_has_the_same_density_allowance_as_white():
    pytest.importorskip("PIL")
    pytest.importorskip("numpy")
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (640, 360), (30, 45, 30))
    font = ImageFont.truetype(
        str(fonts.font_catalog.ROOT / fonts.font_catalog.FACES["poppins-bold"][0]), 36
    )
    ImageDraw.Draw(image).text((110, 60), "COLORFUL WORDS", font=font, fill=(48, 108, 255))
    assert automatic.retry_crops(image, [])


def test_three_variant_selection_retains_the_96_input_bound():
    rows = [
        (i * 100, (0.1, 0.2, 0.3, 0.04), [f"{i}-{variant}" for variant in range(3)])
        for i in range(50)
    ]
    rectangle = automatic.match.Rectangle(x=0.1, y=0.2, width=0.3, height=0.04)
    signatures = {row[0]: [(rectangle, bytes([255]) * 30)] for row in rows}
    names = {name for row in rows for name in row[2]}
    selected = automatic.select_handwriting(rows, 8, signatures=signatures, cached_names=names)
    assert len({name for row in selected for name in row[2]}) == 96


def test_ink_observation_keeps_blue_and_faded_chromatic_glyphs():
    pytest.importorskip("PIL")
    pytest.importorskip("numpy")
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (320, 180), (0, 0, 0))
    ImageDraw.Draw(image).text((65, 95), "Visible", fill=(48, 108, 255), font_size=24)
    rect = automatic.match.Rectangle(x=0.18, y=0.5, width=0.5, height=0.2)
    ink = automatic.glyph_color(image, rect)
    faded = automatic.glyph_color(image.point(lambda v: round(v * 0.04)), rect)
    assert ink != "#FFFFFF" and faded != "#FFFFFF"
    assert automatic.math.dist(automatic.normalized_ink(ink), automatic.normalized_ink(faded)) < 0.1
    assert automatic.glyph_color(Image.new("RGB", image.size), rect) is None
    first = detection("Same words")
    first.ink = ink
    from dataclasses import replace

    assert automatic.same(first, replace(first, ink=faded))


def test_source_case_is_kept_only_for_observed_matching_words():
    assert (
        automatic.observed_punctuation("clear blue words", "CLEAR BLUE WORDS") == "CLEAR BLUE WORDS"
    )
    assert automatic.observed_punctuation("Unseen words", "OTHER WORDS") == "Unseen words"


def test_real_mixed_caption_keeps_arabic_after_english_discovery(local, monkeypatch):
    client, directory, _ = local
    monkeypatch.setattr(automatic.handwriting, "available", lambda: False)
    stage = directory / "mixed-source"
    stage.mkdir()
    track = captions.Track(
        schema_version=2,
        enabled=True,
        cues=[
            captions.Cue(
                start=0.0,
                end=0.6,
                text="Start here",
                appearance=captions.Appearance(
                    style=captions.Style(size_percent=10.0, color="white"),
                    font_binding=fonts.Binding(),
                ),
            ),
            captions.Cue(
                start=0.6,
                end=2.8,
                text="مرحبا بالعالم\nHello world",
                appearance=captions.Appearance(
                    style=captions.Style(size_percent=10.0, color="white"),
                    font_binding=fonts.Binding(),
                ),
            ),
        ],
    )
    ready = fonts.prepare_track("fixture", track, stage, time.monotonic() + 20)
    subtitle = captions.subtitle_filter(track, stage, 640, 360, ready[:2])
    source = directory / "mixed.mp4"
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
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=20,
    )
    saved, _, _ = prepared(client, directory, source)
    output = directory / "mixed-extraction"
    output.mkdir()
    if not automatic.ocr.inspect_capability(output, time.monotonic() + 10)["available"]:
        pytest.skip("Pinned OCR assets required")
    found, _, _ = automatic.detect(
        saved["id"], color.source(saved["id"]), output, threading.Event(), time.monotonic() + 180
    )
    assert len(found) == 2
    assert automatic.word_key(found[0].text) == automatic.word_key("Start here")
    assert found[1].text.splitlines()[0] == "مرحبا بالعالم"
    assert automatic.word_key(found[1].text.splitlines()[1]) == automatic.word_key("Hello world")
    assert abs(found[1].start - 0.6) <= 1 / 30 + 0.001 and abs(found[1].end - 2.8) <= 1 / 30 + 0.001


def test_recovery_deduplicates_regions_and_never_prefers_repeated_weak_words():
    from dataclasses import replace

    good = detection("Clear words")
    weak = replace(good, text="Cl ear words", minimum_confidence=40)
    shape = bytes([255, 0]) * 50
    recovered = automatic.recover_lines(
        15,
        [(good.rectangle, shape)] * 8,
        [(0, good.rectangle, shape, good)] + [(1, weak.rectangle, shape, weak)] * 20,
        [],
    )
    assert [d.text for d in recovered] == ["Clear words"]
    assert automatic.reliable_wording(recovered[0])


def completion(cache, layouts=None, signatures=None, unresolved=None, expected=None):
    return automatic.completion_evidence(
        cache,
        signatures or {},
        layouts or {},
        {},
        unresolved or {},
        set(cache) if expected is None else expected,
        0,
        31,
        set(),
    )


def test_completion_resolves_repeated_supported_readings_without_lowering_confidence():
    from dataclasses import replace

    good = detection("Clear words")
    weak = replace(good, minimum_confidence=60)
    cache = {0: [good], 3: [weak], 15: [weak]}
    result = completion(cache, unresolved={3: "Text could not be read confidently", 15: "retry"})
    assert result["status"] == "complete"
    assert not automatic.reliable_wording(weak)
    assert not result["unresolved_regions"]


def test_completion_tracks_unreadable_region_and_keeps_unobserved_discovery_incomplete():
    from dataclasses import replace

    weak = replace(detection("Unclear words"), minimum_confidence=40)
    frames = [0, 3, 9, 15]
    result = completion({f: [weak] for f in frames}, unresolved={f: "unreadable" for f in frames})
    assert result["status"] == "partial"
    assert len(result["unresolved_regions"]) == 1
    assert result["unresolved_regions"][0]["observations"] == 4
    assert result["reason"].startswith("Some possible caption regions")
    assert result["unresolved_regions"][0]["reason"] == "unreadable_caption"
    missing = completion({0: [], 30: []}, expected={0, 15, 30})
    assert missing["status"] == "partial"
    assert missing["unresolved_regions"][0]["reason"] == "unobserved_interval"
    assert "not observed" in missing["reason"]


def test_completion_requires_temporal_evidence_before_resolving_non_text_shapes():
    r = detection("Unused").rectangle
    varied = {0: [(r, b"\xff\x00")], 15: [(r, b"\x00\xff")]}
    result = completion({0: [], 15: []}, {f: [(r, "uncertain")] for f in varied}, varied)
    assert result["status"] == "no_captions"
    persistent = {0: [(r, b"\xff\x00")], 15: [(r, b"\xff\x00")]}
    result = completion({0: [], 15: []}, {f: [(r, "uncertain")] for f in persistent}, persistent)
    assert result["status"] == "partial"
    assert len(result["unresolved_regions"]) == 1


def test_text_layout_distinguishes_letter_columns_from_horizontal_reflections():
    Image = pytest.importorskip("PIL.Image")
    Draw = pytest.importorskip("PIL.ImageDraw")
    pytest.importorskip("numpy")
    mask = Image.new("L", (132, 72), 255)
    draw = Draw.Draw(mask)
    for x in (20, 40, 60, 80):
        draw.rectangle((x, 20, x + 8, 50), fill=0)
    assert automatic.line_layout(mask) == "glyphs"
    draw.rectangle((16, 16, 116, 56), fill=255)
    for y in (24, 33, 42):
        draw.line((20, y, 105, y), fill=0)
    assert automatic.line_layout(mask) == "scenery"


def test_completion_keeps_conflicts_and_detector_errors_partial():
    from dataclasses import replace

    a = detection("Credible reading")
    b = replace(a, text="Different reading")
    result = completion({0: [a, b]})
    assert result["status"] == "partial"
    assert result["unresolved_regions"][0]["reason"] == "conflicting_readings"
    result = completion({0: []}, unresolved={0: "Detector output requires review"})
    assert result["status"] == "partial"
    assert result["unresolved_regions"][0]["reason"] == "detector_failure"


def test_handwriting_fresh_budget_uses_context_pairs_across_the_timeline():
    r = (0.1, 0.2, 0.3, 0.04)
    rectangle = automatic.match.Rectangle(x=r[0], y=r[1], width=r[2], height=r[3])
    rows = [
        (f, r, [f"{f}-binary", f"{f}-gray", f"{f}-context"])
        for f in (0, 3, 150, 153, 300, 303, 450, 453)
    ]
    selected = automatic.select_handwriting(
        rows,
        2,
        signatures={f: [(rectangle, bytes([255, 0]) * 15)] for f, _, _ in rows},
        cached_names=set(),
    )
    frames = {f for f, _, _ in selected}
    assert {0, 3, 450, 453} <= frames
    assert len({names[2] for _, _, names in selected}) == 4


def test_refinement_reaches_late_intervals_before_spending_budget_on_early_changes():
    visited = []

    def read(frame):
        visited.append(frame)
        if len(visited) > 10:
            raise automatic.engine.ProbeTimeout("bounded")
        return [detection(str(frame // 15) + " observed words")]

    automatic.track(read, [0, 15, 30, 45, 60], set())
    assert 52 in visited


def test_bracketed_single_digit_is_not_trusted_as_a_letter():
    assert not automatic.reliable_wording(detection("You know [1 want"))
    assert automatic.reliable_wording(detection("There are 10 apples"))


def test_completion_duplicate_signatures_do_not_inflate_unresolved_count():
    from dataclasses import replace

    d = replace(detection("Unclear words"), minimum_confidence=40)
    result = completion({0: [d]}, {0: [(d.rectangle, "glyphs")]}, {0: [(d.rectangle, b"a")] * 20})
    assert len(result["unresolved_regions"]) == 1
    assert result["unresolved_regions"][0]["observations"] == 1


def test_prior_region_visibility_checks_are_not_new_unreadable_captions():
    r = detection("Prior words").rectangle
    # A flat background can repeat perfectly inside a disappeared caption's old rectangle.
    result = completion({0: [], 15: []}, signatures={0: [(r, b"aa")], 15: [(r, b"aa")]})
    assert result["status"] == "no_captions"
    assert result["resolved"]["checked_prior_region"] == 2


def test_only_observed_unchanging_small_corner_labels_are_incidental():
    from dataclasses import replace

    d = detection("Camera label")
    d.rectangle = automatic.match.Rectangle(x=0.04, y=0.03, width=0.19, height=0.02)
    cache = {f: [d] for f in (0, 15, 30, 45, 60)}
    assert automatic.persistent_edge_text(cache, set(cache)) == {"Camera label"}
    assert not automatic.persistent_edge_text(cache, set(cache) | {75})
    cache[30] = [replace(d, text="Changing caption")]
    cache[45] = []
    assert not automatic.persistent_edge_text(cache, set(cache))
    assert not automatic.persistent_edge_text(
        {f: [detection("Centered caption")] for f in cache}, set(cache)
    )


def test_vector_glyph_similarity_retains_exact_byte_metric():
    a = bytes(range(256)) * 48
    b = bytes(reversed(range(256))) * 48
    expected = 2 * sum(min(x, y) for x, y in zip(a, b)) / (sum(a) + sum(b))
    assert automatic.glyph_similarity(a, b) == expected
    assert automatic.glyph_similarity(b"\0" * 12, b"\0" * 12) == 0


def test_recognition_representatives_cover_late_changes_without_reading_every_frame():
    first = detection("Geometry only")
    a, b = bytes([255, 0] * 2048), bytes([0, 255] * 2048)
    signatures = {f: [(first.rectangle, a if f < 90 else b)] for f in range(0, 180, 15)}
    selected = automatic.recognition_frames(signatures)
    assert len(selected) == 4
    assert sum(f < 90 for f in selected) == sum(f >= 90 for f in selected) == 2
    assert len(set(selected)) == len(selected)


def test_region_score_keeps_a_complete_block_instead_of_one_strong_line(monkeypatch):
    pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (960, 540), (30, 30, 30))
    full, single = Image.new("L", image.size), Image.new("L", image.size)
    draw = ImageDraw.Draw(full)
    face = ImageFont.truetype(str(fonts.BUNDLED), 30)
    draw.text((480, 100), "First genuine line", font=face, fill=255)
    draw.text((500, 145), "Second genuine line", font=face, fill=255)
    draw.text((100, 210), "A clear reply", font=face, fill=255)
    single.paste(full.crop((0, 200, 960, 260)), (0, 200))
    monkeypatch.setattr(automatic, "color_masks", lambda im: iter([(single, False), (full, True)]))
    crops = automatic.retry_crops(image, [])
    assert crops and crops[0][1][1] < 110 and crops[0][1][3] > 230
    assert len(automatic.handwriting.line_crops(image, crops[0][0], crops[0][1])) == 3


def test_real_lossless_discovery_batches_observe_the_entire_timeline(tmp_path):
    pytest.importorskip("PIL")
    from tests.test_color_analysis import generated

    source = tmp_path / "colors.mp4"
    generated(source, ((255, 0, 0), (0, 255, 0), (0, 0, 255)), frames_per_color=12)
    deadline = time.monotonic() + 30
    video, _, _ = automatic.render.probe(source, tmp_path, deadline)
    frames = [0, 15, 29, 30, 45, 59, 60, 75, 89]
    rows = list(
        automatic.discovery_images(
            {"path": source}, video, frames, (64, 64), tmp_path, threading.Event(), deadline
        )
    )
    assert [f for f, _ in rows] == frames
    assert [max(range(3), key=lambda c: image.getpixel((20, 20))[c]) for _, image in rows] == [
        0,
        0,
        0,
        1,
        1,
        1,
        2,
        2,
        2,
    ]
    assert not (tmp_path / "discovery-batch").exists()


@pytest.mark.parametrize(
    "a,b,conflict",
    [
        ("FIRSTLINE", "FIRST LINE", False),
        ("the final words", "Read the final words", False),
        ("the final words", "the final word", True),
        ("Two actual readings", "Two different readings", True),
    ],
)
def test_credible_readers_cannot_silently_overwrite_different_characters(a, b, conflict):
    assert automatic.wording_conflicts(a, b) is conflict


def test_recognizer_conflict_blocks_even_when_retained_ocr_is_reliable():
    d = detection("Retained source words")
    completion = automatic.completion_evidence(
        {0: [d]},
        {0: [(d.rectangle, bytes([255, 0] * 2048))]},
        {0: [(d.rectangle, "glyphs")]},
        {},
        {0: "Credible recognizers disagree"},
        {0},
        0,
        15,
        set(),
    )
    assert completion["status"] == "partial"
    assert completion["unresolved_regions"][0]["reason"] == "conflicting_readings"


@pytest.mark.parametrize("mixed_script", [False, True])
def test_real_confident_ocr_still_checks_existing_independent_reading(
    local, monkeypatch, mixed_script
):
    pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    client, directory, _ = local
    source = directory / "independent-readers.mp4"
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg required")
    capability = automatic.ocr.inspect_capability(directory, time.monotonic() + 10)
    if not capability["available"]:
        pytest.skip("Installed pinned OCR required")
    font = str(fonts.BUNDLED).replace("\\", "/").replace(":", r"\:")
    subprocess.run(
        [
            shutil.which("ffmpeg"),
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x204020:s=640x360:r=30:d=1",
            "-vf",
            f"drawtext=fontfile='{font}':text='CLEAR SOURCE WORDS':fontsize=40:"
            "fontcolor=white:x=(w-tw)/2:y=160",
            "-c:v",
            "libx264",
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=15,
    )
    saved, _, _ = prepared(client, directory, source)
    monkeypatch.setattr(automatic.handwriting, "available", lambda: True)
    if mixed_script:
        parse = automatic.parse

        def mixed_ocr(*args, **kwargs):
            rows = parse(*args, **kwargs)
            for row in rows:
                row.text += " كلمة"
            return rows

        monkeypatch.setattr(automatic, "parse", mixed_ocr)
    import json

    original = automatic.caption_readings.read

    def independent(project_id, kind, namespace, name):
        if kind != "handwriting":
            return original(project_id, kind, namespace, name)
        return json.dumps(
            {
                "engine": "fixture-independent",
                "results": [
                    {"name": name, "text": "OTHER SOURCE WORDS", "score": 0.99, "capped": False}
                ],
            }
        ).encode()

    monkeypatch.setattr(automatic.caption_readings, "read", independent)
    stage = directory / "independent-stage"
    stage.mkdir()
    cues, _, evidence = automatic.detect(
        saved["id"], color.source(saved["id"]), stage, threading.Event(), time.monotonic() + 90
    )
    assert evidence["cached_handwriting_requests"] > 0
    assert evidence["handwriting_requests"] == 0
    assert not cues  # Contradictory recognizers cannot silently supply a publishable wording.
    assert any(
        r["reason"] == "conflicting_readings" for r in evidence["completion"]["unresolved_regions"]
    )


def test_supported_unequal_line_heights_are_not_dropped_from_the_caption_block():
    small, large = detection("A smaller source line"), detection("A larger source line")
    small.rectangle = small.rectangle.model_copy(update={"y": 0.2, "height": 0.025})
    large.rectangle = large.rectangle.model_copy(update={"y": 0.3, "height": 0.06})
    assert automatic.dominant([large, small], set()).text == small.text + "\n" + large.text


def test_unfinished_full_frame_fallback_is_not_no_captions():
    completion = automatic.completion_evidence(
        {0: []},
        {0: []},
        {0: []},
        {},
        {0: "Detected region has no completed representative reading"},
        {0},
        0,
        15,
        set(),
    )
    assert completion["status"] == "partial"
    assert completion["observed_discovery_frames"] == 1
    assert completion["unresolved_regions"][0]["reason"] == "unreadable_caption"


@pytest.mark.parametrize("background,foreground", [("white", "black"), ("0xbbbbbb", "0x505050")])
def test_real_dark_printed_caption_retains_full_frame_ocr(
    local, monkeypatch, background, foreground
):
    pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    client, directory, _ = local
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg required")
    capability = automatic.ocr.inspect_capability(directory, time.monotonic() + 10)
    if not capability["available"]:
        pytest.skip("Installed pinned OCR required")
    source = directory / "dark-text.mp4"
    font = str(fonts.BUNDLED).replace("\\", "/").replace(":", r"\:")
    subprocess.run(
        [
            shutil.which("ffmpeg"),
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c={background}:s=640x360:r=30:d=1",
            "-vf",
            f"drawtext=fontfile='{font}':text='CLEAR DARK WORDS':fontsize=40:"
            f"fontcolor={foreground}:"
            "x=(w-tw)/2:y=160",
            "-c:v",
            "libx264",
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=15,
    )
    saved, _, _ = prepared(client, directory, source)
    monkeypatch.setattr(automatic.handwriting, "available", lambda: False)
    monkeypatch.setattr(automatic.match, "read", lambda *args: {"revision": 1})
    stage = directory / "dark-stage"
    stage.mkdir()
    cues, _, evidence = automatic.detect(
        saved["id"], color.source(saved["id"]), stage, threading.Event(), time.monotonic() + 90
    )
    assert [c.text for c in cues] == ["CLEAR DARK WORDS"]
    assert evidence["completion"]["status"] == "complete"
    assert evidence["completion"]["observed_discovery_frames"] == 3


def test_reliable_similar_words_are_real_timeline_changes():
    before, after = detection("We can walk away"), detection("We can talk away")
    assert not automatic.same(before, after)
    changes = automatic.track(lambda f: [before if f < 8 else after], [0, 15, 29], set())
    assert [(f, d.text) for f, d in changes] == [(0, before.text), (8, after.text)]


def test_partial_retry_cannot_replace_supported_complete_line():
    full, partial = detection("You can talk to me"), detection("talk to me")
    full.confidence, partial.confidence = 88, 99
    assert automatic.merge_readings([full], [partial]) == [full]
    other = detection("You can walk to me")
    other.confidence = 99
    assert automatic.merge_readings([full], [other]) == [other]


def test_pixel_supported_complete_line_survives_partial_reading_not_conflict():
    full, partial = detection("You can talk to me"), detection("talk to me")
    shape = b"\xff\x00" * 100
    recovered = automatic.recover_lines(
        15, [(full.rectangle, shape)], [(0, full.rectangle, shape, full)], [partial]
    )
    assert [d.text for d in recovered] == [full.text]
    conflict = detection("You can walk to me")
    assert automatic.recover_lines(
        15, [(full.rectangle, shape)], [(0, full.rectangle, shape, full)], [conflict]
    ) == [conflict]


def test_caption_staging_retries_transient_windows_lock_and_retains_unsafe_stage(
    tmp_path, monkeypatch
):
    original = automatic.shutil.rmtree
    calls = []

    def transient(path):
        calls.append(path)
        if len(calls) == 1:
            raise PermissionError("transient lock after process exit")
        original(path)

    monkeypatch.setattr(automatic.shutil, "rmtree", transient)
    stage = tmp_path / "safe"
    with automatic.temporary(stage):
        (stage / "owned").write_text("temporary")
    assert len(calls) == 2 and not stage.exists()
    unsafe = tmp_path / "unsafe"
    with pytest.raises(automatic.engine.ProcessCleanupError):
        with automatic.temporary(unsafe):
            raise automatic.engine.ProcessCleanupError()
    assert unsafe.exists() and len(calls) == 2


def test_caption_staging_persistent_lock_reports_failure(tmp_path, monkeypatch):
    clock = iter([0, 3])
    monkeypatch.setattr(automatic.time, "monotonic", lambda: next(clock))

    def locked(path):
        raise PermissionError("still locked")

    monkeypatch.setattr(automatic.shutil, "rmtree", locked)
    stage = tmp_path / "locked"
    with pytest.raises(ReferenceError) as error:
        with automatic.temporary(stage):
            pass
    assert error.value.code == "cleanup_failure" and stage.exists()


def test_selected_model_pair_can_actually_corroborate_within_the_agreement_window():
    from dataclasses import replace

    d = detection("Independent observed words")
    rect = tuple(d.rectangle.model_dump().values())
    shape = bytes([255, 0] * 2048)
    # Discovery order places both endpoints in a group centered on frame 150.
    rows = [(f, rect, [f"{f}-mask", f"{f}-gray", f"{f}-context"]) for f in (150, 105, 180, 60)]
    cached = {name for _, _, names in rows for name in names}
    readings = {
        f: [replace(d, sharpness=score)] for f, score in ((150, 1), (105, 8), (180, 9), (60, 10))
    }
    selected = automatic.select_handwriting(
        rows,
        8,
        signatures={f: [(d.rectangle, shape)] for f, _, _ in rows},
        readings=readings,
        cached_names=cached,
    )
    assert [row[0] for row in selected] == [60, 105]
    assert all(any(3 <= abs(other[0] - row[0]) <= 90 for other in selected) for row in selected)


def test_partial_discovery_mask_does_not_turn_unselected_frames_into_caption_gaps(
    local, monkeypatch
):
    pytest.importorskip("PIL")
    from PIL import ImageDraw

    original = automatic.discovery_images

    def partial_thumbnails(*args, **kwargs):
        for frame, image in original(*args, **kwargs):
            if frame not in {15, 60}:
                # Emulate a thumbnail mask losing part of a line; native source stays intact.
                image = image.copy()
                ImageDraw.Draw(image).rectangle(
                    (0, 0, image.width // 2, image.height), fill=(32, 64, 32)
                )
            yield frame, image

    monkeypatch.setattr(automatic, "discovery_images", partial_thumbnails)
    monkeypatch.setattr(automatic, "recognition_frames", lambda _: [15, 60])
    test_real_visible_text_timing_and_appearance(local, monkeypatch, True)


def test_overlapping_different_region_cannot_inherit_caption_words():
    line = detection("Observed lower caption")
    line.rectangle = automatic.match.Rectangle(x=0.25, y=0.47, width=0.4, height=0.055)
    title = automatic.match.Rectangle(x=0.3, y=0.44, width=0.31, height=0.105)
    assert automatic.overlaps_line(title, line.rectangle)
    shape = b"\xff\x00" * 100
    prototypes = []
    automatic.remember_lines(30, [(title, shape)], [line], prototypes)
    assert prototypes == []
    assert automatic.recover_lines(45, [(title, shape)], prototypes, []) == []


def test_verified_glyph_continuity_retains_wording_and_sample_provenance():
    line = detection("Independently read caption")
    line.frame_index = 0
    line.sample_rectangle = line.rectangle
    line.sample_text = line.text
    shape = b"\xff\x00" * 100
    prototypes = [(0, line.rectangle, shape, line)]
    for frame in (60, 120, 180):
        signatures = [(line.rectangle, shape)]
        rows = automatic.recover_lines(frame, signatures, prototypes, [])
        assert [d.text for d in rows] == [line.text]
        assert rows[0].frame_index == 0 and rows[0].sample_rectangle == line.rectangle
        automatic.remember_lines(frame, signatures, rows, prototypes)
    assert automatic.recover_lines(195, [], prototypes, []) == []
    changed = b"\x00\xff" * 100
    assert automatic.recover_lines(195, [(line.rectangle, changed)], prototypes, []) == []


def test_conflicting_ocr_cannot_reappear_as_reliable_at_refinement_frames():
    disputed = detection("A disputed final word")
    conflicts = [(30, disputed)]
    for index in (30, 33, 60, 120):
        copied = detection(disputed.text)
        assert automatic.conflicting_reading(index, copied, conflicts)
    assert not automatic.conflicting_reading(121, disputed, conflicts)
    independent = detection("A different observed reading")
    assert not automatic.conflicting_reading(33, independent, conflicts)
    independent.text = disputed.text
    independent.rectangle = automatic.match.Rectangle(x=0.2, y=0.1, width=0.5, height=0.1)
    assert not automatic.conflicting_reading(33, independent, conflicts)


def test_harmonize_shares_one_style_per_lettering_group_and_keeps_manual_styles():
    hand, plain = fonts.builtin("patrickhand-regular"), fonts.Binding()

    def cue(start, size, ink, binding, horizontal, origin="assisted"):
        return captions.Cue(
            start=start,
            end=start + 1,
            text=f"Cue {start}",
            appearance=captions.Appearance(
                style=captions.Style(
                    font=binding.choice,
                    font_origin=origin,
                    color=ink,
                    size_percent=size,
                    outline_percent=0.0,
                    horizontal=horizontal,
                    vertical=0.5,
                ),
                font_binding=binding,
            ),
        )

    yellow = [
        cue(0, 1.8, "#F4C710", plain, 0.4),
        cue(1, 2.9, "#F0C716", hand, 0.5),
        cue(3, 3.3, "#F0C915", hand, 0.6),
        cue(4, 3.7, "#E8C561", hand, 0.45),
    ]
    title = cue(2, 5.1, "#F5E8DB", plain, 0.5)
    manual = cue(5, 1.0, "#F0C716", plain, 0.3, origin="manual")
    result = automatic.harmonize([yellow[0], yellow[1], title, *yellow[2:], manual])
    shared = [result[i] for i in (0, 1, 3, 4)]
    # Median of the dominant font's calibrated sizes (2.9, 3.3, 3.7); the default-font 1.8 is not.
    assert [c.appearance.style.size_percent for c in shared] == [pytest.approx(3.3)] * 4
    assert {c.appearance.style.color for c in shared} == {"#F0C716"}
    assert {c.appearance.style.font for c in shared} == {hand.choice}
    assert [c.appearance.font_binding for c in shared] == [hand] * 4
    assert [c.appearance.style.horizontal for c in shared] == [0.4, 0.5, 0.6, 0.45]
    assert result[2] == title and result[5] == manual
    # An early small partial reading must not split the lettering into two size groups.
    early = [cue(i, size, "#F0C716", hand, 0.5) for i, size in enumerate((1.4, 2.6, 3.1, 3.4))]
    assert len({c.appearance.style.size_percent for c in automatic.harmonize(early)}) == 1


def test_reference_placement_maps_into_each_slots_pillarboxed_or_full_picture(monkeypatch):
    import types

    import framing
    import sequence

    portrait, wide = object(), object()
    slots = [
        types.SimpleNamespace(id="a", clip_id=portrait, output_start_frame=0, output_end_frame=60),
        types.SimpleNamespace(id="b", clip_id=None, output_start_frame=60, output_end_frame=90),
        types.SimpleNamespace(id="c", clip_id=wide, output_start_frame=90, output_end_frame=150),
    ]
    monkeypatch.setattr(
        sequence,
        "read",
        lambda _: types.SimpleNamespace(sequence=types.SimpleNamespace(slots=slots)),
    )
    monkeypatch.setattr("clip_library.source", lambda _, clip_id, **k: (clip_id, None))
    sizes = {portrait: (720, 1280), wide: (1920, 1080)}
    monkeypatch.setattr(
        automatic.render,
        "probe",
        lambda path, *a, **k: ({"width": sizes[path][0], "height": sizes[path][1]}, None, 1),
    )
    settings = framing.Settings(format="reference", canvas=(1280, 720), fit="fit")
    areas = automatic.picture_areas("p", settings, (1280, 720), None, None, None)
    # 9:16 footage fits as a centered 404-pixel pillar; 16:9 footage fills the canvas.
    assert [(start, end) for start, end, _ in areas] == [(0, 2), (3, 5)]
    assert areas[0][2] == pytest.approx((438 / 1280, 0, 404 / 1280, 1))
    assert areas[1][2] == (0, 0, 1, 1)

    style = captions.Style(horizontal=0.25, vertical=0.9)
    cue = captions.Cue(start=0.5, end=1.5, text="Pillarboxed")
    assert automatic.into_picture(cue, style, areas) == areas[0][2]
    assert (style.horizontal, style.vertical) == pytest.approx((438 / 1280 + 101 / 1280, 0.9))
    wide_style = captions.Style(horizontal=0.25, vertical=0.9)
    automatic.into_picture(captions.Cue(start=3.5, end=4, text="Full"), wide_style, areas)
    assert (wide_style.horizontal, wide_style.vertical) == (0.25, 0.9)


def test_reference_size_maps_to_picture_height_and_shrinks_only_on_overflow(monkeypatch, tmp_path):
    import types

    import framing

    areas = [
        (0, 1, (0.0, 0.125, 1.0, 0.75)),  # Letterboxed: picture is 75% of canvas height.
        (1, 2, (0.3, 0.0, 0.4, 1.0)),  # Pillarboxed: full height, 40% of canvas width.
        (2, 3, (0.3, 0.0, 0.4, 1.0)),
    ]
    monkeypatch.setattr(automatic.render.footage, "source", lambda *a: {"path": "footage"})
    monkeypatch.setattr(automatic.render, "probe", lambda *a, **k: ({}, None, 1))
    monkeypatch.setattr(
        framing, "read", lambda _: types.SimpleNamespace(settings=framing.Settings())
    )
    monkeypatch.setattr(framing, "geometry", lambda *a: (None, (1280, 720), ""))
    monkeypatch.setattr(automatic, "picture_areas", lambda *a: areas)
    monkeypatch.setattr(automatic.fonts, "prepare", lambda *a: None)
    # At 10% of a 256-pixel canvas each line measures 512 pixels: at most 8.89% per canvas width.
    monkeypatch.setattr(
        automatic.appearance, "render_sample", lambda *a, **k: ((0, 0, 512, 30), None)
    )

    def cue(start, size, ink):
        return captions.Cue(
            start=start,
            end=start + 1,
            text="Same width line",
            appearance=captions.Appearance(
                style=captions.Style(
                    font_origin="assisted",
                    color=ink,
                    size_percent=size,
                    horizontal=0.5,
                    vertical=0.9,
                    outline_percent=0.0,
                ),
                font_binding=fonts.Binding(),
            ),
        )

    letterboxed, fits, overflows = automatic.fit_review_width(
        "p",
        [cue(0, 8.0, "#FFFFFF"), cue(1, 3.0, "#FFFF00"), cue(2, 8.0, "#0000FF")],
        tmp_path,
        None,
        time.monotonic() + 20,
    )
    # Reference size is relative to its frame height, which becomes the picture height.
    assert letterboxed.appearance.style.size_percent == pytest.approx(6.0)
    assert letterboxed.appearance.style.vertical == pytest.approx(0.125 + 0.9 * 0.75)
    # A pillar keeps the full-height size while the line fits inside the picture...
    assert fits.appearance.style.size_percent == pytest.approx(3.0)
    assert fits.appearance.style.horizontal == pytest.approx(0.5)
    # ...and shrinks only when the line would overflow 90% of the picture width.
    assert overflows.appearance.style.size_percent == pytest.approx(
        2560 * 0.36 * 1280 / (512 * 720)
    )


def test_harmonize_prefers_a_fitted_font_over_the_unfitted_default():
    hand, plain = fonts.builtin("patrickhand-regular"), fonts.Binding()
    cues = [
        captions.Cue(
            start=i,
            end=i + 1,
            text=f"Cue {i}",
            appearance=captions.Appearance(
                style=captions.Style(
                    font=binding.choice,
                    font_origin="assisted",
                    color="#F0C716",
                    # Each size is calibrated to its own font's glyph height.
                    size_percent=6.0 if binding is plain else 7.8,
                    outline_percent=0.0,
                ),
                font_binding=binding,
            ),
        )
        for i, binding in enumerate([plain, plain, hand, plain, hand])
    ]
    shared = automatic.harmonize(cues)
    assert {c.appearance.style.font for c in shared} == {hand.choice}
    assert {c.appearance.style.size_percent for c in shared} == {7.8}


def test_patrick_hand_size_renders_the_reference_ink_height(local):
    _, directory, _ = local
    binding = fonts.builtin("patrickhand-regular")
    text = "I just want you to promise me that"
    glyph_height = 37 / 720  # A measured reference line height (23.2 s, e2e evidence).

    def ink_height(size, width, height, name):
        stage = directory / name
        stage.mkdir()
        cue = [captions.Cue(start=0.0, end=1.0, text=text)]
        prepared = fonts.prepare("fixture", binding, cue, stage, time.monotonic() + 20)
        box, _ = automatic.appearance.render_sample(
            captions.Style(
                font=binding.choice, size_percent=size, outline_percent=0.0, placement="center"
            ),
            binding,
            text,
            width,
            height,
            stage,
            prepared,
            time.monotonic() + 20,
            with_mask=False,
        )
        return box[3]

    size = automatic.ink_size(glyph_height, ink_height(10.0, 4096, 256, "sample"))
    rendered = ink_height(size, 1280, 720, "rendered") / 720
    assert rendered == pytest.approx(glyph_height, rel=0.05)
    # The former font-independent 150x estimate renders Patrick Hand over 10% too tall.
    assert ink_height(150 * glyph_height, 1280, 720, "fixed") / 720 > 1.1 * glyph_height


def test_small_printed_caption_is_proposed_when_brighter_scenery_wins_the_mask():
    pytest.importorskip("PIL")  # Optional visual extra; CI may not install it.
    from PIL import Image, ImageDraw, ImageFont

    width, height = 960, 720
    image = Image.new("RGB", (width, height), (176, 70, 150))  # Bright pink scene.
    draw = ImageDraw.Draw(image)
    # Wide, sparse neon speckle mid-frame outscores small solid text in the single-mask vote.
    for x in range(330, 700, 7):
        for y in range(200, 236, 9):
            draw.rectangle([x, y, x + 1, y + 6], fill=(120, 240, 250))
    draw.rectangle([0, 640, width, height], fill=(20, 15, 40))
    font = ImageFont.truetype(str(fonts.BUNDLED), 22)
    draw.text((width / 2, 690), "what is?", font=font, fill=(235, 200, 50), anchor="ms")
    crops = automatic.retry_crops(image, [])
    caption = [box for _, box, _ in crops if box[1] >= 0.85 * height]
    assert caption, [box for _, box, _ in crops]
    lines = automatic.handwriting.line_crops(
        image, *next((mask, box) for mask, box, _ in crops if box[1] >= 0.85 * height)
    )
    assert [round(r[1], 2) for r, _ in lines] == [0.94]


def test_caption_whose_top_is_below_94_percent_of_the_frame_is_still_proposed():
    pytest.importorskip("PIL")  # Optional visual extra; CI may not install it.
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (960, 720), (20, 15, 40))
    font = ImageFont.truetype(str(fonts.BUNDLED), 24)
    # Lowercase without ascenders: the ink starts at about 0.95 of the frame height, as the
    # printed reference's "it's over." does (top 0.947).
    ImageDraw.Draw(image).text(
        (480, 696), "now so a mess", font=font, fill=(235, 200, 50), anchor="ms"
    )
    assert automatic.retry_crops(image, [])


def test_stability_vote_treats_apostrophe_only_differences_as_the_same_wording():
    # Printed reference: "i'm done embarrassing myself" was read twice, once as "im", and the
    # split vote dropped the cue as isolated wording.
    assert automatic.vote_key("i’m done embarrassing myself") == automatic.vote_key(
        "im done embarrassing myself"
    )
    assert automatic.vote_key("i'm done") != automatic.vote_key("i'm gone")


def render_box(directory, binding, text, size, width, height, vertical=0.5):
    directory.mkdir()
    prepared = fonts.prepare(
        "fixture",
        binding,
        [captions.Cue(start=0.0, end=1.0, text=text)],
        directory,
        time.monotonic() + 20,
    )
    box, _ = automatic.appearance.render_sample(
        captions.Style(
            font=binding.choice,
            size_percent=size,
            outline_percent=0.0,
            vertical=vertical,
            horizontal=0.5,
        ),
        binding,
        text,
        width,
        height,
        directory,
        prepared,
        time.monotonic() + 20,
        with_mask=False,
    )
    return box


def test_partial_read_fallback_picks_a_condensed_face_for_narrow_printed_text(local):
    _, directory, _ = local
    text = "i can't pay back the theatre"
    boxes = {
        binding.choice: render_box(directory / binding.choice, binding, text, 10.0, 4096, 256)
        for binding in automatic.PRINTED_FACES
    }
    # Printed reference: this line is 288 px wide with 26 px glyph height (aspect 11.1).
    assert automatic.printed_face(boxes, 288 / 26) == "barlowcondensed-regular"
    wide = boxes["default"][2] / boxes["default"][3]
    assert automatic.printed_face(boxes, wide) == "default"


def test_fallback_anchor_puts_rendered_ink_bottom_on_the_observed_line(local):
    _, directory, _ = local
    binding, text, size = fonts.builtin("barlowcondensed-regular"), "nobody showed up.", 4.5
    sample = render_box(directory / "sample", binding, text, 10.0, 4096, 256)
    vertical = automatic.baseline_vertical(0.968, size, sample)
    rendered = render_box(directory / "rendered", binding, text, size, 1280, 720, vertical)
    assert (rendered[1] + rendered[3]) / 720 == pytest.approx(0.968, abs=1.5 / 720)


def test_lines_of_one_lettering_get_consistent_sizes_with_or_without_apostrophes(local):
    _, directory, _ = local
    binding = fonts.builtin("patrickhand-regular")
    # Handwritten reference: these lines share one letter height on screen, but the font's
    # straight apostrophe rises above its ascenders, so "I've ..." estimated 16% smaller.
    lines = [
        "Boys you meet,",
        "you Can talk to me about anything ..",
        "I've done it all and you can too .",
        "No, I’m serious, Soph.",
    ]
    sizes = [
        automatic.ink_size(
            0.054,
            render_box(
                directory / str(n), binding, automatic.letter_sample(line), 10.0, 4096, 256
            )[3],
        )
        for n, line in enumerate(lines)
    ]
    assert max(sizes) <= 1.06 * min(sizes), sizes
    assert automatic.letter_sample("i can't pay") == "i cant pay"
    assert automatic.letter_sample("''") == "''"  # Nothing left to measure: keep the line.
