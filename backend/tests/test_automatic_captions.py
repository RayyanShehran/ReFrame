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
