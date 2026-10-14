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


def test_long_valid_footage_adapts_under_budget_and_reaches_real_export(local, monkeypatch):
    import clips
    import visual_matching as visual

    client, directory, _ = local
    pid = seed(client, directory)
    # Three 120-second independent footage files plus the 3-second reference exceed 512.
    monkeypatch.setattr(
        clips, "probe_file", __import__("tests.test_projects", fromlist=["REAL_PROBE"]).REAL_PROBE
    )
    ids = []
    for index in range(3):
        path = directory / f"long-{index}.mp4"
        generated(
            path, [(30 + index * 70, 40 + index * 50, 80 + index * 35)], frames_per_color=1440
        )
        response = client.post(
            f"/api/projects/{pid}/clips",
            files={"file": (path.name, path.read_bytes(), "video/mp4")},
        )
        assert response.status_code == 200, response.text
        ids = [c["id"] for c in response.json()["clips"]]
    real_model = visual.readiness()["ready"]
    if not real_model:
        monkeypatch.setattr(visual, "readiness", lambda: {"ready": True})
        monkeypatch.setattr(visual, "verify_model", lambda: None)
    original = engine.run_command
    requests = []

    def adapter(args, stage, name, *rest, **kwargs):
        if name != "visual-inference":
            return original(args, stage, name, *rest, **kwargs)
        from tests.test_visual_matching import record

        batch = json.loads((stage / "visual-request.json").read_text())
        requests.extend(batch)
        if real_model:
            return original(args, stage, name, *rest, **kwargs)
        return subprocess.CompletedProcess(
            args,
            0,
            json.dumps(
                {"analyses": [record(r["source_hash"], r["frames"]) for r in batch]}
            ).encode(),
            b"",
        )

    monkeypatch.setattr(engine, "run_command", adapter)
    url = f"/api/projects/{pid}/generation"
    assert (
        client.post(
            url, json={"captions": "off", "audio": "mute", "replace_customized": True}
        ).status_code
        == 202
    )
    done = wait(client, url, timeout=150)
    assert done["status"] == "ready", done
    assert sum(len(r["frames"]) for r in requests) <= 512
    library = client.get(f"/api/projects/{pid}/clips").json()["clips"]
    with projects.database() as db:
        cached = {
            r["source_hash"]: json.loads(r["analysis"])["samples"]
            for r in db.execute("SELECT * FROM visual_cache WHERE project_id=?", (pid,))
        }
    for clip in library:
        assert clip["sha256"] in cached  # Including all later uploads.
        if clip["id"] != ids[0]:
            assert cached[clip["sha256"]][0]["frame"] == 0
            assert cached[clip["sha256"]][-1]["frame"] >= 3585
    output = client.get(f"/api/projects/{pid}/render").json()["output"]
    assert output["duration_seconds"] == pytest.approx(3, abs=1 / 30)
    assert len(output["spec"]["sequence"]["slots"]) == 3
    print(
        "ADAPTIVE_EXPORT", len(library), sum(len(r["frames"]) for r in requests), done["output_id"]
    )


def test_visual_failure_can_continue_with_measurements_and_retained_checkpoints(local, monkeypatch):
    client, directory, _ = local
    pid = seed(client, directory)
    monkeypatch.setattr(generation.visual_matching, "readiness", lambda: {"ready": True})
    original = generation.visual_matching.analyze

    def fail(*args, **kwargs):
        raise engine.RetrievalFailure("sample_limit", "Fixture coverage unavailable")

    monkeypatch.setattr(generation.visual_matching, "analyze", fail)
    url = f"/api/projects/{pid}/generation"
    options = {"captions": "off", "audio": "mute", "replace_customized": True}
    assert client.post(url, json=options).status_code == 202
    failed = wait(client, url)
    assert failed["status"] == "failed" and failed["completed"] == ["slots"]
    slot_revision = sequence.read(pid).sequence.revision
    monkeypatch.setattr(
        sequence, "generate", lambda *a, **k: pytest.fail("Must retain completed slots")
    )
    monkeypatch.setattr(
        generation.visual_matching,
        "analyze",
        lambda *a, **k: pytest.fail("No visual work in fallback"),
    )
    assert (
        client.post(
            url, json=options | {"expected_revision": 1, "matching_mode": "measurements"}
        ).status_code
        == 202
    )
    ready = wait(client, url)
    assert ready["status"] == "ready", ready
    assert sequence.read(pid).sequence.revision == slot_revision + 1  # Assignment save only.
    assert any("subject-aware matching was not used" in n for n in ready["notices"])
    monkeypatch.setattr(generation.visual_matching, "analyze", original)


