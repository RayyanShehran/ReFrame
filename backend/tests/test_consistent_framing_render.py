"""One small mixed-source export exercises automatic evidence and manual overrides."""

import base64
import shutil
import subprocess

import clip_library
import clips
import video_render as render
from tests.test_color_analysis import finish
from tests.test_edit_plan import seed_pacing
from tests.test_framing_render import frame, pixel
from tests.test_projects import REAL_PROBE
from tests.test_video_render import local as local  # noqa: F401
from tests.test_video_render import prepared, selected


def test_mixed_real_sources_borders_dark_fit_override_preview_and_caption_canvas(
    local, monkeypatch
):
    client, directory, _ = local
    filters = [
        "color=green:s=320x180",
        "color=blue:s=180x320",
        "color=red:s=240x240",
        "color=yellow:s=320x180,pad=320:240:0:30:black",
        "color=cyan:s=180x320,pad=240:320:30:0:black",
        "color=0x080808:s=320x180",
        "color=blue:s=320x180,drawbox=x=0:y=0:w=100:h=180:color=red:t=fill",
    ]
    paths = []
    for i, f in enumerate(filters):
        path = directory / f"mixed-{i}.mp4"
        subprocess.run(
            [
                shutil.which("ffmpeg"),
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                f + ",setsar=1",
                "-t",
                "7" if i == 0 else "2",
                "-r",
                "30",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(path),
            ],
            check=True,
            capture_output=True,
            timeout=15,
        )
        paths.append(path)
    saved, url, _ = prepared(client, directory, paths[0])
    pid = saved["id"]
    base = f"/api/projects/{pid}"
    selected(client, url, dict(brightness=0, contrast=1, saturation=1))
    monkeypatch.setattr(clips, "probe_file", REAL_PROBE)
    for path in paths[1:]:
        response = client.post(
            base + "/clips", files={"file": (path.name, path.read_bytes(), "video/mp4")}
        )
        assert response.status_code == 200, response.text
    seed_pacing(pid, (1,) * 7, 7)
    seq = client.post(base + "/sequence/generate", json={"expected_revision": 0}).json()["sequence"]
    library = clip_library.read(pid).clips
    assigned = [
        dict(id=s["id"], clip_id=str(c.id), duration_frames=30, source_start_frame=0)
        for s, c in zip(seq["slots"], library)
    ]
    response = client.post(base + "/sequence", json={"expected_revision": 1, "slots": assigned})
    assert response.status_code == 200, response.text
    slots = response.json()["sequence"]["slots"]
    caption = client.post(
        base + "/captions",
        json=dict(
            expected_revision=0,
            enabled=True,
            mode="sequence",
            expected_sequence_revision=2,
            cues=[dict(start=0, end=1, text="Canvas")],
            style=dict(placement="center", size_percent=10),
        ),
    )
    assert caption.status_code == 200, caption.text
    before = client.get(base + "/captions").json()
    controls = {slots[-1]["id"]: dict(fit="fill", horizontal=0, vertical=0.5, zoom=1.8, bars="off")}
    response = client.post(
        base + "/framing",
        json=dict(
            expected_revision=0, format="square", fit="automatic", bars="auto", slots=controls
        ),
    )
    assert response.status_code == 200, response.text
    settings = response.json()["settings"]
    decisions = settings["decisions"]
    assert decisions[slots[3]["id"]]["insets"][1] > 0.1
    assert decisions[slots[4]["id"]]["insets"][0] > 0.1
    assert decisions[slots[5]["id"]]["insets"] == [0, 0, 0, 0]
    assert client.get(base + "/framing").json() == response.json()
    assert client.get(base + "/captions").json() == before
    # Compare preview/export in the same saved color mode on every FFmpeg version.
    assert (
        client.post(base + "/grading", json=dict(expected_revision=0, mode="original")).status_code
        == 200
    )
    response = client.post(
        base + "/render",
        json=dict(
            expected_revision=0,
            expected_grading_revision=1,
            expected_sequence_revision=2,
            expected_caption_revision=1,
            expected_framing_revision=1,
        ),
    )
    assert response.status_code == 202, response.text
    done = finish(client, base + "/render")
    assert done["status"] == "ready", done
    output = done["output"]
    assert (output["width"], output["height"], output["decoded_frames"]) == (720, 720, 210)
    path = render.destination(pid, output["output_id"])
    shots = [frame(path, i + 0.5) for i in range(7)]
    assert pixel(shots[3], 36, 36)[0] > 180 and pixel(shots[3], 36, 36)[1] > 180
    assert pixel(shots[4], 36, 36)[1] > 150 and pixel(shots[4], 36, 36)[2] > 150
    assert max(pixel(shots[5], 36, 36)) < 20
    assert pixel(shots[-1], 5, 36)[0] > 150  # noncentral manual crop retains the red subject
    # Fit fallback retains all landscape/square pixels; captions compose onto the final canvas.
    assert max(pixel(shots[0], 5, 5)) < 20
    assert pixel(shots[0], 36, 36)[0] > 25
    # Real preview agrees with the corresponding export, with grading set to original.
    preview = client.post(
        base + "/frame-preview",
        json=dict(
            expected_recipe_revision=0,
            expected_grading_revision=1,
            expected_framing_revision=1,
            expected_sequence_revision=2,
            clip_id=slots[-1]["clip_id"],
            slot_id=slots[-1]["id"],
            timestamp_seconds=0.5,
        ),
    )
    assert preview.status_code == 200, preview.text
    png = directory / "mixed-preview.png"
    png.write_bytes(base64.b64decode(preview.json()["edited"]["png_base64"]))
    actual = frame(png, 0)
    expected = shots[-1]
    assert sum(abs(a - b) for a, b in zip(actual, expected)) / len(actual) < 6
    # Changing only one saved source interval invalidates just its decision until a framing save.
    assigned[-1]["source_start_frame"] = 10
    assert (
        client.post(base + "/sequence", json=dict(expected_revision=2, slots=assigned)).status_code
        == 200
    )
    stale = client.post(
        base + "/render",
        json=dict(
            expected_revision=0,
            expected_grading_revision=1,
            expected_sequence_revision=3,
            expected_caption_revision=1,
            expected_framing_revision=1,
        ),
    )
    assert stale.status_code == 409
