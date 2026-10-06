import json
import math
import shutil
import struct
import subprocess

import pytest

import clip_library
import projects
import video_render as render
from tests.test_color_analysis import finish
from tests.test_edit_plan import seed_pacing
from tests.test_video_render import local as local  # noqa: F401
from tests.test_video_render import pixels, prepared, selected


def setup(client, directory):
    saved, url, _ = prepared(client, directory)
    seed_pacing(saved["id"], (1, 1, 1), 3)
    pid = saved["id"]
    endpoint = f"/api/projects/{pid}/sequence"
    result = client.post(endpoint + "/generate", json={"expected_revision": 0})
    assert result.status_code == 200, result.text
    return pid, url, endpoint, result.json()["sequence"]


def test_sequence_ranges_revisions_sources_and_caption_binding(local):
    client, directory, _ = local
    pid, _, url, saved = setup(client, directory)
    primary = clip_library.read(pid).clips[0]
    assert len(saved["slots"]) == 3
    assert client.post(url + "/generate", json={"expected_revision": 1}).status_code == 409
    slots = [
        {
            "id": s["id"],
            "clip_id": str(primary.id),
            "duration_frames": 30,
            "source_start_frame": start,
        }
        for s, start in zip(saved["slots"], (60, 0, 30))
    ]
    assert (
        client.post(url, json={"expected_revision": 1, "slots": slots}).json()["status"] == "ready"
    )
    assert client.post(url, json={"expected_revision": 1, "slots": slots}).status_code == 409
    assert (
        client.post(
            f"/api/projects/{pid}/render",
            json={"expected_revision": 1, "expected_sequence_revision": 1},
        ).status_code
        == 409
    )
    invalid = [slots[0] | {"source_start_frame": 61}, *slots[1:]]
    assert client.post(url, json={"expected_revision": 2, "slots": invalid}).status_code == 422
    invalid = [slots[0], slots[0], slots[2]]
    assert client.post(url, json={"expected_revision": 2, "slots": invalid}).status_code == 422
    caption_url = f"/api/projects/{pid}/captions"
    caption = {
        "expected_revision": 0,
        "mode": "sequence",
        "expected_sequence_revision": 2,
        "enabled": True,
        "cues": [{"start": 0, "end": 1, "text": "Keep my text"}],
    }
    response = client.post(caption_url, json=caption)
    assert response.status_code == 200, response.text
    assert client.post(url, json={"expected_revision": 2, "slots": slots[::-1]}).status_code == 200
    stale = client.get(caption_url).json()
    assert stale["status"] == "stale" and stale["track"]["cues"][0]["text"] == "Keep my text"
    assert client.delete(f"/api/projects/{pid}/clips/{primary.id}").status_code == 409
    path, _ = clip_library.source(pid, primary.id)
    path.write_bytes(b"changed")
    assert client.get(url).json()["status"] == "stale"


def make_fixtures(directory):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg required")
    a, b = directory / "camera-a.mp4", directory / "camera-b.mp4"
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=red:s=320x180:r=30:d=2",
            "-f",
            "lavfi",
            "-i",
            "color=lime:s=320x180:r=30:d=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=4",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map",
            "[v]",
            "-map",
            "2:a",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(a),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=blue:s=180x320:r=30:d=3",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(b),
        ],
        check=True,
        capture_output=True,
    )
    return a, b


def test_real_multisource_order_reuse_silence_and_outdated(local):
    client, directory, _ = local
    a, b = make_fixtures(directory)
    saved, recipe_url, _ = prepared(client, directory, a)
    pid = saved["id"]
    # Fixture seed carries old metadata; use the actual validated source metadata.
    with projects.database() as db:
        row = db.execute("SELECT metadata FROM clips WHERE project_id=?", (pid,)).fetchone()
        metadata = json.loads(row[0]) | {
            "width": 320,
            "height": 180,
            "duration_seconds": 4,
            "has_audio": True,
            "audio_codec": "aac",
        }
        db.execute("UPDATE clips SET metadata=? WHERE project_id=?", (json.dumps(metadata), pid))
    # Exercise the real parser and actual inspection for the second source.
    import clips
    from tests.test_projects import REAL_PROBE

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(clips, "probe_file", REAL_PROBE)
        response = client.post(
            f"/api/projects/{pid}/clips",
            files={"file": ("camera-b.mp4", b.read_bytes(), "video/mp4")},
        )
    assert response.status_code == 200, response.text
    first, second = clip_library.read(pid).clips
    seed_pacing(pid, (1, 1, 1), 4)
    url = f"/api/projects/{pid}/sequence"
    saved_sequence = client.post(url + "/generate", json={"expected_revision": 0}).json()[
        "sequence"
    ]
    slots = [
        {"id": s["id"], "clip_id": str(c.id), "duration_frames": 30, "source_start_frame": start}
        for s, c, start in zip(saved_sequence["slots"], (first, second, first), (60, 30, 0))
    ]
    assert client.post(url, json={"expected_revision": 1, "slots": slots}).status_code == 200
    revision = selected(client, recipe_url, {"brightness": 0, "contrast": 1, "saturation": 1})
    response = client.post(
        f"/api/projects/{pid}/render",
        json={"expected_revision": revision, "expected_sequence_revision": 2},
    )
    assert response.status_code == 202, response.text
    operation = finish(client, f"/api/projects/{pid}/render")
    assert operation["status"] == "ready", operation
    output = render.Output.model_validate(operation["output"])
    assert (output.width, output.height, output.decoded_frames) == (320, 180, 90)
    assert output.duration_seconds == pytest.approx(3, abs=1 / 30)
    path = render.destination(pid, output.output_id)
    raw = pixels(path)
    frames = [raw[i : i + 768] for i in range(0, len(raw), 768)]
    channels = [tuple(sum(frame[c::3]) / 256 for c in range(3)) for frame in frames]
    assert channels[0][1] > 200 and channels[2][0] > 200
    assert channels[1][2] > max(channels[1][:2]) + 70
    pcm = subprocess.run(
        [
            shutil.which("ffmpeg"),
            "-v",
            "error",
            "-i",
            str(path),
            "-ac",
            "1",
            "-ar",
            "48000",
            "-f",
            "s16le",
            "-",
        ],
        check=True,
        capture_output=True,
    ).stdout
    samples = struct.unpack(f"<{len(pcm) // 2}h", pcm)

    def rms(start, end):
        values = samples[round(start * 48000) : round(end * 48000)]
        return math.sqrt(sum(v * v for v in values) / len(values))

    assert rms(0.2, 0.8) > 1000 and rms(2.2, 2.8) > 1000
    assert rms(1.2, 1.8) < 10
    print(
        f"M28 export: {output.width}x{output.height}, {output.decoded_frames} frames, "
        f"{output.duration_seconds}s; RGB={channels}; "
        f"RMS={rms(0.2, 0.8):.1f}/{rms(1.2, 1.8):.1f}/{rms(2.2, 2.8):.1f}"
    )
    assert client.post(url, json={"expected_revision": 2, "slots": slots[::-1]}).status_code == 200
    assert client.get(f"/api/projects/{pid}/render").json()["outdated"]
    import threading
    import time

    with pytest.raises(render.engine.RetrievalFailure, match="Sequence changed"):
        render.commit(
            pid, str(output.output_id), path, output, threading.Event(), time.monotonic() + 30
        )
    assert path.is_file()
    assert client.get(f"/api/projects/{pid}/outputs/{output.output_id}/video").status_code == 200
