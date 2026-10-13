import pytest

import caption_readings as readings
import projects
from tests.test_projects import create  # noqa: F401
from tests.test_projects import local as local


def test_readings_bind_source_and_tool_without_altering_project(local):
    client, _, _ = local
    project = create(client)
    pid = project["id"]
    namespace = readings.identity({"hash": "a"}, {"model": "v1"})
    readings.save(pid, "handwriting", namespace, "crop", b"observed words")
    assert readings.read(pid, "handwriting", namespace, "crop") == b"observed words"
    assert (
        readings.read(pid, "handwriting", readings.identity({"hash": "b"}, {"model": "v1"}), "crop")
        is None
    )
    assert (
        readings.read(pid, "handwriting", readings.identity({"hash": "a"}, {"model": "v2"}), "crop")
        is None
    )
    assert readings.read(pid, "ocr", namespace, "crop") is None
    assert client.get(f"/api/projects/{pid}").json() == project


def test_cache_is_bounded_and_cascades_without_resurrecting_deleted_projects(local):
    client, _, _ = local
    pid = create(client)["id"]
    for i in range(34):
        readings.save(pid, "ocr", "namespace", str(i), b"safe TSV")
    assert readings.read(pid, "ocr", "namespace", "0") is None
    assert readings.read(pid, "ocr", "namespace", "33") == b"safe TSV"
    with projects.database() as db:
        assert db.execute("SELECT count(*) FROM caption_readings").fetchone()[0] == 32
        db.execute("DELETE FROM projects WHERE id=?", (pid,))
    readings.save(pid, "ocr", "namespace", "late", b"late worker")
    assert readings.read(pid, "ocr", "namespace", "late") is None
    with pytest.raises(ValueError, match="payload"):
        readings.save(pid, "ocr", "namespace", "oversized", b"x" * (readings.MAX_PAYLOAD + 1))


def test_late_generation_cannot_overwrite_a_newer_correction(local):
    import json
    import uuid

    import generation

    client, _, _ = local
    pid = create(client)["id"]
    stale = generation.Operation(
        project_id=pid, operation_id=str(uuid.uuid4()), revision=1, status="failed"
    )
    newer = stale.model_copy(update={"revision": 2, "message": "Saved correction"})
    with projects.database() as db:
        db.execute(
            "INSERT INTO generation_operations VALUES(?,?,?,?,?,?,?)",
            (pid, stale.operation_id, 2, "failed", newer.model_dump_json(), "{}", 1),
        )
    generation.persist(pid, stale, {})
    with projects.database() as db:
        saved = json.loads(
            db.execute(
                "SELECT operation FROM generation_operations WHERE project_id=?", (pid,)
            ).fetchone()[0]
        )
    assert saved["revision"] == 2 and saved["message"] == "Saved correction"


def test_retry_reuses_completed_model_facts_and_only_submits_missing_crops(local, monkeypatch):
    import json
    import time

    import automatic_captions as automatic

    client, directory, _ = local
    pid = create(client)["id"]
    names = ["a" * 64 + ".png", "b" * 64 + ".png", "c" * 64 + ".png"]
    calls = []

    def model(args, stage, pending, deadline, *, persist):
        calls.append(pending)
        chosen = pending[:2]
        payload = {
            "engine": "test",
            "results": [
                {"name": n, "text": "Observed words", "score": 0.9, "capped": False} for n in chosen
            ],
        }
        persist(payload)
        return payload, len(chosen) != len(pending)

    monkeypatch.setattr(automatic, "run_handwriting", model)
    first = automatic.cached_handwriting(
        pid, "source-model", directory, names, time.monotonic() + 10
    )
    second = automatic.cached_handwriting(
        pid, "source-model", directory, names, time.monotonic() + 10
    )
    assert first[1:] == (True, 3, 0)
    assert second[1:] == (False, 1, 2) and calls == [names, names[2:]]
    assert {r["name"] for r in second[0]["results"]} == set(names)
    assert json.loads((directory / "request.json").read_text()) == names[2:]
