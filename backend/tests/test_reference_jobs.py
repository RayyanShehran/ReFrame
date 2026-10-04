import asyncio
import hashlib
import json
import sqlite3
import sys
import threading
import time
import uuid
from contextlib import contextmanager

import pytest

import projects
import reference_engine as engine
import reference_jobs as jobs
from tests.test_projects import create, upload
from tests.test_projects import local as project_fixture


@pytest.fixture
def local(monkeypatch, tmp_path):
    yield from project_fixture.__wrapped__(monkeypatch, tmp_path)


def generated(url, directory, stop):
    assert url.endswith("6718335390845095173")
    path = directory / "reference.mp4"
    path.write_bytes(b"generated reference")
    return {
        "path": path,
        "media": {
            "duration_seconds": 2,
            "width": 320,
            "height": 240,
            "video_codec": "hevc",
            "has_audio": False,
            "audio_codec": None,
            "decoded_frame_bytes": 12288,
            "decoded_audio_samples": 0,
        },
        "versions": {"yt_dlp": "2026.8.19", "ffmpeg": "test"},
    }


@pytest.fixture(autouse=True)
def lifecycle(monkeypatch):
    monkeypatch.setattr(jobs, "start_lock", asyncio.Lock())
    monkeypatch.setattr(jobs, "active", None)
    monkeypatch.setattr(jobs, "closing", set())
    monkeypatch.setattr(engine, "retrieve_media", generated)


def endpoint(saved):
    return f"/api/projects/{saved['id']}/reference-media"


def finished(client, url):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = client.get(url)
        assert result.status_code == 200
        if result.json()["status"] != "running" and jobs.active is None:
            return result.json()
        time.sleep(0.01)
    pytest.fail("Worker did not finish")


def test_retains_separate_media_and_reuses_ready(local):
    client, directory, _ = local
    saved = create(client)
    assert upload(client, saved["id"]).status_code == 200
    url = endpoint(saved)
    assert client.get(url).json()["status"] == "idle"
    started = client.post(url)
    assert started.status_code == 202
    result = finished(client, url)
    assert result["status"] == "ready"
    assert result["media"]["sha256"] == hashlib.sha256(b"generated reference").hexdigest()
    assert result["media"]["has_audio"] is False
    assert result["media"]["audio_codec"] is None
    assert len(list((directory / "projects" / saved["id"]).iterdir())) == 2
    assert list((directory / "reference-staging").iterdir()) == []
    assert client.post(url).json()["operation_id"] == result["operation_id"]
    projects.initialize()
    jobs.recover()
    assert client.get(url).json() == result
    assert client.get(f"/api/projects/{saved['id']}").json()["clip_status"] == "ready"
    assert "path" not in json.dumps(result)


def test_duplicate_busy_and_responsive_reads(local, monkeypatch):
    client, _, _ = local
    first, second = create(client), create(client)
    entered, release = threading.Event(), threading.Event()

    def blocked(*args):
        entered.set()
        assert release.wait(5)
        return generated(*args)

    monkeypatch.setattr(engine, "retrieve_media", blocked)
    try:
        started = client.post(endpoint(first)).json()
        assert entered.wait(2)
        assert client.post(endpoint(first)).json()["operation_id"] == started["operation_id"]
        assert client.post(endpoint(second)).status_code == 503
        assert client.get("/health").status_code == 200
        assert client.get("/api/projects").status_code == 200
        assert not projects.operation_lock.locked()
    finally:
        release.set()
    assert finished(client, endpoint(first))["status"] == "ready"


