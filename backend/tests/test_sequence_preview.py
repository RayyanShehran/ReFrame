import base64
import json

import pytest

import clip_library
import clips
import projects
import video_render as render
from tests.test_color_analysis import finish
from tests.test_edit_plan import seed_pacing
from tests.test_framing_render import frame
from tests.test_projects import REAL_PROBE
from tests.test_sequence import make_fixtures
from tests.test_video_render import local as local  # noqa: F401
from tests.test_video_render import prepared


def test_real_sequence_preview_crosses_boundary_and_preserves_animation(local, monkeypatch):
    client, directory, _ = local
    a, b = make_fixtures(directory)
    saved, _, _ = prepared(client, directory, a)
    pid = saved["id"]
    base = f"/api/projects/{pid}"
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
    monkeypatch.setattr(clips, "probe_file", REAL_PROBE)
    assert (
        client.post(
            base + "/clips", files={"file": ("b.mp4", b.read_bytes(), "video/mp4")}
        ).status_code
        == 200
    )
    first, second = clip_library.read(pid).clips
    seed_pacing(pid, (1, 1, 1), 4)
    seq = client.post(base + "/sequence/generate", json={"expected_revision": 0}).json()["sequence"]
    slots = [
        {"id": s["id"], "clip_id": str(c.id), "duration_frames": 30, "source_start_frame": start}
        for s, c, start in zip(seq["slots"], (first, second, first), (60, 30, 0))
    ]
    assert (
        client.post(base + "/sequence", json={"expected_revision": 1, "slots": slots}).status_code
        == 200
    )
    caption = client.post(
        base + "/captions",
        json={
            "expected_revision": 0,
            "mode": "sequence",
            "expected_sequence_revision": 2,
            "enabled": True,
            "cues": [{"start": 0.7, "end": 1.5, "text": "Boundary"}],
            "style": {"placement": "center", "vertical": 0.5, "size_percent": 10},
            "animation": {"mode": "pop", "entrance_seconds": 0.4, "initial_scale": 0.5},
        },
    )
    assert caption.status_code == 200, caption.text
    request = {
        "cue_index": 0,
        "expected_recipe_revision": 1,
        "expected_framing_revision": 0,
        "expected_caption_revision": 1,
        "expected_sequence_revision": 2,
    }
    still = client.post(base + "/caption-preview", json=request)
    assert still.status_code == 200, still.text
    value = still.json()
    assert value["sequence_revision"] == 2 and value["source_clip_id"] == str(second.id)
    assert value["source_timestamp_seconds"] == pytest.approx(1.1)
    response = client.post(base + "/caption-motion-preview", json=request)
    assert response.status_code == 200, response.text
    motion = response.json()
    assert (motion["start_frame"], motion["end_frame"], motion["decoded_frames"]) == (15, 51, 36)
    path = directory / "sequence-preview.mp4"
    path.write_bytes(base64.b64decode(motion["video_base64"]))
    # Actual preview is green before the boundary, blue afterwards, red absent.
    before = frame(path, second=0.2, width=320, height=180)
    after = frame(path, second=0.8, width=320, height=180)
    assert before[1] > 180 and before[0] < 30
    center = (180 // 2 * 320 + 320 // 2) * 3
    assert after[center + 2] > 150
    assert (
        client.post(
            base + "/render",
            json={
                "expected_revision": 1,
                "expected_sequence_revision": 2,
                "expected_caption_revision": 1,
            },
        ).status_code
        == 202
    )
    output = finish(client, base + "/render")
    assert output["status"] == "ready", output
    full = render.destination(pid, output["output"]["output_id"])
    for t in (0.3, 0.5, 0.8):
        preview_frame = frame(path, second=t, width=320, height=180)
        full_frame = frame(full, second=t + 0.5, width=320, height=180)
        # Separate lossy encode, same animation phase/geometry on the output timeline.
        assert sum(abs(a - b) for a, b in zip(preview_frame, full_frame)) / len(full_frame) < 4
    assert (
        client.post(
            base + "/caption-motion-preview", json=request | {"expected_sequence_revision": 1}
        ).status_code
        == 409
    )
    assert not list((projects.DATA_DIR / "preview-staging").iterdir())
