import shutil
import subprocess
import types

import pytest

import camera_motion as motion
import clip_library
import video_render as render
from tests.test_color_analysis import finish
from tests.test_sequence import setup
from tests.test_video_render import local as local  # noqa: F401
from tests.test_video_render import selected

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required")


def zoom_shot(start, end):
    return motion.ShotMotion(
        start_frame=start,
        end_frame=end,
        keyframes=[
            motion.Keyframe(frame=start, scale=1.0, x=0.0, y=0.0, angle=0.0),
            motion.Keyframe(frame=end - 1, scale=1.2, x=0.03, y=0.0, angle=0.0),
        ],
        moves=["zoom_in", "pan"],
    )


def test_a_slot_across_a_reference_cut_continues_into_the_next_shots_motion():
    static = motion.ShotMotion(
        start_frame=0,
        end_frame=30,
        keyframes=[motion.Keyframe(frame=0, scale=1.0, x=0.0, y=0.0, angle=0.0)],
    )
    settings = motion.Settings(shots=[static, zoom_shot(30, 60)])

    def slot(start, frames):
        return types.SimpleNamespace(reference_start_frame=start, duration_frames=frames)

    samples = motion.slot_samples(settings, slot(20, 30))
    assert len(samples) == 30
    assert samples[:11] == [(1.0, 0.0, 0.0, 0.0)] * 11  # Static until the cut, no jump at it.
    assert samples[-1] == pytest.approx(motion.sample(settings.shots[1], 49))
    # A slot that only covers static shots gets no motion at all.
    assert motion.slot_samples(settings, slot(0, 30)) is None


def test_saved_motion_is_bound_applied_without_black_edges_and_toggled(local):
    client, directory, _ = local
    pid, recipe_url, url, saved = setup(client, directory)
    primary = clip_library.read(pid).clips[0]
    slots = [
        {"id": s["id"], "clip_id": str(primary.id), "duration_frames": 30, "source_start_frame": f}
        for s, f in zip(saved["slots"], (0, 30, 60))
    ]
    assert client.post(url, json={"expected_revision": 1, "slots": slots}).status_code == 200
    endpoint = f"/api/projects/{pid}/camera-motion"
    default = client.get(endpoint).json()
    assert default["status"] == "default"
    assert (default["settings"]["revision"], default["settings"]["enabled"]) == (0, True)
    measurement = motion.Measurement(
        width=192,
        height=108,
        shots=[zoom_shot(0, 30), zoom_shot(30, 60), zoom_shot(60, 90)],
        timings=motion.Timings(decode_seconds=0.1, estimate_seconds=0.1, frames=90),
    )
    stored = motion.save_analysis(pid, measurement, expected_revision=0)
    assert (stored.settings.revision, stored.settings.enabled, stored.status) == (1, True, "ready")
    revision = selected(client, recipe_url, {"brightness": 0, "contrast": 1, "saturation": 1})
    request = {"expected_revision": revision, "expected_sequence_revision": 2}
    # Omitting the saved motion revision is accepted only at the unchanged default.
    assert client.post(f"/api/projects/{pid}/render", json=request).status_code == 409
    request["expected_motion_revision"] = 1
    response = client.post(f"/api/projects/{pid}/render", json=request)
    assert response.status_code == 202, response.text
    operation = finish(client, f"/api/projects/{pid}/render")
    assert operation["status"] == "ready", operation
    output = render.Output.model_validate(operation["output"])
    assert output.spec.camera_motion.revision == 1
    assert output.spec.renderer_version == motion.RENDERER_VERSION
    path = render.destination(pid, output.output_id)
    raw = subprocess.run(
        [shutil.which("ffmpeg"), "-v", "error", "-i", str(path), "-f", "rawvideo"]
        + ["-pix_fmt", "gray", "-"],
        capture_output=True,
        check=True,
    ).stdout
    width, height = output.width, output.height
    assert len(raw) == output.decoded_frames * width * height
    for index in range(output.decoded_frames):
        frame = raw[index * width * height : (index + 1) * width * height]
        rows = [frame[r * width : (r + 1) * width] for r in (0, height - 1)]
        columns = [bytes(frame[r * width + c] for r in range(height)) for c in (0, width - 1)]
        assert min(min(edge) for edge in rows + columns) >= 40, f"black edge at frame {index}"
    # Turning motion off is a saved revision change: the old output is outdated.
    off = client.post(endpoint, json={"expected_revision": 1, "enabled": False})
    assert off.status_code == 200 and off.json()["settings"]["revision"] == 2
    assert client.post(endpoint, json={"expected_revision": 1, "enabled": True}).status_code == 409
    assert client.get(f"/api/projects/{pid}/render").json()["outdated"]
    # A fresh analysis keeps the user's choice.
    again = motion.save_analysis(pid, measurement, expected_revision=2)
    assert (again.settings.revision, again.settings.enabled) == (3, False)


