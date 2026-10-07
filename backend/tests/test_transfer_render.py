"""One reusable real fixture: moving texture, two balances, saved slot match and captions."""

import base64
import json
import shutil
import subprocess

import pytest

import clip_library
import clips
import color_transfer
import grading
import projects
import reference_jobs
import video_render as render
from tests.test_color_analysis import finish
from tests.test_edit_plan import seed_pacing
from tests.test_projects import REAL_PROBE
from tests.test_video_render import local as local  # noqa: F401
from tests.test_video_render import prepared


def fixtures(directory):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg required")
    original = directory / "texture.mp4"
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=320x180:r=30:d=3",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=3",
            "-c:v",
            "libx264",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(original),
        ],
        check=True,
        capture_output=True,
    )
    paths = []
    for name, filters in [
        ("dark", "eq=brightness=-0.08:contrast=0.9,colorbalance=rs=-0.04:bs=0.08"),
        ("warm", "colorbalance=rs=0.08:bs=-0.06:rh=0.06:bh=-0.04"),
    ]:
        path = directory / f"{name}.mp4"
        subprocess.run(
            [
                ffmpeg,
                "-v",
                "error",
                "-i",
                str(original),
                "-vf",
                filters,
                "-c:v",
                "libx264",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "copy",
                str(path),
            ],
            check=True,
            capture_output=True,
        )
        paths.append(path)
    return original, *paths


