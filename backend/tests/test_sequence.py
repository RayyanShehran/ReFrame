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
    caption = client.post(
        f"/api/projects/{pid}/captions",
        json={
            "expected_revision": 0,
            "mode": "sequence",
            "expected_sequence_revision": 2,
            "enabled": True,
            "cues": [{"start": 1.1, "end": 1.9, "text": "Assembled caption"}],
        },
    )
    assert caption.status_code == 200, caption.text
    revision = selected(client, recipe_url, {"brightness": 0, "contrast": 1, "saturation": 1})
    response = client.post(
        f"/api/projects/{pid}/render",
        json={
            "expected_revision": revision,
            "expected_sequence_revision": 2,
            "expected_caption_revision": 1,
        },
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
    full = subprocess.run(
        [
            shutil.which("ffmpeg"),
            "-v",
            "error",
            "-i",
            str(path),
            "-vf",
            "fps=1",
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
    frame_size = output.width * output.height * 3
    white = []
    for frame in (full[i : i + frame_size] for i in range(0, len(full), frame_size)):
        white.append(sum(min(frame[i : i + 3]) > 160 for i in range(0, len(frame), 3)))
    assert white[0] == 0 and white[1] > 20 and white[2] == 0

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


def seed_slot_colors(pid, slot_ids, primary_id, revision=3):
    """Saved colour matches and settings keyed by the given slot ids, plus the original clip."""
    import color_transfer as transfer
    import grading

    controls = {f"slot:{id}": transfer.Controls(strength=0.7) for id in slot_ids}
    controls[f"clip:{primary_id}"] = transfer.Controls(strength=0.6)
    settings = grading.Settings(
        mode="transfer", revision=revision, controls=controls, shot_slots=slot_ids, updated_at="x"
    )
    with projects.database() as db:
        for key in [f"slot:{id}" for id in slot_ids] + [f"clip:{primary_id}"]:
            db.execute("INSERT INTO color_matches VALUES(?,?,?)", (pid, key, "{}"))
        db.execute(
            "INSERT INTO grading_settings VALUES(?,?,?) ON CONFLICT(project_id) "
            "DO UPDATE SET revision=excluded.revision,settings=excluded.settings",
            (pid, revision, settings.model_dump_json()),
        )


def slot_colors(pid):
    with projects.database() as db:
        keys = sorted(
            k for (k,) in db.execute("SELECT key FROM color_matches WHERE project_id=?", (pid,))
        )
        row = db.execute(
            "SELECT revision, settings FROM grading_settings WHERE project_id=?", (pid,)
        ).fetchone()
    return keys, row[0], json.loads(row[1])


def test_sequence_writes_prune_colour_matches_for_removed_slots(local):
    client, directory, _ = local
    pid, _, url, saved = setup(client, directory)
    primary = clip_library.read(pid).clips[0]
    old = [s["id"] for s in saved["slots"]]
    seed_slot_colors(pid, old, primary.id)

    # Saving without slot 3 drops only its match, control and shot-slot entry.
    slots = [
        {"id": s["id"], "clip_id": str(primary.id), "duration_frames": 30, "source_start_frame": 0}
        for s in saved["slots"][:2]
    ]
    assert client.post(url, json={"expected_revision": 1, "slots": slots}).status_code == 200
    keys, revision, settings = slot_colors(pid)
    assert keys == sorted([f"clip:{primary.id}", f"slot:{old[0]}", f"slot:{old[1]}"])
    assert revision == settings["revision"] == 4
    assert set(settings["controls"]) == {f"clip:{primary.id}", f"slot:{old[0]}", f"slot:{old[1]}"}
    assert settings["shot_slots"] == old[:2] and settings["mode"] == "transfer"
    assert settings["controls"][f"clip:{primary.id}"]["strength"] == 0.6

    # A save that removes nothing leaves the colour settings revision alone.
    assert client.post(url, json={"expected_revision": 2, "slots": slots[::-1]}).status_code == 200
    assert slot_colors(pid)[1] == 4

    # Regenerating replaces every slot id, so every slot match goes; clip matches stay.
    regenerated = client.post(url + "/generate", json={"expected_revision": 3, "replace": True})
    assert regenerated.status_code == 200, regenerated.text
    keys, revision, settings = slot_colors(pid)
    assert keys == [f"clip:{primary.id}"]
    assert revision == 5 and settings["shot_slots"] == []
    assert set(settings["controls"]) == {f"clip:{primary.id}"}
    import grading

    restored = grading.read_settings(pid)  # the rewritten settings still validate
    assert restored.revision == 5 and restored.mode == "transfer" and restored.shot_slots == []


def test_startup_cleanup_prunes_colour_matches_left_by_earlier_regenerations(local):
    import sequence

    client, directory, _ = local
    pid, _, _, saved = setup(client, directory)
    primary = clip_library.read(pid).clips[0]
    current = [s["id"] for s in saved["slots"]]
    stale = ["11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"]
    seed_slot_colors(pid, current + stale, primary.id)
    sequence.prune_stale_slot_colors()
    keys, revision, settings = slot_colors(pid)
    assert keys == sorted([f"clip:{primary.id}"] + [f"slot:{id}" for id in current])
    assert revision == 4 and settings["shot_slots"] == current
    sequence.prune_stale_slot_colors()
    assert slot_colors(pid)[1] == 4
