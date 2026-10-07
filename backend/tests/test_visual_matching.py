import base64
import json
import threading
import time

import pytest

import projects
import reference_engine as engine
import visual_matching as visual
from tests.test_assembly import synthetic
from tests.test_video_render import local as local  # noqa: F401


def record(digest, frames):
    return {
        "source_hash": digest,
        "revision": visual.REVISION,
        "algorithm": visual.ALGORITHM,
        "samples": [
            {
                "frame": f,
                "embedding": [1.0] + [0.0] * 511,
                "image": base64.b64encode(b"\xff\xd8fixture\xff\xd9").decode(),
            }
            for f in frames
        ],
    }


def test_validation_and_multisample_scoring():
    a = visual.validate(record("a" * 64, [0, 15, 30]), "a" * 64, [0, 15, 30])
    assert len(visual.range_samples(a, 0, 30)) == 2
    assert not visual.range_samples(a, 1, 15)
    assert visual.similarity(a.samples, a.samples) == 1
    for field, value in [("revision", "wrong"), ("algorithm", "wrong"), ("source_hash", "b" * 64)]:
        with pytest.raises(engine.RetrievalFailure):
            visual.validate(record("a" * 64, [0]) | {field: value}, "a" * 64, [0])
    for embedding in [[float("nan")] + [0.0] * 511, [0.0] * 512, [1.0]]:
        bad = record("a" * 64, [0])
        bad["samples"][0]["embedding"] = embedding
        with pytest.raises(engine.RetrievalFailure):
            visual.validate(bad, "a" * 64, [0])
    with pytest.raises(engine.RetrievalFailure):
        visual.validate(record("a" * 64, [0]), "a" * 64, [15])


def test_readiness_never_downloads(monkeypatch, tmp_path):
    monkeypatch.setattr(visual, "MODEL", tmp_path / "absent.onnx")
    monkeypatch.setattr(
        visual.urllib.request, "urlopen", lambda *a, **k: pytest.fail("implicit network")
    )
    assert not visual.readiness()["ready"]
    with pytest.raises(engine.RetrievalFailure) as error:
        visual.verify_model()
    assert error.value.code == "model_unavailable"


def test_cached_adapter_validation_and_project_budget(local, monkeypatch, tmp_path):
    from tests.test_sequence import setup

    client, directory, _ = local
    pid, _, _, _ = setup(client, directory)
    monkeypatch.setattr(visual, "verify_model", lambda: None)
    measurement = synthetic(60)
    with projects.database() as db:
        db.execute(
            "INSERT INTO visual_cache VALUES(?,?,?,?)",
            (pid, "a" * 64, visual.ALGORITHM, json.dumps(record("a" * 64, [0, 15, 30, 45]))),
        )
    monkeypatch.setattr(
        visual.color, "inspect_video", lambda *a: pytest.fail("cache should not decode")
    )
    updates = []
    analyses = visual.analyze(
        pid,
        [("a" * 64, tmp_path / "unused", measurement)],
        tmp_path,
        threading.Event(),
        time.monotonic() + 5,
        updates.append,
    )
    assert len(analyses["a" * 64].samples) == 4 and updates == [1]
    monkeypatch.setattr(visual, "MAX_SAMPLES", 3)
    with pytest.raises(engine.RetrievalFailure) as error:
        visual.analyze(
            pid,
            [("a" * 64, tmp_path / "unused", measurement)],
            tmp_path,
            threading.Event(),
            time.monotonic() + 5,
            updates.append,
        )
    assert error.value.code == "sample_limit"