@pytest.mark.parametrize("choice", ["off", "partial"])
def test_partial_caption_review_requires_confirmation_and_resumes_only_caption_render(
    local, monkeypatch, choice
):
    import captions

    client, directory, _ = local
    pid = seed(client, directory)
    # Recognition is mocked here; optional-asset setup gates are covered separately.
    monkeypatch.setattr(generation, "capability", lambda *args: None)
    calls = []

    def incomplete(project_id, source, *args):
        calls.append(project_id)
        return (
            [captions.Cue(start=0.5, end=1.2, text="Reliable words")],
            ["Some text remains unreadable"],
            {
                "unreadable_samples": 3,
                "method": generation.automatic_captions.METHOD,
                "reads": 4,
                "engine_version": "local-test",
                "assets_commit": "a" * 40,
                "source": source["identity"].model_dump(mode="json"),
                "reference_operation_id": source["reference_operation_id"],
            },
        )

    monkeypatch.setattr(generation.automatic_captions, "detect", incomplete)
    url = f"/api/projects/{pid}/generation"
    options = {
        "captions": "reference",
        "audio": "mute",
        "replace_customized": True,
        "matching_mode": "measurements",
    }
    assert client.post(url, json=options).status_code == 202
    failed = wait(client, url)
    assert failed["failure_code"] == "caption_partial", failed
    assert failed["completed"] == ["slots", "sequence", "grading", "audio"]
    assert failed["caption_review"]["cues"][0]["text"] == "Reliable words"
    assert client.get(f"/api/projects/{pid}/render").json()["output"] is None
    correction = {
        "expected_revision": 1,
        "cues": [{"start": 0.5, "end": 1.2, "text": "User corrected words"}],
        "verified_indices": [0],
    }
    corrected = client.post(url + "/caption-review", json=correction)
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["revision"] == 2
    assert client.post(url + "/caption-review", json=correction).status_code == 409
    assert (
        client.post(
            url + "/caption-review",
            json=correction | {"expected_revision": 2, "verified_indices": [1]},
        ).status_code
        == 422
    )
    assert (
        client.post(
            url + "/caption-review",
            json=correction
            | {"expected_revision": 2, "cues": [{"start": 0.5, "end": 4.0, "text": "Outside"}]},
        ).status_code
        == 422
    )
    seq = sequence.read(pid).sequence.model_dump()
    with projects.database() as db:
        measured = [
            tuple(r)
            for r in db.execute("SELECT blueprint FROM color_operations WHERE project_id=?", (pid,))
        ]
    monkeypatch.setattr(
        sequence, "generate", lambda *a, **k: pytest.fail("Do not recreate completed slots")
    )

    def retry_with_less_text(*args):
        _, notes, evidence = incomplete(*args)
        return [], notes, evidence

    monkeypatch.setattr(generation.automatic_captions, "detect", retry_with_less_text)
    assert client.post(url, json=options | {"expected_revision": 2}).status_code == 202
    retried = wait(client, url)
    assert retried["failure_code"] == "caption_partial"
    assert retried["caption_review"]["cues"][0]["text"] == "User corrected words"
    if choice == "off":
        monkeypatch.setattr(
            generation.automatic_captions,
            "detect",
            lambda *a, **k: pytest.fail("Off must not OCR again"),
        )
        retry = options | {"captions": "off", "expected_revision": 3}
    else:
        monkeypatch.setattr(
            generation.automatic_captions,
            "detect",
            lambda *a, **k: pytest.fail("Use saved reviewed cues"),
        )
        retry = options | {"accept_partial_captions": True, "expected_revision": 3}
    assert client.post(url, json=retry).status_code == 202
    done = wait(client, url)
    assert done["status"] == "ready", done
    output = client.get(f"/api/projects/{pid}/render").json()["output"]
    assert output["spec"]["captions"]["enabled"] is (choice == "partial")
    assert sequence.read(pid).sequence.model_dump() == seq
    assert done["caption_review"]["cues"][0]["text"] == "User corrected words"
    with projects.database() as db:
        assert [
            tuple(r)
            for r in db.execute("SELECT blueprint FROM color_operations WHERE project_id=?", (pid,))
        ] == measured
    assert len(calls) == 2


