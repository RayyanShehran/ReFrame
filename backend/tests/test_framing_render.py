"""One edge/tone fixture exercises actual framing in both render timelines."""

import shutil
import subprocess

import pytest

import captions
import framing
import reference_engine as engine
import reference_jobs as jobs
import transcription
import video_render as render
from tests.test_color_analysis import finish
from tests.test_edit_plan import seed_pacing
from tests.test_framing import local as local  # noqa: F401
from tests.test_transcription import completed
from tests.test_video_render import prepared, selected


def edge_fixture(path):
    subprocess.run(
        [
            shutil.which("ffmpeg"),
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=green:s=640x360:r=30:d=3,"
            "drawbox=x=0:y=0:w=160:h=360:color=red:t=fill,"
            "drawbox=x=480:y=0:w=160:h=360:color=blue:t=fill,"
            "drawbox=x=0:y=0:w=640:h=30:color=yellow:t=fill,"
            "drawbox=x=0:y=330:w=640:h=30:color=cyan:t=fill",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=3",
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
    return path


def frame(path, second=0.5, width=72, height=72):
    raw = subprocess.run(
        [
            shutil.which("ffmpeg"),
            "-v",
            "error",
            "-ss",
            str(second),
            "-i",
            str(path),
            "-vf",
            f"scale={width}:{height}",
            "-frames:v",
            "1",
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
    assert len(raw) == width * height * 3
    return raw


def pixel(raw, x, y, width=72):
    return tuple(raw[(y * width + x) * 3 : (y * width + x) * 3 + 3])


def test_actual_fit_fill_noncentral_cuts_captions_audio_and_source_geometry(local):
    client, directory, _ = local
    source = edge_fixture(directory / "edges.mp4")
    saved, recipe_url, _ = prepared(client, directory, source)
    pid = saved["id"]
    revision = selected(client, recipe_url, {"brightness": 0, "contrast": 1, "saturation": 1})
    base = f"/api/projects/{pid}"
    geometry = client.get(base + "/framing/source")
    assert geometry.status_code == 200, geometry.text
    assert geometry.json() == {
        "display_width": 640,
        "display_height": 360,
        "original_width": 640,
        "original_height": 360,
    }
    assert not list((directory / "framing-inspection").iterdir())
    for index, choices in enumerate(
        [
            {"format": "portrait", "fit": "fit"},
            {"format": "square", "fit": "fill", "horizontal": 0},
            {"format": "square", "fit": "fill", "horizontal": 1},
        ],
        1,
    ):
        assert (
            client.post(
                base + "/framing", json={"expected_revision": index - 1} | choices
            ).status_code
            == 200
        )
        body = {"expected_revision": revision, "expected_framing_revision": index}
        if index == 3:
            seed_pacing(pid, lengths=(0.5, 0.5), footage_duration=3)
            assert (
                client.post(base + "/edit-plan/generate", json={"expected_revision": 0}).status_code
                == 200
            )
            assert (
                client.post(
                    base + "/edit-plan",
                    json={"expected_revision": 1, "source_starts_seconds": [0, 2]},
                ).status_code
                == 200
            )
            assert (
                client.post(
                    base + "/captions",
                    json={
                        "expected_revision": 0,
                        "mode": "cuts",
                        "expected_plan_revision": 2,
                        "enabled": True,
                        "style": {"size": "large", "placement": "bottom-center"},
                        "cues": [{"start": 0.2, "end": 0.8, "text": "Final canvas مرحبا"}],
                    },
                ).status_code
                == 200
            )
            body |= {"expected_plan_revision": 2, "expected_caption_revision": 1}
        response = client.post(base + "/render", json=body)
        assert response.status_code == 202, response.text
        operation = finish(client, base + "/render")
        assert operation["status"] == "ready", operation
        output = render.Output.model_validate(operation["output"])
        path = render.destination(pid, output.output_id)
        assert output.has_audio and output.spec.framing.revision == index
        assert output.decoded_frames == (90 if index < 3 else 30)
        assert output.duration_seconds == pytest.approx(3 if index < 3 else 1, abs=0.04)
        assert output.decoded_audio_samples == pytest.approx(
            (3 if index < 3 else 1) * 48000, abs=1024
        )
        if index == 1:
            assert (output.width, output.height) == (720, 1280)
            raw = frame(path, height=128)
            assert max(pixel(raw, 36, 10)) < 5 and max(pixel(raw, 36, 115)) < 5
            assert pixel(raw, 5, 64)[0] > 150 and pixel(raw, 66, 64)[2] > 150
        elif index == 2:
            assert (output.width, output.height) == (720, 720)
            raw = frame(path)
            assert pixel(raw, 5, 36)[0] > 150 and pixel(raw, 66, 36)[1] > 60
        else:
            raw = frame(path)
            assert pixel(raw, 5, 36)[1] > 60 and pixel(raw, 66, 36)[2] > 150
            # White text on the final bottom canvas, absent outside the half-open cue.
            before = frame(path, second=0.1)
            after = frame(path, second=0.9)
            region = [(x, y) for y in range(53, 69) for x in range(8, 64)]

            def changed(image):
                return sum(
                    max(abs(a - b) for a, b in zip(pixel(image, x, y), pixel(before, x, y))) > 20
                    for x, y in region
                )

            assert changed(raw) > 50
            assert changed(after) <= 2


def test_snapshot_revision_outdated_playable_and_timeline_bindings_unchanged(local):
    client, directory, _ = local
    pid, output = completed(client, directory, cuts=True)
    base = f"/api/projects/{pid}"
    body = {
        "expected_revision": 0,
        "mode": "cuts",
        "expected_plan_revision": 1,
        "enabled": True,
        "cues": [{"start": 0, "end": 1, "text": "Keep timing"}],
    }
    assert client.post(base + "/captions", json=body).status_code == 200
    routes = ["color-recipe", "style-blueprint", "footage-color", "edit-plan", "audio", "captions"]
    before = [client.get(base + "/" + r).json() for r in routes]
    audio_timeline = transcription.binding(pid, captions.read(pid).track.timeline)
    assert (
        client.post(
            base + "/framing", json={"expected_revision": 0, "format": "square"}
        ).status_code
        == 200
    )
    assert before == [client.get(base + "/" + r).json() for r in routes]
    assert transcription.binding(pid, captions.read(pid).track.timeline) == audio_timeline
    assert client.get(base + "/render").json()["outdated"]
    video = client.get(base + f"/outputs/{output.output_id}/video")
    assert video.status_code == 200 and video.content == b"mock audio"
    assert video.headers["X-Framing-Revision"] == "0"
    assert (
        client.post(
            base + "/render",
            json={
                "expected_revision": 1,
                "expected_plan_revision": 1,
                "expected_caption_revision": 1,
            },
        ).json()["error"]["code"]
        == "revision_conflict"
    )
    spec = render.specification(pid, 1, 1, 0, 1, 1)
    assert spec.framing.format == "square" and spec.edit_plan == output.spec.edit_plan
    assert spec.captions.timeline == captions.read(pid).track.timeline
    old = output.spec.model_dump(exclude={"framing"})
    assert render.Spec.model_validate(old).framing == framing.Settings()


def test_real_rotation_and_nonsquare_pixels_before_framing(local):
    client, directory, _ = local
    source = edge_fixture(directory / "edges.mp4")
    anamorphic, rotated = directory / "anamorphic.mp4", directory / "rotated.mp4"
    common = [shutil.which("ffmpeg"), "-v", "error", "-i"]
    subprocess.run(
        common
        + [str(source), "-vf", "setsar=2/1", "-c:v", "libx264", "-c:a", "copy", str(anamorphic)],
        check=True,
        capture_output=True,
        timeout=15,
    )
    help_text = subprocess.run(
        [shutil.which("ffmpeg"), "-h", "full"], capture_output=True, timeout=15
    ).stdout
    rotate_args = (
        [
            shutil.which("ffmpeg"),
            "-v",
            "error",
            "-display_rotation",
            "90",
            "-i",
            str(anamorphic),
            "-c",
            "copy",
        ]
        if b"display_rotation" in help_text
        else common + [str(anamorphic), "-c", "copy", "-metadata:s:v:0", "rotate=90"]
    )
    subprocess.run(
        rotate_args + [str(rotated)],
        check=True,
        capture_output=True,
        timeout=15,
    )
    saved, url, _ = prepared(client, directory, rotated)
    revision = selected(client, url, {"brightness": 0, "contrast": 1, "saturation": 1})
    base = f"/api/projects/{saved['id']}"
    geometry = client.get(base + "/framing/source").json()
    assert (geometry["display_width"], geometry["display_height"]) == (360, 1280)
    assert (
        client.post(
            base + "/framing", json={"expected_revision": 0, "format": "square"}
        ).status_code
        == 200
    )
    response = client.post(
        base + "/render", json={"expected_revision": revision, "expected_framing_revision": 1}
    )
    assert response.status_code == 202, response.text
    result = finish(client, base + "/render")
    assert result["status"] == "ready", result
    output = render.Output.model_validate(result["output"])
    assert (output.width, output.height) == (720, 720)
    raw = frame(render.destination(saved["id"], output.output_id))
    assert max(pixel(raw, 15, 36)) < 5 and max(pixel(raw, 56, 36)) < 5
    # Rotation turns the blue/right and red/left strips into top/bottom edges.
    ends = [pixel(raw, 36, y) for y in (5, 66)]
    assert any(p[0] > 150 for p in ends) and any(p[2] > 150 for p in ends)


def test_geometry_containment_failure_retains_staging_and_quarantines(local, monkeypatch):
    client, directory, _ = local
    saved, _, _ = prepared(client, directory)

    def failed(*args):
        raise engine.ProcessCleanupError

    monkeypatch.setattr(render, "probe", failed)
    response = client.get(f"/api/projects/{saved['id']}/framing/source")
    assert response.status_code == 500 and response.json()["error"]["code"] == "cleanup_failure"
    assert list((directory / "framing-inspection").iterdir())
    with pytest.raises(render.ReferenceError, match="manual review"):
        jobs.check_quarantine()