def test_visual_selection_tracks_association_after_reorder_and_reports_missing():
    import uuid
    from types import SimpleNamespace

    import assembly
    import sequence

    clips = [str(uuid.UUID(int=i)) for i in (1, 2, 3)]

    def embedding(digest, channel):
        data = record(digest, [0, 15, 30, 45])
        for item in data["samples"]:
            item["embedding"] = [float(i == channel) for i in range(512)]
        return visual.validate(data, digest, [0, 15, 30, 45])

    reference = record("a" * 64, [0, 15, 30, 45])
    for item in reference["samples"][2:]:
        item["embedding"] = [0.0, 1.0] + [0.0] * 510
    embeddings = {id: embedding("b" * 64, i) for i, id in enumerate(clips)}
    embeddings["reference"] = visual.validate(reference, "a" * 64, [0, 15, 30, 45])
    # Slot order is deliberately reversed relative to its associated reference shots.
    slots = [
        sequence.Slot(
            id=uuid.uuid4(),
            duration_frames=30,
            source_end_frame=30,
            output_start_frame=i * 30,
            output_end_frame=(i + 1) * 30,
            reference_start_frame=r,
            reference_end_frame=r + 30,
        )
        for i, r in enumerate([30, 0])
    ]
    data = {
        "sequence": SimpleNamespace(slots=slots, output_frames=60),
        "settings": assembly.Settings(expected_sequence_revision=1, matching_mode="subject_aware"),
    }
    measured = {id: synthetic(60) for id in clips}
    chosen = assembly.select(data, measured, synthetic(60), embeddings=embeddings)
    assert [str(c.clip_id) for c in chosen] == [clips[1], clips[0]]
    assert [c.reference_start_frame for c in chosen] == [30, 0]
    assert all(c.visual_similarity == 1 and len(c.footage_sample_frames) == 2 for c in chosen)
    assert chosen == assembly.select(data, measured, synthetic(60), embeddings=embeddings)
    data["sequence"].slots = [
        slots[0].model_copy(update={"reference_start_frame": None, "reference_end_frame": None})
    ]
    fallback = assembly.select(data, measured, synthetic(60), embeddings=embeddings)[0]
    assert fallback.visual_similarity is None and "No reference interval" in fallback.warnings[0]
    # Absent/ambiguous vectors remain suggestions requiring review, never invented labels.
    for analysis in embeddings.values():
        if analysis is not embeddings["reference"]:
            for sample in analysis.samples:
                sample.embedding = [0.0, 0.0, 1.0] + [0.0] * 509
    data["sequence"].slots = slots
    weak = assembly.select(data, measured, synthetic(60), embeddings=embeddings)[0]
    assert any("Weak" in w for w in weak.warnings) and any("Ambiguous" in w for w in weak.warnings)