def test_partial_caption_confirmation_rejects_changed_reference(local):
    import captions

    client, directory, _ = local
    pid = seed(client, directory)
    source = generation.color.source(pid)
    review = generation.CaptionReview(
        cues=[captions.Cue(start=0.5, end=1, text="Read words")],
        notices=[],
        unreadable_samples=1,
        evidence=captions.VisibleBinding(
            source=source["identity"].model_copy(update={"media_sha256": "b" * 64}),
            reference_operation_id=source["reference_operation_id"],
            method=generation.automatic_captions.METHOD,
            reads=1,
            engine_version="test",
            assets_commit="a" * 40,
        ),
    )
    operation = generation.Operation(
        operation_id=str(__import__("uuid").uuid4()),
        options=generation.Request(accept_partial_captions=True),
        caption_review=review,
    )
    with pytest.raises(ReferenceError) as error:
        generation.prepare_captions(
            pid, "reference", threading.Event(), time.monotonic() + 20, operation
        )
    assert error.value.code == "caption_review_required"


def test_user_corrected_cues_survive_overlapping_retry_until_explicit_replacement():
    import captions

    source = {
        "identity": generation.color.Source(
            canonical_url="https://www.tiktok.com/@test/video/6718335390845095173",
            video_id="6718335390845095173",
            media_sha256="a" * 64,
        )
    }
    good = captions.Cue(start=0.5, end=1.5, text="Corrected words\nمرحبا\nThird line")
    wrong = captions.Cue(start=0.5, end=1.5, text="Misread words")
    review = generation.CaptionReview(
        cues=[good],
        verified_cues=[good],
        notices=[],
        unreadable_samples=1,
        evidence=captions.VisibleBinding(
            source=source["identity"],
            reference_operation_id=str(__import__("uuid").uuid4()),
            method=generation.automatic_captions.METHOD,
            reads=1,
            engine_version="test",
            assets_commit="a" * 40,
        ),
    )
    cues, verified = generation.retain_reviewed([wrong], review, source, incomplete=True)
    assert cues == [good] and verified == [good]
    assert generation.retain_reviewed([wrong], review, source, replace_verified=True) == (
        [wrong],
        [],
    )
    assert generation.retain_reviewed(
        [wrong],
        review,
        {"identity": source["identity"].model_copy(update={"media_sha256": "b" * 64})},
    ) == ([wrong], [])


def test_ready_generation_caption_retry_preserves_completed_media_work(monkeypatch):
    bound = {"revisions": {"caption_tracks": 1}, "reference": {}, "clips": {}}
    previous = generation.Operation(
        project_id="fixture",
        operation_id="existing",
        revision=3,
        status="ready",
        completed=["slots", "sequence", "grading", "audio", "captions"],
        options=generation.Request(captions="off"),
    )
    monkeypatch.setattr(generation, "get_operation", lambda _: previous)
    monkeypatch.setattr(
        generation, "record", lambda _: {"snapshot": json.dumps(bound), "cleanup_safe": True}
    )
    monkeypatch.setattr(generation, "snapshot", lambda _: bound)
    from contextlib import contextmanager

    @contextmanager
    def db():
        class Connection:
            def execute(self, *args):
                pass

        yield Connection()

    monkeypatch.setattr(generation.projects, "database", db)
    resumed, _ = generation.begin_operation(
        "fixture", generation.Request(expected_revision=3, captions="reference")
    )
    assert resumed.completed == ["slots", "sequence", "grading", "audio"]


