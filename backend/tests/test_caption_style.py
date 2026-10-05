import json
import shutil
import subprocess

import pytest

import captions
import font_assets as fonts
import projects
import video_render as render
from tests.test_color_analysis import finish
from tests.test_edit_plan import seed_pacing
from tests.test_font_assets import upload
from tests.test_frame_preview import pixels
from tests.test_framing_render import frame, pixel
from tests.test_video_render import local as local  # noqa: F401
from tests.test_video_render import prepared


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    directory = tmp_path_factory.mktemp("caption-style-media")
    path = directory / "caption-source.mp4"
    font_path = captions.filter_path(fonts.BUNDLED)
    subprocess.run(
        [
            shutil.which("ffmpeg"),
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x204020:s=480x270:r=30:d=4,"
            "drawbox=x=0:y=0:w=480:h=270:color=0x202080:t=fill:enable='gte(t,2)',"
            f"drawtext=fontfile={font_path}:text='Reference font':fontsize=22:"
            "fontcolor=white:borderw=2:bordercolor=black:x=(w-tw)/2:y=h-40",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=4",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-colorspace",
            "bt709",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=15,
    )
    return path


def setup(client, directory, source):
    saved, _, _ = prepared(client, directory, source)
    pid = saved["id"]
    with projects.database() as db:
        row = db.execute("SELECT metadata FROM clips WHERE project_id=?", (pid,)).fetchone()
        metadata = json.loads(row[0]) | {"duration_seconds": 4, "width": 480, "height": 270}
        db.execute("UPDATE clips SET metadata=? WHERE project_id=?", (json.dumps(metadata), pid))
    return pid


def preview(client, pid, cue=0, caption_revision=1, plan_revision=None):
    return client.post(
        f"/api/projects/{pid}/caption-preview",
        json={
            "cue_index": cue,
            "expected_recipe_revision": 1,
            "expected_framing_revision": 0,
            "expected_caption_revision": caption_revision,
            "expected_plan_revision": plan_revision,
        },
    )


def test_custom_font_preview_matches_real_export_and_outdated_style_preserves_timing(local, source):
    client, directory, _ = local
    pid = setup(client, directory, source)
    uploaded = upload(client, pid, fonts.BUNDLED.read_bytes())
    assert uploaded.status_code == 200, uploaded.text
    body = {
        "expected_revision": 0,
        "mode": "whole",
        "enabled": True,
        "style": {
            "font": "custom",
            "size_percent": 9,
            "color": "#FFFF00",
            "outline_color": "#002244",
            "outline_percent": 0.6,
            "shadow_color": "#FF0000",
            "shadow_percent": 0.8,
            "alignment": "left",
            "horizontal": 0.12,
            "vertical": 0.45,
        },
        "cues": [{"start": 0.2, "end": 1.8, "text": "Hello مرحبا {\\b1}"}],
    }
    saved = client.post(f"/api/projects/{pid}/captions", json=body)
    assert saved.status_code == 200, saved.text
    still = preview(client, pid)
    assert still.status_code == 200, still.text
    data = still.json()
    assert 0.2 <= data["output_timestamp_seconds"] < 1.8
    assert data["source_timestamp_seconds"] == data["output_timestamp_seconds"]
    assert data["font"] == uploaded.json()["font"]
    png = pixels(data["image"], directory, "caption")
    assert (
        sum(png[i] > 180 and png[i + 1] > 140 and png[i + 2] < 100 for i in range(0, len(png), 3))
        > 80
    )  # Actual yellow subtitle pixels, absent from the green source fixture.
    endpoint = f"/api/projects/{pid}/render"
    req = {"expected_revision": 1, "expected_caption_revision": 1}
    assert client.post(endpoint, json=req).status_code == 202
    result = finish(client, endpoint)
    assert result["status"] == "ready", result
    output = render.Output.model_validate(result["output"])
    assert output.spec.captions.font_binding.model_dump(mode="json") == data["font"]
    actual = frame(
        render.destination(pid, output.output_id),
        second=data["output_timestamp_seconds"],
        width=480,
        height=270,
    )
    assert sum(abs(a - b) for a, b in zip(actual, png)) / len(png) < 3
    assert output.decoded_frames == 120 and abs(output.decoded_audio_samples - 192000) <= 1024
    changed = client.post(
        f"/api/projects/{pid}/captions",
        json=body
        | {"expected_revision": 1, "style": body["style"] | {"color": "#00FFFF", "bold": True}},
    )
    assert changed.status_code == 200
    after = changed.json()["track"]
    before = saved.json()["track"]
    assert all(
        after[k] == before[k] for k in ("cues", "timeline", "provenance", "automatic_binding")
    )
    assert client.get(endpoint).json()["outdated"] is True
    assert preview(client, pid).json()["error"]["code"] == "revision_conflict"
    path = render.destination(pid, output.output_id)
    assert path.is_file()  # Previous MP4 stays downloadable.
    replacement = upload(client, pid, fonts.BUNDLED.read_bytes(), 1, 2, True)
    assert replacement.status_code == 200
    assert client.get(endpoint).json()["outdated"] is True
    assert path.is_file()
    assert not list((directory / "preview-staging").iterdir())


def test_reference_inspection_and_cut_cue_use_the_correct_source_moment(local, source):
    client, directory, _ = local
    pid = setup(client, directory, source)
    uploaded = upload(client, pid, fonts.BUNDLED.read_bytes())
    assert uploaded.status_code == 200, uploaded.text
    media = client.get(f"/api/projects/{pid}/reference-media").json()
    inspected = client.post(
        f"/api/projects/{pid}/reference-frame",
        json={"timestamp_seconds": 2.5, "expected_reference_operation_id": media["operation_id"]},
    )
    assert inspected.status_code == 200, inspected.text
    data = inspected.json()
    raw = pixels(data["image"], directory, "reference")
    expected = frame(source, second=2.5, width=480, height=270)
    assert sum(abs(a - b) for a, b in zip(raw, expected)) / len(raw) < 3
    assert data["source"]["media_sha256"] == media["media"]["sha256"]
    seed_pacing(pid)
    plan = f"/api/projects/{pid}/edit-plan"
    assert client.post(plan + "/generate", json={"expected_revision": 0}).status_code == 200
    assert (
        client.post(
            plan, json={"expected_revision": 1, "source_starts_seconds": [0, 2, 3]}
        ).status_code
        == 200
    )
    body = {
        "expected_revision": 0,
        "mode": "cuts",
        "expected_plan_revision": 2,
        "enabled": True,
        "style": {"font": "custom", "placement": "center", "size": "large"},
        "cues": [{"start": 1.2, "end": 1.7, "text": "Cut frame مرحبا"}],
    }
    assert client.post(f"/api/projects/{pid}/captions", json=body).status_code == 200
    still = preview(client, pid, plan_revision=2)
    assert still.status_code == 200, still.text
    data = still.json()
    assert data["font"] == uploaded.json()["font"]
    assert data["source_timestamp_seconds"] == pytest.approx(data["output_timestamp_seconds"] + 1)
    raw = pixels(data["image"], directory, "cut-caption")
    color = pixel(raw, 20, 20, 480)
    assert color[2] > color[1] + 50  # Blue second range, not green output-time footage.
    endpoint = f"/api/projects/{pid}/render"
    assert (
        client.post(
            endpoint,
            json={
                "expected_revision": 1,
                "expected_caption_revision": 1,
                "expected_plan_revision": 2,
            },
        ).status_code
        == 202
    )
    output = finish(client, endpoint)["output"]
    assert output["spec"]["captions"]["font_binding"] == data["font"]
    actual = frame(
        render.destination(pid, output["output_id"]),
        second=data["output_timestamp_seconds"],
        width=480,
        height=270,
    )
    assert sum(abs(a - b) for a, b in zip(actual, raw)) / len(raw) < 3
    assert preview(client, pid, plan_revision=1).status_code == 409