def test_association_persistence_and_visual_failure_preserves_prior(local, monkeypatch):
    import assembly
    from tests.test_color_analysis import finish
    from tests.test_sequence import setup

    client, directory, _ = local
    pid, _, seq_url, saved = setup(client, directory)
    assert [(s["reference_start_frame"], s["reference_end_frame"]) for s in saved["slots"]] == [
        (0, 30),
        (30, 60),
        (60, 90),
    ]
    slots = [
        {k: s[k] for k in ("id", "duration_frames", "clip_id", "source_start_frame")}
        for s in saved["slots"][::-1]
    ]
    slots[0]["duration_frames"] = 15
    restored = client.post(seq_url, json={"expected_revision": 1, "slots": slots}).json()[
        "sequence"
    ]
    assert [s["reference_start_frame"] for s in restored["slots"]] == [60, 30, 0]
    settings = {"expected_sequence_revision": 2, "allow_reused_ranges": True}
    url = f"/api/projects/{pid}/assembly"
    assert client.post(url, json=settings).status_code == 202
    prior = finish(client, url)["proposal"]
    monkeypatch.setattr(visual, "readiness", lambda: {"ready": False})
    assert client.post(url, json=settings | {"matching_mode": "subject_aware"}).status_code == 409
    monkeypatch.setattr(visual, "readiness", lambda: {"ready": True})

    def failed(*args):
        raise engine.RetrievalFailure("inference_failed", "Deterministic adapter failure")

    monkeypatch.setattr(visual, "analyze", failed)
    assert client.post(url, json=settings | {"matching_mode": "subject_aware"}).status_code == 202
    result = finish(client, url)
    assert result["status"] == "failed" and result["failure_code"] == "inference_failed"
    assert result["proposal"] == prior and client.get(seq_url).json()["sequence"] == restored
    assert not assembly.staging(result["operation_id"]).exists()
    malformed = slots[0] | {"reference_start_frame": 10, "reference_end_frame": 9}
    assert (
        client.post(seq_url, json={"expected_revision": 2, "slots": [malformed]}).status_code == 422
    )
    too_long = slots[0] | {"reference_start_frame": 80, "reference_end_frame": 100}
    assert (
        client.post(seq_url, json={"expected_revision": 2, "slots": [too_long]}).status_code == 422
    )
    changed = [slots[0] | {"reference_start_frame": 0, "reference_end_frame": 30}, *slots[1:]]
    assert client.post(seq_url, json={"expected_revision": 2, "slots": changed}).status_code == 200
    assert client.get(url).json()["proposal_stale"]

    def deterministic_adapter(project_id, items, directory, stop, deadline, progress):
        return {
            digest: visual.validate(
                record(digest, [s.frame for s in measured.samples]),
                digest,
                [s.frame for s in measured.samples],
            )
            for digest, path, measured in items
        }

    monkeypatch.setattr(visual, "analyze", deterministic_adapter)
    request = settings | {"expected_sequence_revision": 3, "matching_mode": "subject_aware"}
    assert client.post(url, json=request).status_code == 202
    ready = finish(client, url)
    assert ready["status"] == "ready", ready
    proposal = ready["proposal"]
    assert proposal["model_revision"] == visual.REVISION
    assert proposal["settings"]["matching_mode"] == "subject_aware"
    assert proposal["choices"][1]["visual_similarity"] == 1
    assert proposal["choices"][1]["reference_image"]
    assert client.get(url).json()["proposal"] == proposal
    applied = client.post(url + "/apply", json=request | {"proposal_id": proposal["id"]})
    assert applied.status_code == 200, applied.text
    assert applied.json()["slots"][0]["reference_start_frame"] == 0
    assert client.get(seq_url).json()["sequence"]["revision"] == 3


def test_real_decode_one_adapter_call_across_large_staging(local, monkeypatch, tmp_path):
    import hashlib
    import subprocess

    import candidate_analysis as candidates
    from tests.test_color_analysis import generated
    from tests.test_sequence import setup

    client, directory, _ = local
    pid, _, _, _ = setup(client, directory)
    stage = tmp_path / "visual-stage"
    stage.mkdir()
    # Shared inspector's default remains 2 MiB; visual caller must pass its own budget.
    (stage / "prior.rgb").write_bytes(b"x" * (3 * 1024 * 1024))
    path = tmp_path / "sample.mp4"
    generated(path, ((10, 20, 30),), 36)
    measurement = synthetic(90)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(visual, "verify_model", lambda: None)
    original = engine.run_command
    calls = []

    def adapter(args, directory, name, *rest, **kwargs):
        if name != "visual-inference":
            return original(args, directory, name, *rest, **kwargs)
        requests = json.loads((directory / "visual-request.json").read_text())
        calls.append(requests)
        return subprocess.CompletedProcess(
            args,
            0,
            json.dumps(
                {"analyses": [record(r["source_hash"], r["frames"]) for r in requests]}
            ).encode(),
            "",
        )

    monkeypatch.setattr(engine, "run_command", adapter)
    result = visual.analyze(
        pid,
        [(digest, path, measurement)],
        stage,
        threading.Event(),
        time.monotonic() + 15,
        lambda n: None,
    )
    assert len(calls) == 1 and len(result[digest].samples) == 6
    assert not list(stage.glob("*.json")) and not list(stage.glob(digest + "*"))
    assert candidates.TEMP_BUDGET < visual.TEMP_BUDGET