def test_short_user_verified_cue_survives_saved_review_roundtrip():
    import captions

    cue = captions.Cue(start=0.5, end=0.55, text="Verified short cue")
    source = generation.color.Source(
        canonical_url="https://www.tiktok.com/@test/video/6718335390845095173",
        video_id="6718335390845095173",
        media_sha256="a" * 64,
    )
    review = generation.CaptionReview(
        cues=[cue],
        verified_cues=[cue],
        notices=[],
        unreadable_samples=0,
        evidence=captions.VisibleBinding(
            source=source,
            reference_operation_id=str(__import__("uuid").uuid4()),
            method=generation.automatic_captions.METHOD,
            reads=1,
            engine_version="test",
            assets_commit="a" * 40,
        ),
    )
    restored = generation.CaptionReview.model_validate_json(review.model_dump_json())
    assert restored.cues == restored.verified_cues == [cue]


def test_saved_user_correction_survives_retry_without_claiming_verification():
    import captions

    source = {
        "identity": generation.color.Source(
            canonical_url="https://www.tiktok.com/@test/video/6718335390845095173",
            video_id="6718335390845095173",
            media_sha256="a" * 64,
        )
    }
    corrected = captions.Cue(start=0.5, end=1.0, text="User corrected words")
    review = generation.CaptionReview(
        cues=[corrected],
        corrected_cues=[corrected],
        notices=[],
        unreadable_samples=1,
        evidence=captions.VisibleBinding(
            source=source["identity"],
            reference_operation_id=str(__import__("uuid").uuid4()),
            method=generation.automatic_captions.METHOD,
            reads=1,
            engine_version="test",
            assets_commit="a" * 40,
        ),
    )
    wrong = captions.Cue(start=0.5, end=1.0, text="New incorrect reading")
    assert generation.retain_reviewed([wrong], review, source) == ([corrected], [])
    assert generation.retain_reviewed([wrong], review, source, replace_verified=True) == (
        [wrong],
        [],
    )


def test_continuous_observed_retry_supersedes_old_fragments_but_keeps_manual_and_other_cues():
    import uuid

    import captions

    source = {
        "identity": generation.color.Source(
            canonical_url="https://www.tiktok.com/@test/video/6718335390845095173",
            video_id="6718335390845095173",
            media_sha256="a" * 64,
        )
    }
    fragments = [
        captions.Cue(start=1.0, end=1.3, text="Partial"),
        captions.Cue(start=1.6, end=1.8, text="Noisy partial"),
    ]
    unrelated = captions.Cue(start=4.0, end=5.0, text="Other caption")
    manual = captions.Cue(start=6.0, end=7.0, text="User correction")
    review = generation.CaptionReview(
        cues=[*fragments, unrelated, manual],
        corrected_cues=[manual],
        notices=[],
        unreadable_samples=1,
        evidence=captions.VisibleBinding(
            source=source["identity"],
            reference_operation_id=str(uuid.uuid4()),
            method=generation.automatic_captions.METHOD,
            reads=2,
            engine_version="test",
            assets_commit="a" * 40,
        ),
    )
    continuous = captions.Cue(start=1.0, end=2.0, text="Observed continuous words")
    wrong = captions.Cue(start=6.0, end=7.0, text="Automatic misreading")
    result, verified = generation.retain_reviewed(
        [continuous, wrong], review, source, incomplete=True
    )
    assert result == [continuous, unrelated, manual] and verified == []


def test_scoped_partial_reconciliation_preserves_unobserved_cue_portions():
    import uuid

    import captions

    source = {
        "identity": generation.color.Source(
            canonical_url="https://www.tiktok.com/@test/video/6718335390845095173",
            video_id="6718335390845095173",
            media_sha256="a" * 64,
        )
    }
    old = captions.Cue(start=1.0, end=5.0, text="Existing valid words")
    new = captions.Cue(start=4.0, end=5.0, text="Improved words")
    review = generation.CaptionReview(
        cues=[old],
        notices=[],
        unreadable_samples=1,
        evidence=captions.VisibleBinding(
            source=source["identity"],
            reference_operation_id=str(uuid.uuid4()),
            method=generation.automatic_captions.METHOD,
            reads=1,
            engine_version="test",
            assets_commit="a" * 40,
        ),
    )
    result, _ = generation.retain_reviewed(
        [new], review, source, incomplete=True, observed_window=(4.0, 6.0)
    )
    assert [(c.start, c.end, c.text) for c in result] == [
        (1.0, 4.0, old.text),
        (4.0, 5.0, new.text),
    ]


