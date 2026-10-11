import json
import shutil
import subprocess
import threading
import time

import pytest

import generation
import projects
import reference_engine as engine
import reference_jobs as jobs
import sequence
from references import ReferenceError
from tests.test_color_analysis import generated
from tests.test_video_render import local as local  # noqa: F401
from tests.test_video_render import prepared


def seed(client, directory, source=None):
    path = source or directory / "automatic-input.mp4"
    if source is None:
        generated(path, [(15, 15, 15), (230, 230, 230), (70, 70, 70)], frames_per_color=12)
    saved, _, _ = prepared(client, directory, path)
    pid = saved["id"]
    with projects.database() as db:
        for table in ("clips", "reference_operations"):
            data = json.loads(
                db.execute(f"SELECT metadata FROM {table} WHERE project_id=?", (pid,)).fetchone()[0]
            )
            data.update(duration_seconds=3.0, width=64, height=64)
            if source is not None:
                data.update(has_audio=True, audio_codec="aac")
            db.execute(f"UPDATE {table} SET metadata=? WHERE project_id=?", (json.dumps(data), pid))
    return pid


def wait(client, url, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = client.get(url).json()
        if data["status"] != "running" and jobs.active is None:
            return data
        time.sleep(0.03)
    pytest.fail("Owned generation did not finish")


def test_one_request_generates_sequence_per_slot_transfer_audio_and_output(local, monkeypatch):
    client, directory, _ = local
    if not generation.visual_matching.readiness()["ready"]:
        pytest.skip("Installed visual model required only for this real-inference integration")
    pid = seed(client, directory)
    url = f"/api/projects/{pid}/generation"
    assert client.get(url).json()["status"] == "idle"
    # Seeded legacy recipes require explicit authorization to replace saved work.
    assert client.post(url, json={"captions": "off", "audio": "mute"}).status_code == 409
    start = client.post(url, json={"captions": "off", "audio": "mute", "replace_customized": True})
    assert start.status_code == 202, start.text
    done = wait(client, url)
    assert done["status"] == "ready", done
    saved = sequence.read(pid).sequence
    assert len(saved.slots) == 3 and saved.output_frames == 90
    assert all(slot.clip_id and slot.reference_start_frame is not None for slot in saved.slots)
    output = client.get(f"/api/projects/{pid}/render").json()["output"]
    assert output["duration_seconds"] == pytest.approx(3, abs=1 / 30)
    assert output["has_audio"] is False
    assert output["spec"]["grading"]["settings"]["mode"] == "transfer"
    assert len(output["spec"]["grading"]["settings"]["shot_slots"]) == 3
    assert all(
        output["spec"]["grading"]["settings"]["controls"][f"slot:{slot.id}"]["strength"] == 0.7
        for slot in saved.slots
    )
    assert client.get(url).json() == done
    previous_id = done["output_id"]
    original = generation.prepare_captions

    def fail_caption(*args):
        raise ReferenceError(422, "fixture_caption_failure", "Retry this stage.")

    monkeypatch.setattr(generation, "prepare_captions", fail_caption)
    assert (
        client.post(
            url, json={"expected_revision": 1, "captions": "off", "audio": "mute"}
        ).status_code
        == 202
    )
    failed = wait(client, url)
    assert failed["status"] == "failed" and "grading" in failed["completed"]
    assert client.get(f"/api/projects/{pid}/render").json()["output"]["output_id"] == previous_id
    saved_revision = sequence.read(pid).sequence.revision

    def no_repeat(*args, **kwargs):
        pytest.fail("Retry must reuse the saved sequence and grading")

    monkeypatch.setattr(sequence, "generate", no_repeat)
    monkeypatch.setattr(generation, "prepare_captions", original)
    assert (
        client.post(
            url, json={"expected_revision": 2, "captions": "off", "audio": "mute"}
        ).status_code
        == 202
    )
    resumed = wait(client, url)
    assert resumed["status"] == "ready", resumed
    assert sequence.read(pid).sequence.revision == saved_revision
    assert (
        client.post(
            url, json={"expected_revision": 0, "captions": "off", "audio": "mute"}
        ).status_code
        == 409
    )
    print(
        "AUTOMATIC_OUTPUT",
        done["output_id"],
        saved.output_frames,
        [slot.duration_frames for slot in saved.slots],
    )


def test_snapshot_guards_user_edits_and_changed_source_without_erasing_work(local):
    client, directory, _ = local
    pid = seed(client, directory)
    operation, data = generation.begin_operation(
        pid, generation.Request(captions="off", audio="mute", replace_customized=True)
    )
    generation.guard(
        pid, operation.operation_id, data["snapshot"], threading.Event(), time.monotonic() + 10
    )
    client.post(f"/api/projects/{pid}/audio", json={"expected_revision": 0, "mode": "mute"})
    with pytest.raises(ReferenceError, match="saved edits changed"):
        generation.guard(
            pid, operation.operation_id, data["snapshot"], threading.Event(), time.monotonic() + 10
        )
    assert generation.get_operation(pid).status == "running"
    operation.status = "failed"
    generation.persist(pid, operation, data["snapshot"])
    with pytest.raises(ReferenceError, match="Confirm replacement"):
        generation.begin_operation(
            pid, generation.Request(expected_revision=1, captions="off", audio="mute")
        )


def test_unsafe_custom_caption_staging_is_retained(tmp_path):
    directory = tmp_path / "unsafe"
    with pytest.raises(engine.ProcessCleanupError):
        with generation.automatic_captions.temporary(directory):
            (directory / "owned.txt").write_text("retained")
            raise engine.ProcessCleanupError()
    assert (directory / "owned.txt").exists()


def test_cancelled_or_deleted_generation_cannot_publish(local):
    client, directory, _ = local
    pid = seed(client, directory)
    operation, data = generation.begin_operation(
        pid, generation.Request(captions="off", audio="mute", replace_customized=True)
    )
    stop = threading.Event()
    stop.set()
    with pytest.raises(engine.RetrievalFailure):
        generation.guard(pid, operation.operation_id, data["snapshot"], stop, time.monotonic() + 10)
    with projects.database() as db:
        db.execute("DELETE FROM generation_operations WHERE project_id=?", (pid,))
    with pytest.raises(ReferenceError, match="no longer current"):
        generation.guard(
            pid, operation.operation_id, data["snapshot"], threading.Event(), time.monotonic() + 10
        )
    generation.persist(pid, operation, data["snapshot"])
    assert generation.get_operation(pid).status == "idle"


def test_recovery_preserves_checkpoints_and_requires_explicit_retry(local):
    client, directory, _ = local
    pid = seed(client, directory)
    operation, data = generation.begin_operation(
        pid, generation.Request(captions="off", audio="mute", replace_customized=True)
    )
    operation.completed = ["slots", "sequence"]
    generation.persist(pid, operation, data["snapshot"])
    generation.recover()
    recovered = generation.get_operation(pid)
    assert recovered.status == "failed" and recovered.failure_code == "interrupted"
    retried, _ = generation.begin_operation(
        pid, generation.Request(expected_revision=1, captions="off", audio="mute")
    )
    assert retried.completed == ["slots", "sequence"]


def test_missing_setup_fails_before_retrieval_without_network_or_hidden_download(
    local, monkeypatch
):
    client, directory, _ = local
    pid = seed(client, directory)
    monkeypatch.setattr(generation.visual_matching, "readiness", lambda: {"ready": False})

    def forbidden(*args, **kwargs):
        pytest.fail("Capability preflight must precede media processing")

    monkeypatch.setattr(jobs, "pipeline", forbidden)
    url = f"/api/projects/{pid}/generation"
    response = client.post(
        url, json={"captions": "off", "audio": "mute", "replace_customized": True}
    )
    assert response.status_code == 202
    done = wait(client, url)
    assert done["status"] == "failed" and done["failure_code"] == "visual_setup"
    assert not done["completed"]


def test_caption_my_audio_uses_actual_assembled_audio_without_publishing_scratch(
    local, monkeypatch
):
    if not generation.visual_matching.readiness()["ready"]:
        pytest.skip("Installed visual model needed for generated assembled-audio integration")
    import numpy as np

    import captions

    client, directory, _ = local
    silent = directory / "silent-input.mp4"
    generated(silent, [(15, 15, 15), (230, 230, 230), (70, 70, 70)])
    tone = directory / "tone-input.mp4"
    subprocess.run(
        [
            shutil.which("ffmpeg"),
            "-v",
            "error",
            "-i",
            str(silent),
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=660:sample_rate=48000:duration=3",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-t",
            "3",
            str(tone),
        ],
        check=True,
        capture_output=True,
        timeout=15,
    )
    pid = seed(client, directory, tone)
    monkeypatch.setattr(generation, "capability", lambda *args: None)
    monkeypatch.setattr(generation.automatic_captions, "detect", lambda *args: ([], [], {}))
    calls = []

    def speech(path, duration, language, stage, stop, deadline):
        assert "assembled-audio-source" in str(path) and path.exists()
        assert duration == pytest.approx(3, abs=1 / 30)
        raw = subprocess.run(
            [
                shutil.which("ffmpeg"),
                "-v",
                "error",
                "-i",
                str(path),
                "-map",
                "0:a:0",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-f",
                "s16le",
                "-",
            ],
            capture_output=True,
            check=True,
            timeout=10,
        ).stdout
        samples = np.frombuffer(raw, dtype="<i2").astype(float)
        frequencies = np.fft.rfftfreq(len(samples), 1 / 16000)
        peak = frequencies[np.abs(np.fft.rfft(samples)).argmax()]
        assert peak == pytest.approx(660, abs=2)
        calls.append((duration, peak))
        assert generation.render.get_operation(pid).output is None
        return ([{"start": 0.5, "end": 1.5, "text": "Assembled speech"}], "en", {})

    monkeypatch.setattr(generation.transcribe_local, "run", speech)
    url = f"/api/projects/{pid}/generation"
    assert (
        client.post(
            url, json={"audio": "original", "captions": "output_audio", "replace_customized": True}
        ).status_code
        == 202
    )
    done = wait(client, url)
    assert done["status"] == "ready", done
    track = captions.read(pid).track
    assert track.provenance == "generated_output_audio" and track.generated_audio_sha256
    assert track.automatic_binding.timeline.mode == "sequence"
    assert track.cues[0].start == 0.5 and track.cues[0].text == "Assembled speech"
    assert len(calls) == 1
    assert not generation.staging(done["operation_id"]).exists()


@pytest.mark.parametrize("safe", [True, False])
def test_nested_failure_preserves_confirmed_cleanup_state_for_retry(local, safe):
    client, directory, _ = local
    pid = seed(client, directory)
    import color_analysis as color

    with projects.database() as db:
        row = db.execute(
            "SELECT operation_id FROM color_operations WHERE project_id=?", (pid,)
        ).fetchone()
    color.fail_operation(
        pid,
        row[0],
        engine.RetrievalFailure("cleanup_failure", "Fixture cleanup failure", cleanup_safe=safe),
    )
    failure = generation.stage_failure(pid, color, color.get_operation(pid))
    assert failure.code == "cleanup_failure" and failure.cleanup_safe is safe