def test_a_rolled_slot_in_fit_keeps_the_bars_still_and_black(local):
    client, directory, _ = local
    pid, recipe_url, url, saved = setup(client, directory)
    primary = clip_library.read(pid).clips[0]
    slots = [
        {"id": s["id"], "clip_id": str(primary.id), "duration_frames": 30, "source_start_frame": f}
        for s, f in zip(saved["slots"], (0, 30, 60))
    ]
    assert client.post(url, json={"expected_revision": 1, "slots": slots}).status_code == 200
    framing = {"expected_revision": 0, "format": "portrait", "fit": "fit"}
    assert client.post(f"/api/projects/{pid}/framing", json=framing).status_code == 200
    turn = 0.06  # About 3.4 degrees, well above the noise floor.
    rolled = [
        motion.ShotMotion(
            start_frame=start,
            end_frame=start + 30,
            keyframes=[
                motion.Keyframe(frame=start, scale=1.0, x=0.0, y=0.0, angle=0.0),
                motion.Keyframe(frame=start + 29, scale=1.0, x=0.0, y=0.0, angle=turn),
            ],
            moves=["rotate"],
        )
        for start in (0, 30, 60)
    ]
    measurement = motion.Measurement(
        width=192,
        height=108,
        shots=rolled,
        timings=motion.Timings(decode_seconds=0.1, estimate_seconds=0.1, frames=90),
    )
    stored = motion.save_analysis(pid, measurement, expected_revision=0)
    assert stored.settings.schema_version == 2
    revision = selected(client, recipe_url, {"brightness": 0, "contrast": 1, "saturation": 1})
    request = {
        "expected_revision": revision,
        "expected_sequence_revision": 2,
        "expected_framing_revision": 1,
        "expected_motion_revision": 1,
    }
    response = client.post(f"/api/projects/{pid}/render", json=request)
    assert response.status_code == 202, response.text
    operation = finish(client, f"/api/projects/{pid}/render")
    assert operation["status"] == "ready", operation
    output = render.Output.model_validate(operation["output"])
    assert output.spec.renderer_version == motion.ROTATION_RENDERER_VERSION
    assert (output.width, output.height) == (720, 1280)
    path = render.destination(pid, output.output_id)
    raw = subprocess.run(
        [shutil.which("ffmpeg"), "-v", "error", "-i", str(path), "-f", "rawvideo"]
        + ["-pix_fmt", "gray", "-"],
        capture_output=True,
        check=True,
    ).stdout
    width, height = output.width, output.height
    # The clip fitted to the canvas width, centred between the bars.
    source = client.get(f"/api/projects/{pid}/framing/source").json()
    image = round(width * source["display_height"] / source["display_width"])
    top = (height - image) // 2
    for index in range(output.decoded_frames):
        frame = raw[index * width * height : (index + 1) * width * height]
        bars = frame[: (top - 4) * width] + frame[(top + image + 4) * width :]
        assert max(bars) < 24, f"bars not black at frame {index}"
        inside = [frame[r * width : (r + 1) * width] for r in (top + 4, top + image - 5)]
        columns = [
            bytes(frame[r * width + c] for r in range(top + 4, top + image - 4))
            for c in (2, width - 3)
        ]
        assert min(min(edge) for edge in inside + columns) >= 40, f"black corner at frame {index}"