def test_repeated_retry_notices_cannot_poison_operation_restoration(local):
    import uuid

    from tests.test_projects import create

    client, _, _ = local
    pid = create(client)["id"]
    operation = generation.Operation(
        project_id=pid, operation_id=str(uuid.uuid4()), revision=1, status="failed"
    )
    operation.notices = ["Completed work preserved"] * 40
    with projects.database() as db:
        db.execute(
            "INSERT INTO generation_operations VALUES(?,?,?,?,?,?,?)",
            (pid, operation.operation_id, 1, "failed", operation.model_dump_json(), "{}", 1),
        )
    restored = generation.get_operation(pid)
    assert restored.notices == ["Completed work preserved"]
    generation.persist(pid, operation, {})
    with projects.database() as db:
        assert (
            len(
                json.loads(
                    db.execute(
                        "SELECT operation FROM generation_operations WHERE project_id=?", (pid,)
                    ).fetchone()[0]
                )["notices"]
            )
            == 1
        )


def test_observed_source_label_removes_only_matching_unprotected_old_interval():
    import uuid

    import captions

    source = {
        "identity": generation.color.Source(
            canonical_url="https://www.tiktok.com/@test/video/6718335390845095173",
            video_id="6718335390845095173",
            media_sha256="a" * 64,
        )
    }
    label = captions.Cue(start=1.0, end=2.0, text="CreatorLabel")
    other = captions.Cue(start=3.0, end=4.0, text="CreatorLabel")
    protected = captions.Cue(start=5.0, end=6.0, text="CreatorLabel")
    review = generation.CaptionReview(
        cues=[label, other, protected],
        corrected_cues=[protected],
        notices=[],
        unreadable_samples=1,
        evidence=captions.VisibleBinding(
            source=source["identity"],
            reference_operation_id=str(uuid.uuid4()),
            method=generation.automatic_captions.METHOD,
            reads=1,
            engine_version="test",
            assets_commit="a" * 40,
        ),
    )
    result, _ = generation.retain_reviewed(
        [],
        review,
        source,
        incomplete=True,
        excluded_intervals=[
            {"start": 1.2, "end": 1.3, "text": "CreatorLabel"},
            {"start": 5.2, "end": 5.3, "text": "CreatorLabel"},
        ],
    )
    assert result == [other, protected]


def test_observed_replacement_does_not_leave_the_superseded_automatic_tail():
    import uuid

    import captions

    source = {
        "identity": generation.color.Source(
            canonical_url="https://www.tiktok.com/@test/video/6718335390845095173",
            video_id="6718335390845095173",
            media_sha256="a" * 64,
        )
    }
    old = captions.Cue(start=1, end=5, text="Old automatic fragment")
    fresh = captions.Cue(start=1, end=4, text="Observed replacement")
    review = generation.CaptionReview(
        cues=[old],
        notices=[],
        unreadable_samples=1,
        evidence=captions.VisibleBinding(
            source=source["identity"],
            reference_operation_id=str(uuid.uuid4()),
            method=generation.automatic_captions.METHOD,
            reads=2,
            engine_version="test",
            assets_commit="a" * 40,
        ),
    )
    result, _ = generation.retain_reviewed(
        [fresh], review, source, incomplete=True, observed_window=(0, 6)
    )
    assert result == [fresh]


def evidence_review(cues, observations, *, protected=()):
    import uuid

    import captions

    source = {
        "identity": generation.color.Source(
            canonical_url="https://www.tiktok.com/@test/video/6718335390845095173",
            video_id="6718335390845095173",
            media_sha256="a" * 64,
        )
    }
    review = generation.CaptionReview(
        cues=cues,
        observations=observations,
        corrected_cues=list(protected),
        notices=[],
        unreadable_samples=1,
        evidence=captions.VisibleBinding(
            source=source["identity"],
            reference_operation_id=str(uuid.uuid4()),
            method=generation.automatic_captions.METHOD,
            reads=2,
            engine_version="test",
            assets_commit="a" * 40,
        ),
    )
    return review, source


