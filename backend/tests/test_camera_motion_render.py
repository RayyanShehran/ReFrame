import shutil
import subprocess

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
