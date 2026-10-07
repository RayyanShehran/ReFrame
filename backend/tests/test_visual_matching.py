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