def observation(cue, step, wording="ocr", supported=True):
    return generation.CaptionObservation(
        start=cue.start,
        end=cue.end,
        text=cue.text,
        start_window=(max(0, cue.start - step), cue.start),
        end_window=(max(0, cue.end - step), cue.end),
        wording=wording,
        start_supported=supported,
        end_supported=supported,
    )


def test_coarse_full_run_preserves_refined_boundaries_but_accepts_better_words():
    import captions

    old = captions.Cue(start=1.233333, end=2.466667, text="Really observed sentence.")
    fresh = captions.Cue(start=1.5, end=2.5, text="Really observed sentence!")
    review, source = evidence_review([old], [observation(old, 1 / 30)])
    facts = [observation(fresh, 0.5, "paired_model")]
    result, _ = generation.retain_reviewed(
        [fresh], review, source, incomplete=True, observations=facts
    )
    assert [(c.start, c.end, c.text) for c in result] == [(old.start, old.end, fresh.text)]
    assert (
        facts[0].wording == "paired_model"
        and facts[0].start_window == review.observations[0].start_window
    )


def test_stronger_observations_correct_timing_without_weaker_text_replacing_model_words():
    import captions

    old = captions.Cue(start=1.5, end=2.5, text="I read the words correctly")
    fresh = captions.Cue(start=1.2, end=2.4, text="1 read the words correctly")
    review, source = evidence_review([old], [observation(old, 0.5, "paired_model")])
    facts = [observation(fresh, 1 / 30, "unresolved")]
    result, _ = generation.retain_reviewed(
        [fresh], review, source, incomplete=True, observations=facts
    )
    assert [(c.start, c.end, c.text) for c in result] == [(fresh.start, fresh.end, old.text)]
    assert facts[0].wording == "paired_model"


def test_observed_absence_removes_stale_tail_not_neighbor_or_manual_correction():
    import captions

    tail = captions.Cue(start=2.4, end=2.8, text="Previously split fragment")
    neighbor = captions.Cue(start=3, end=4, text="Distinct reply")
    manual = captions.Cue(start=5, end=6, text="Protected wording")
    fresh = captions.Cue(start=1, end=2.4, text="Complete replacement")
    review, source = evidence_review([tail, neighbor, manual], [], protected=[manual])
    result, _ = generation.retain_reviewed(
        [fresh],
        review,
        source,
        incomplete=True,
        observed_absence=[(2.4, 2.9), (5, 6)],
    )
    assert result == [fresh, neighbor, manual]


def test_changed_source_cannot_reuse_boundary_or_absence_evidence():
    import captions

    old = captions.Cue(start=1.2, end=2.4, text="Observed words")
    fresh = captions.Cue(start=1.5, end=2.5, text=old.text)
    review, source = evidence_review([old], [observation(old, 1 / 30)])
    source["identity"] = source["identity"].model_copy(update={"media_sha256": "b" * 64})
    result, _ = generation.retain_reviewed(
        [fresh],
        review,
        source,
        incomplete=True,
        observations=[observation(fresh, 0.5)],
        observed_absence=[(1, 3)],
    )
    assert result == [fresh]


def test_unconfirmed_ocr_cutoff_cannot_replace_supported_visibility_boundary():
    import captions

    old = captions.Cue(start=1.2, end=2.4, text="Observed words")
    fresh = captions.Cue(start=1.3, end=2.2, text=old.text)
    review, source = evidence_review([old], [observation(old, 0.1)])
    facts = [observation(fresh, 1 / 30, supported=False)]
    result, _ = generation.retain_reviewed(
        [fresh], review, source, incomplete=True, observations=facts
    )
    assert result == [old]
    assert facts[0].end_supported and facts[0].end_window == review.observations[0].end_window