@pytest.mark.parametrize(
    "code,safe", [("deadline", True), ("missing_tools", True), ("cleanup_failure", False)]
)
def test_failure_cleanup_and_explicit_retry(local, monkeypatch, code, safe):
    client, directory, _ = local
    saved = create(client)

    def failure(url, stage, stop):
        (stage / "partial.part").write_bytes(b"partial")
        raise engine.RetrievalFailure(code, "Safe failure", safe)

    monkeypatch.setattr(engine, "retrieve_media", failure)
    first = client.post(endpoint(saved)).json()
    failed = finished(client, endpoint(saved))
    assert failed["failure_code"] == code
    assert bool(list((directory / "reference-staging").iterdir())) is (not safe)
    if safe:
        monkeypatch.setattr(engine, "retrieve_media", generated)
        next_op = client.post(endpoint(saved)).json()
        assert next_op["operation_id"] != first["operation_id"]
        assert finished(client, endpoint(saved))["status"] == "ready"
    else:
        assert client.post(endpoint(saved)).status_code == 500
        assert client.delete(f"/api/projects/{saved['id']}").status_code == 500
        jobs.recover()
        assert list((directory / "reference-staging").iterdir())


def test_actual_timeout_and_cleanup(local, monkeypatch):
    client, directory, _ = local
    saved = create(client)

    def timeout(url, stage, stop):
        return engine.run_command(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            stage,
            "helper",
            time.monotonic() + 0.2,
            0.2,
        )

    monkeypatch.setattr(engine, "retrieve_media", timeout)
    client.post(endpoint(saved))
    assert finished(client, endpoint(saved))["failure_code"] == "deadline"
    assert list((directory / "reference-staging").iterdir()) == []


@pytest.mark.parametrize("boundary", ["move", "database", "cleanup"])
def test_retention_failure_compensates(local, monkeypatch, boundary):
    client, directory, _ = local
    saved = create(client)
    if boundary == "move":
        from pathlib import Path

        monkeypatch.setattr(Path, "replace", lambda *_: (_ for _ in ()).throw(OSError("move")))
    elif boundary == "cleanup":
        original = jobs.clean_stage

        def clean(op):
            if jobs.staging(op).exists():
                raise OSError("cleanup")
            original(op)

        monkeypatch.setattr(jobs, "clean_stage", clean)
    else:
        original = projects.database

        @contextmanager
        def database():
            with original() as connection:

                class Failing:
                    def execute(self, sql, params=()):
                        if "SET state = 'ready'" in sql:
                            raise sqlite3.OperationalError("commit")
                        return connection.execute(sql, params)

                yield Failing()

        monkeypatch.setattr(projects, "database", database)
    client.post(endpoint(saved))
    result = finished(client, endpoint(saved))
    assert result["status"] == "failed"
    assert result["failure_code"] == (
        "cleanup_failure" if boundary == "cleanup" else "storage_failure"
    )
    assert not list((directory / "projects" / saved["id"]).glob("reference-*"))


def test_delete_stops_and_joins_worker(local, monkeypatch):
    client, directory, _ = local
    saved = create(client)
    entered, stopped = threading.Event(), threading.Event()

    def waiting(url, stage, stop):
        entered.set()
        assert stop.wait(5)
        stopped.set()
        raise engine.RetrievalFailure("interrupted", "Stopped")

    monkeypatch.setattr(engine, "retrieve_media", waiting)
    client.post(endpoint(saved))
    assert entered.wait(2)
    assert client.delete(f"/api/projects/{saved['id']}").status_code == 204
    assert stopped.is_set() and jobs.active is None
    assert not (directory / "projects" / saved["id"]).exists()
    assert client.get(endpoint(saved)).status_code == 404


