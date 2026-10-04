import shutil
import subprocess

import captions
import projects
import video_render as render
from tests.test_audio_render import amplitude, samples
from tests.test_color_analysis import finish
from tests.test_edit_plan import seed_pacing
from tests.test_video_render import local as local  # noqa: F401
from tests.test_video_render import prepared, selected


def fixture(path):
    subprocess.run(
        [
            shutil.which("ffmpeg"),
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x404040:s=480x270:r=30:d=4",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=4",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=15,
    )


def frames(path, indices):
    selected_frames = "+".join(f"eq(n,{i})" for i in indices)
    return subprocess.run(
        [
            shutil.which("ffmpeg"),
            "-v",
            "error",
            "-i",
            str(path),
            "-vf",
            f"select='{selected_frames}'",
            "-fps_mode",
            "passthrough",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "-",
        ],
        check=True,
        capture_output=True,
        timeout=15,
    ).stdout


def test_real_captions_half_open_arabic_literals_after_cuts_and_grade_audio_unchanged(
    local, monkeypatch
):
    client, directory, _ = local
    directory = directory / "quoted' [captions],; export"
    monkeypatch.setattr(projects, "DATA_DIR", directory)
    projects.initialize()
    source = directory / "captions-source.mp4"
    fixture(source)
    saved, recipe, _ = prepared(client, directory, source)
    pid = saved["id"]
    revision = selected(client, recipe, {"brightness": -0.2, "contrast": 1, "saturation": 1})
    seed_pacing(pid)
    plan = f"/api/projects/{pid}/edit-plan"
    assert client.post(plan + "/generate", json={"expected_revision": 0}).status_code == 200
    assert (
        client.post(
            plan, json={"expected_revision": 1, "source_starts_seconds": [0, 2, 3]}
        ).status_code
        == 200
    )
    url = f"/api/projects/{pid}/captions"
    body = {
        "expected_revision": 0,
        "mode": "cuts",
        "expected_plan_revision": 2,
        "enabled": True,
        "style": {"size": "large", "placement": "center"},
        "cues": [
            {"start": 0.5, "end": 1, "text": "Hello ReFrame"},
            {"start": 1.2, "end": 1.7, "text": "مرحبا بالعالم"},
            {"start": 2, "end": 2.6, "text": r"{\b1}literal \N <i>"},
        ],
    }
    assert client.post(url, json=body).status_code == 200
    endpoint = f"/api/projects/{pid}/render"
    request = {
        "expected_revision": revision,
        "expected_plan_revision": 2,
        "expected_caption_revision": 1,
    }
    assert client.post(endpoint, json=request).status_code == 202
    result = finish(client, endpoint)
    assert result["status"] == "ready", (result["failure_code"], result["message"])
    output = render.Output.model_validate(result["output"])
    assert output.decoded_frames == 90
    # FFmpeg 6 leaves AAC tail padding; allow one 1024-sample codec frame.
    assert abs(output.decoded_audio_samples - 144000) <= 1024
    assert output.spec.captions.cues[2].text == body["cues"][2]["text"]
    path = render.destination(pid, output.output_id)
    raw = frames(path, [0, 15, 30, 36, 51, 60, 78])
    size = 480 * 270 * 3
    assert len(raw) == size * 7
    images = [raw[i : i + size] for i in range(0, len(raw), size)]
    assert all(max(images[i]) < 25 for i in [0, 2, 4, 6])  # no caption outside [start,end)
    assert all(sum(v > 240 for v in images[i]) > 200 for i in [1, 3, 5])  # grade never darkens text
    assert images[1] != images[3] != images[5]
    sound = samples(path)
    levels = [amplitude(sound, t, 440) for t in [0.2, 0.9, 1.4, 2.2]]
    assert all(v > 0.1 for v in levels) and max(levels) / min(levels) < 1.05
    assert not list((projects.DATA_DIR / render.STAGING).glob("*"))
    assert client.get(endpoint).json()["output"] == result["output"]
    changed = client.post(url, json=body | {"expected_revision": 1, "enabled": False})
    assert changed.status_code == 200 and client.get(endpoint).json()["outdated"]
    assert client.post(endpoint, json=request).status_code == 409
    # Switching whole/cuts cannot silently rebind an enabled track.
    assert (
        client.post(url, json=body | {"expected_revision": 2, "enabled": True}).status_code == 200
    )
    assert (
        client.post(
            endpoint, json={"expected_revision": revision, "expected_caption_revision": 3}
        ).json()["error"]["code"]
        == "captions_stale"
    )


def test_missing_libass_is_useful_failure_disabled_and_legacy_remain_valid(local, monkeypatch):
    client, directory, _ = local
    saved, _, _ = prepared(client, directory)
    pid = saved["id"]
    url = f"/api/projects/{pid}/captions"
    assert (
        client.post(
            url,
            json={
                "expected_revision": 0,
                "mode": "whole",
                "enabled": True,
                "cues": [{"start": 0.2, "end": 0.5, "text": "test"}],
            },
        ).status_code
        == 200
    )
    real = render.tool

    def tool(args, *rest, **kwargs):
        if "filter=ass" in args:
            return b"Unknown filter 'ass'"
        return real(args, *rest, **kwargs)

    monkeypatch.setattr(render, "tool", tool)
    endpoint = f"/api/projects/{pid}/render"
    assert (
        client.post(
            endpoint, json={"expected_revision": 1, "expected_caption_revision": 1}
        ).status_code
        == 202
    )
    failed = finish(client, endpoint)
    assert failed["failure_code"] == "captions_unavailable" and "libass" in failed["message"]
    assert client.post(url, json={"expected_revision": 1, "mode": "whole"}).status_code == 200
    assert (
        client.post(
            endpoint, json={"expected_revision": 1, "expected_caption_revision": 2}
        ).status_code
        == 202
    )
    result = finish(client, endpoint)
    assert result["status"] == "ready", (result["failure_code"], result["message"])
    assert render.Spec.model_fields["captions"].default_factory().enabled is False
    assert captions.ass_time(0.333) == "0:00:00.33"
    assert captions.ass_time(0.334) == "0:00:00.36"