def test_normal_generation_retry_persists_independent_word_and_boundary_evidence(
    local, monkeypatch
):
    import captions

    client, directory, _ = local
    pid = seed(client, directory)
    monkeypatch.setattr(generation, "capability", lambda *args: None)
    calls = []

    def detector(project_id, source, *args):
        cue = captions.Cue(
            start=0.5 if not calls else 0.7,
            end=1.2 if not calls else 1.3,
            text="Actually observed words." if not calls else "Actually observed words!",
        )
        fact = observation(
            cue, 1 / 30 if not calls else 0.5, "ocr" if not calls else "paired_model"
        )
        calls.append(cue)
        return (
            [cue],
            [],
            {
                "unreadable_samples": 1,
                "observations": [fact.model_dump()],
                "source": source["identity"].model_dump(),
                "reference_operation_id": source["reference_operation_id"],
                "method": generation.automatic_captions.METHOD,
                "reads": 2,
                "engine_version": "fixture",
                "assets_commit": "a" * 40,
            },
        )

    monkeypatch.setattr(generation.automatic_captions, "detect", detector)
    url = f"/api/projects/{pid}/generation"
    options = {
        "captions": "reference",
        "audio": "mute",
        "replace_customized": True,
        "matching_mode": "measurements",
    }
    for revision in (0, 1):
        response = client.post(url, json=options | {"expected_revision": revision})
        assert response.status_code == 202, response.text
        saved = wait(client, url)
        assert saved["failure_code"] == "caption_partial", saved
    cue = saved["caption_review"]["cues"][0]
    assert (cue["start"], cue["end"], cue["text"]) == (0.5, 1.2, calls[1].text)
    fact = saved["caption_review"]["observations"][0]
    assert fact["wording"] == "paired_model" and fact["end_window"] == pytest.approx(
        [1.2 - 1 / 30, 1.2]
    )
    assert client.get(url).json() == saved


def test_supported_end_and_absent_tail_supersede_only_adjacent_repeated_fragment():
    import captions

    fresh = captions.Cue(
        start=1,
        end=2.4,
        text="Extra first line\nI read these words correctly\nand every visible line is retained",
    )
    tail = captions.Cue(
        start=2.4, end=2.9, text="1 read these words correctly\nand every visible line is retained"
    )
    neighbor = captions.Cue(start=4, end=5, text=tail.text)
    review, source = evidence_review([tail, neighbor], [])
    facts = [observation(fresh, 1 / 30)]
    result, _ = generation.retain_reviewed(
        [fresh], review, source, incomplete=True, observations=facts, observed_absence=[(2.5, 2.8)]
    )
    assert result == [fresh, neighbor]


def test_legacy_missing_support_flags_do_not_allow_coarse_overwrite_of_fine_brackets():
    import captions

    old = captions.Cue(start=1.233333, end=2.466667, text="Observed continuous words")
    fresh = captions.Cue(start=1.5, end=2.5, text=old.text)
    prior = observation(old, 1 / 30, supported=False)
    review, source = evidence_review([old], [prior])
    result, _ = generation.retain_reviewed(
        [fresh], review, source, incomplete=True, observations=[observation(fresh, 0.5)]
    )
    assert result == [old]


def test_coarse_partial_single_line_cannot_erase_a_supported_multiline_phase():
    import captions

    old = captions.Cue(
        start=2.033333, end=3.466667, text="Observed first line\nObserved second line"
    )
    fresh = captions.Cue(start=1.5, end=3.5, text="Observed first line")
    neighbor = captions.Cue(start=4, end=5, text="Distinct reply")
    review, source = evidence_review([old, neighbor], [observation(old, 1 / 30)])
    result, _ = generation.retain_reviewed(
        [fresh], review, source, incomplete=True, observations=[observation(fresh, 0.5)]
    )
    assert old in result and neighbor in result
    assert [(c.start, c.end, c.text) for c in result if c != old and c != neighbor] == [
        (fresh.start, old.start, fresh.text),
        (old.end, fresh.end, fresh.text),
    ]