def decoded(path, second=0.5):
    return subprocess.run(
        [
            shutil.which("ffmpeg"),
            "-v",
            "error",
            "-ss",
            str(second),
            "-i",
            str(path),
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
    ).stdout


def png_pixels(data, directory):
    path = directory / "compare.png"
    path.write_bytes(base64.b64decode(data))
    return decoded(path, 0)


def test_real_transfer_perclip_slot_preview_export_and_timing(local, monkeypatch):
    client, directory, _ = local
    a, b, reference = fixtures(directory)
    saved, _, _ = prepared(client, directory, a)
    pid = saved["id"]
    base = f"/api/projects/{pid}"
    with projects.database() as db:
        row = db.execute("SELECT metadata FROM clips WHERE project_id=?", (pid,)).fetchone()
        metadata = json.loads(row[0]) | dict(
            width=320, height=180, duration_seconds=3, has_audio=True, audio_codec="aac"
        )
        db.execute("UPDATE clips SET metadata=? WHERE project_id=?", (json.dumps(metadata), pid))
        row = db.execute("SELECT * FROM reference_operations WHERE project_id=?", (pid,)).fetchone()
        path = reference_jobs.destination(pid, row["operation_id"])
        shutil.copyfile(reference, path)
        digest = render.color.digest(path)
        metadata = json.loads(row["metadata"]) | dict(
            size_bytes=path.stat().st_size, sha256=digest, duration_seconds=3
        )
        db.execute(
            "UPDATE reference_operations SET metadata=? WHERE project_id=?",
            (json.dumps(metadata), pid),
        )
        row = db.execute(
            "SELECT blueprint FROM color_operations WHERE project_id=?", (pid,)
        ).fetchone()
        blueprint = json.loads(row[0])
        blueprint["source"]["media_sha256"] = digest
        db.execute(
            "UPDATE color_operations SET blueprint=?,source_hash=? WHERE project_id=?",
            (json.dumps(blueprint), digest, pid),
        )
    monkeypatch.setattr(clips, "probe_file", REAL_PROBE)
    assert (
        client.post(
            base + "/clips", files={"file": ("dark.mp4", b.read_bytes(), "video/mp4")}
        ).status_code
        == 200
    )
    first, second = clip_library.read(pid).clips
    seed_pacing(pid, (1, 1, 1), 3)
    seq = client.post(base + "/sequence/generate", json={"expected_revision": 0}).json()["sequence"]
    slots = [
        dict(id=s["id"], clip_id=str(c.id), duration_frames=30, source_start_frame=start)
        for s, c, start in zip(seq["slots"], (first, second, first), (0, 30, 60))
    ]
    assert (
        client.post(base + "/sequence", json={"expected_revision": 1, "slots": slots}).status_code
        == 200
    )
    # Prepare the project look plus the second slot's independently bound reference interval.
    request = dict(expected_revision=0, shot_slots=[slots[1]["id"]], expected_sequence_revision=2)
    response = client.post(base + "/grading/prepare", json=request)
    assert response.status_code == 202, response.text
    matched = finish(client, base + "/grading")
    assert matched["status"] == "ready", matched
    assert len(matched["entries"]) == 3 and all(e["valid"] for e in matched["entries"])
    models = {e["match"]["key"]: e["match"]["model"] for e in matched["entries"]}
    assert models[f"clip:{first.id}"] != models[f"clip:{second.id}"]
    body = dict(
        expected_revision=0,
        mode="transfer",
        shot_slots=[slots[1]["id"]],
        controls={key: dict(strength=1) for key in models},
    )
    assert client.post(base + "/grading", json=body).status_code == 200
    spec = render.specification(pid, 0, expected_sequence_revision=2, expected_grading_revision=1)
    assert (
        grading.routed(spec.grading, second.id, spec.sequence.slots[1])[0].key
        == f"slot:{slots[1]['id']}"
    )
    assert (
        grading.routed(spec.grading, first.id, spec.sequence.slots[0])[0]
        == grading.routed(spec.grading, first.id, spec.sequence.slots[2])[0]
    )
    preview_request = dict(
        expected_recipe_revision=0,
        expected_framing_revision=0,
        expected_grading_revision=1,
        timestamp_seconds=0.5,
        clip_id=str(first.id),
    )
    response = client.post(base + "/frame-preview", json=preview_request)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["reference"]
    before = png_pixels(preview["original"]["png_base64"], directory)
    after = png_pixels(preview["edited"]["png_base64"], directory)
    assert sum(abs(a - b) for a, b in zip(before, after)) / len(after) > 2
    render_request = dict(expected_revision=0, expected_grading_revision=1)
    response = client.post(base + "/render", json=render_request)
    assert response.status_code == 202, response.text
    output = finish(client, base + "/render")
    assert output["status"] == "ready", output
    video = render.destination(pid, output["output"]["output_id"])
    pixels = decoded(video)
    error = sum(abs(a - b) for a, b in zip(pixels, after)) / len(after)
    assert error < 5  # AAC/H.264 output, exact same color filter, allowed encode quantization.
    cut_plan = client.post(
        base + "/edit-plan/generate", json={"expected_revision": 0, "output_duration_seconds": 2.0}
    )
    assert cut_plan.status_code == 200, cut_plan.text
    response = client.post(base + "/render", json=render_request | {"expected_plan_revision": 1})
    assert response.status_code == 202, response.text
    cuts = finish(client, base + "/render")
    assert cuts["status"] == "ready" and cuts["output"]["decoded_frames"] == 60, cuts
    # Strength zero selects the exact ungraded filter path, including no LUT color conversion.
    bound = spec.grading.model_copy(
        update={
            "settings": spec.grading.settings.model_copy(
                update={"controls": {f"clip:{first.id}": color_transfer.Controls(strength=0)}}
            )
        }
    )
    assert grading.filter_for(bound, first.id, directory) == "null"
    caption = client.post(
        base + "/captions",
        json=dict(
            expected_revision=0,
            mode="sequence",
            expected_sequence_revision=2,
            enabled=True,
            cues=[dict(start=1.1, end=1.9, text="Saved transfer")],
        ),
    )
    assert caption.status_code == 200, caption.text
    response = client.post(
        base + "/render",
        json=render_request | dict(expected_sequence_revision=2, expected_caption_revision=1),
    )
    assert response.status_code == 202, response.text
    output = finish(client, base + "/render")
    assert output["status"] == "ready", output
    assert output["output"]["decoded_frames"] == 90 and output["output"]["has_audio"]
    assert output["output"]["duration_seconds"] == pytest.approx(3, abs=1 / 30)
    assert output["output"]["spec"]["grading"]["settings"]["mode"] == "transfer"
    # A range change invalidates its shot match, preserving project clip matches.
    slots[1]["source_start_frame"] = 0
    assert (
        client.post(base + "/sequence", json=dict(expected_revision=2, slots=slots)).status_code
        == 200
    )
    matches = client.get(base + "/grading").json()["entries"]
    assert sum(e["valid"] for e in matches) == 2
    assert client.get(base + "/render").json()["outdated"]
    assert (
        client.post(
            base + "/render",
            json=render_request | dict(expected_sequence_revision=3, expected_caption_revision=1),
        ).status_code
        == 409
    )
    (directory / "transfer-evidence.json").write_text(
        json.dumps(
            dict(preview_encode_mae=error, models=models, output=output["output"]), indent=2
        ),
        encoding="utf-8",
    )