def test_stale_commit_cannot_overwrite_retry_or_revive_deleted(local):
    client, _, _ = local
    saved = create(client)
    old, url = jobs.begin_operation(saved["id"])
    stage = jobs.staging(old.operation_id)
    path, media = jobs.pipeline(url, stage, threading.Event(), time.monotonic() + 10)
    jobs.fail_operation(
        saved["id"], old.operation_id, engine.RetrievalFailure("interrupted", "Stopped")
    )
    new, _ = jobs.begin_operation(saved["id"])
    # begin_operation removed the old owned staging; simulate a late completion with its own file.
    stage.mkdir()
    path.write_bytes(b"late")
    with pytest.raises(engine.RetrievalFailure):
        jobs.commit_media(
            saved["id"], old.operation_id, path, media, threading.Event(), time.monotonic() + 10
        )
    assert jobs.get_operation(saved["id"]).operation_id == new.operation_id
    projects.delete_project(saved["id"])
    from references import ReferenceError

    with pytest.raises(ReferenceError):
        jobs.commit_media(
            saved["id"], old.operation_id, path, media, threading.Event(), time.monotonic() + 10
        )
    assert client.get(f"/api/projects/{saved['id']}").status_code == 404
    jobs.clean_stage(old.operation_id)


def test_migration_and_interrupted_recovery_preserve_clip(local):
    client, directory, _ = local
    saved = create(client)
    assert upload(client, saved["id"]).status_code == 200
    with projects.database() as connection:
        connection.execute("DROP TABLE reference_operations")
        connection.execute("PRAGMA user_version = 1")
    projects.initialize()
    with projects.database() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 10
    operation, url = jobs.begin_operation(saved["id"])
    stage = jobs.staging(operation.operation_id)
    stage.mkdir()
    (stage / "abandoned.part").write_bytes(b"partial")
    target = jobs.destination(saved["id"], operation.operation_id)
    target.write_bytes(b"uncommitted")
    orphan = directory / "reference-staging" / str(uuid.uuid4())
    orphan.mkdir()
    unrelated = directory / "reference-staging" / "unrelated"
    unrelated.mkdir()
    jobs.recover()
    assert jobs.get_operation(saved["id"]).failure_code == "interrupted"
    assert not stage.exists() and not target.exists() and not orphan.exists()
    assert unrelated.exists()
    assert client.get(f"/api/projects/{saved['id']}").json()["clip_status"] == "ready"


def test_restart_detects_same_size_corruption(local):
    client, _, _ = local
    saved = create(client)
    client.post(endpoint(saved))
    result = finished(client, endpoint(saved))
    target = jobs.destination(saved["id"], result["operation_id"])
    target.write_bytes(b"x" * result["media"]["size_bytes"])
    jobs.recover()
    assert client.get(endpoint(saved)).json()["failure_code"] == "media_unavailable"


def test_shutdown_interrupts_and_joins_owned_process(local, monkeypatch):
    client, directory, _ = local
    saved = create(client)
    entered = threading.Event()
    # Keep the real engine wrapper so its thread-local cancellation reaches run_command.
    monkeypatch.setattr(engine, "retrieve_media", ORIGINAL_RETRIEVE)
    monkeypatch.setattr(engine.shutil, "which", lambda name: name)

    def helper(args, stage, name, deadline, limit):
        entered.set()
        return REAL_COMMAND(
            [sys.executable, "-c", "import time; time.sleep(30)"], stage, name, deadline, limit
        )

    monkeypatch.setattr(engine, "run_command", helper)
    client.post(endpoint(saved))
    assert entered.wait(2)
    client.portal.call(jobs.shutdown)
    assert jobs.active is None
    assert client.get(endpoint(saved)).json()["failure_code"] == "interrupted"
    assert list((directory / "reference-staging").iterdir()) == []


ORIGINAL_RETRIEVE = engine.retrieve_media
REAL_COMMAND = engine.run_command


def test_cancelled_start_still_establishes_worker_ownership(local, monkeypatch):
    client, _, _ = local
    saved = create(client)
    entered, release = threading.Event(), threading.Event()
    original = jobs.begin_operation

    def delayed(project_id):
        result = original(project_id)
        entered.set()
        assert release.wait(3)
        return result

    monkeypatch.setattr(jobs, "begin_operation", delayed)

    async def cancel_start():
        task = asyncio.create_task(jobs.start(saved["id"]))
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        if jobs.active:
            await jobs.active[3]
        assert jobs.get_operation(saved["id"]).status == "ready"

    client.portal.call(cancel_start)