def test_pinned_multiline_evidence_cannot_duplicate_a_manual_correction():
    import captions

    old = captions.Cue(start=1.2, end=2.4, text="Observed first line\nManual second line")
    fresh = captions.Cue(start=1.5, end=2.5, text="Observed first line")
    review, source = evidence_review([old], [observation(old, 1 / 30)], protected=[old])
    result, _ = generation.retain_reviewed(
        [fresh], review, source, incomplete=True, observations=[observation(fresh, 0.5)]
    )
    assert result == [old]


@pytest.mark.parametrize(
    "step,supported,expected_old", [(0.5, True, True), (1 / 30, False, True), (1 / 30, True, False)]
)
def test_legacy_cue_needs_supported_fine_observations_before_timing_replacement(
    step, supported, expected_old
):
    import captions

    old = captions.Cue(start=1.233333, end=2.466667, text="Observed words.")
    fresh = captions.Cue(start=1.5, end=2.5, text="Observed words!")
    review, source = evidence_review([old], [])
    facts = [observation(fresh, step, "paired_model", supported)]
    result, _ = generation.retain_reviewed(
        [fresh], review, source, incomplete=True, observations=facts
    )
    assert result[0].text == fresh.text
    assert (result[0].start, result[0].end) == (
        (old.start, old.end) if expected_old else (fresh.start, fresh.end)
    )
    if expected_old:
        assert facts[0].start_window is None and facts[0].end_window is None
    assert generation.CaptionObservation.model_validate_json(facts[0].model_dump_json()) == facts[0]


def test_negative_visibility_supersedes_tail_despite_preserved_legacy_end():
    import captions

    old = captions.Cue(
        start=1.0,
        end=2.4,
        text="An earlier distinct line before the entire complete observed sentence finishes now",
    )
    tail = captions.Cue(start=2.4, end=2.9, text="complete observed sentence finishes")
    fresh = old.model_copy(update={"end": 2.6})
    review, source = evidence_review([old, tail], [])
    observations = [
        generation.CaptionObservation(
            start=1.0,
            end=2.6,
            text=fresh.text,
            start_window=(1.0, 1.0),
            end_window=(2.3, 2.6),
            start_supported=True,
            end_supported=True,
            wording="paired_model",
        )
    ]
    result, _ = generation.retain_reviewed(
        [fresh],
        review,
        source,
        incomplete=True,
        observations=observations,
        observed_absence=[(2.6, 2.95)],
    )
    assert result == [old]


def test_many_coarse_blocks_cannot_overwrite_finer_saved_phases():
    import captions

    first = captions.Cue(start=1.1, end=3.1, text="A first visible line")
    second = captions.Cue(start=3.1, end=4.9, text="A first visible line\nAnother visible line")
    review, source = evidence_review([first, second], [])
    fresh = [
        first.model_copy(update={"start": 1.5, "end": 3.5}),
        second.model_copy(update={"start": 3.5, "end": 5.0}),
    ]
    facts = [
        generation.CaptionObservation(
            start=c.start,
            end=c.end,
            text=c.text,
            start_window=(c.start - 0.5, c.start),
            end_window=(c.end - 0.5, c.end),
            start_supported=False,
            end_supported=False,
            wording="ocr",
        )
        for c in fresh
    ]
    result, _ = generation.retain_reviewed(
        fresh, review, source, incomplete=True, observations=facts
    )
    assert result == [first, second]


def test_coarse_continuation_cannot_recreate_a_verified_automatic_tail():
    import captions

    old = captions.Cue(start=1.1, end=2.4, text="A complete visible sentence")
    fact = generation.CaptionObservation(
        start=old.start,
        end=old.end,
        text=old.text,
        start_window=(1.0666666667, 1.1),
        end_window=(2.3666666667, 2.4),
        start_supported=True,
        end_supported=True,
        wording="paired_model",
    )
    review, source = evidence_review([old], [fact])
    fresh = old.model_copy(update={"start": 1.5, "end": 3.0})
    facts = [
        generation.CaptionObservation(
            start=1.5,
            end=3.0,
            text=fresh.text,
            start_window=(1.0, 1.5),
            end_window=(2.5, 3.0),
            start_supported=False,
            end_supported=True,
            wording="unresolved",
        )
    ]
    result, _ = generation.retain_reviewed(
        [fresh], review, source, incomplete=True, observations=facts
    )
    assert result == [old]
