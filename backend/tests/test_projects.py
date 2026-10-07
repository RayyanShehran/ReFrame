import asyncio
import hashlib
import json
import shutil
import sqlite3
import subprocess
import threading
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

import clips
import main
import projects
from references import ReferenceDetails, canonicalize_url
from tests.test_clips import MP4

URL = "https://www.tiktok.com/@scout2015/video/6718335390845095173"
REAL_PROBE = clips.probe_file


@pytest.fixture
def local(monkeypatch, tmp_path):
    monkeypatch.setattr(projects, "DATA_DIR", tmp_path)
    monkeypatch.setattr(clips, "DATA_DIR", tmp_path / "temporary")
    monkeypatch.setattr(projects, "operation_lock", asyncio.Lock())
    monkeypatch.setattr(clips, "inspection_lock", asyncio.Lock())
    calls = []

    async def reference(url):
        calls.append(url)
        canonical, video_id = canonicalize_url(url)
        return ReferenceDetails(
            canonical_url=canonical, video_id=video_id, title="Snapshot", author_name="Scout"
        )

    async def probe(path, extension, filename, size):
        return clips.parse_probe(json.dumps(MP4).encode(), extension, filename, size)

    monkeypatch.setattr(projects, "inspect_reference", reference)
    monkeypatch.setattr(clips, "probe_file", probe)
    with TestClient(main.app) as client:
        yield client, tmp_path, calls


def create(client, name="My project"):
    response = client.post("/api/projects", json={"name": name, "reference_url": URL})
    assert response.status_code == 201, response.text
    return response.json()


def upload(client, project_id, content=b"video"):
    return client.post(
        f"/api/projects/{project_id}/clip", files={"file": ("clip.mp4", content, "video/mp4")}
    )


def test_create_list_validate_snapshot_and_restart(local, monkeypatch):
    client, directory, calls = local
    first = create(client, "  First  ")
    second = create(client, "Second")
    assert first["name"] == "First"
    assert first["reference"]["video_id"] == "6718335390845095173"
    assert first["reference_inspected_at"].endswith("+00:00")
    assert [row["id"] for row in client.get("/api/projects").json()] == [second["id"], first["id"]]
    for name in ["", "   ", "x" * 81]:
        assert (
            client.post("/api/projects", json={"name": name, "reference_url": URL}).status_code
            == 422
        )
    assert (
        client.post(
            "/api/projects", json={"name": "Bad", "reference_url": "https://example.com"}
        ).status_code
        == 422
    )
    assert upload(client, first["id"]).status_code == 200
    retained = client.get(f"/api/projects/{first['id']}").json()
    assert retained["clip_status"] == "ready"
    assert retained["clip"]["storage_status"] == "retained"
    assert retained["clip"]["sha256"] == hashlib.sha256(b"video").hexdigest()
    assert "path" not in json.dumps(retained)
    assert list((directory / "project-staging").iterdir()) == []
    assert len(list((directory / "projects" / first["id"]).iterdir())) == 1
    assert upload(client, first["id"]).status_code == 409

    async def offline(_url):
        raise AssertionError("Reopening must not contact TikTok")

    monkeypatch.setattr(projects, "inspect_reference", offline)
    # A distinct FastAPI application instance with the same lifespan/routes/storage.
    from fastapi import FastAPI

    restarted = FastAPI(lifespan=main.lifespan)
    restarted.router.routes.extend(main.app.router.routes)
    with TestClient(restarted) as reopened:
        assert reopened.get(f"/api/projects/{first['id']}").json() == retained
    assert calls == [URL, URL, "https://example.com"]


def test_project_limit(local):
    client, _, _ = local
    for index in range(10):
        create(client, str(index))
    response = client.post("/api/projects", json={"name": "Eleven", "reference_url": URL})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "project_limit"


def test_rejection_and_temporary_endpoint(local):
    client, directory, _ = local
    saved = create(client)
    assert upload(client, saved["id"], b"").status_code == 415
    assert client.get(f"/api/projects/{saved['id']}").json()["clip_status"] == "empty"
    assert list((directory / "project-staging").iterdir()) == []
    assert upload(client, saved["id"]).status_code == 200
    response = client.post("/api/clips/inspect", files={"file": ("temp.mp4", b"data", "video/mp4")})
    assert response.status_code == 200
    assert response.json()["storage_status"] == "not_retained"
    assert list((directory / "temporary").iterdir()) == []


def test_move_failure_can_retry(local, monkeypatch):
    client, directory, _ = local
    saved = create(client)
    with monkeypatch.context() as patch:

        def fail(*_args):
            raise OSError("private path")

        patch.setattr(Path, "replace", fail)
        response = upload(client, saved["id"])
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "storage_failure"
    assert client.get(f"/api/projects/{saved['id']}").json()["clip_status"] == "empty"
    assert list((directory / "project-staging").iterdir()) == []
    assert upload(client, saved["id"]).status_code == 200


def test_database_ready_commit_failure_compensates(local):
    client, directory, _ = local
    saved = create(client)
    with projects.database() as connection:
        connection.execute(
            "CREATE TRIGGER fail_ready BEFORE UPDATE OF state ON clips WHEN NEW.state = 'ready' "
            "BEGIN SELECT RAISE(FAIL, 'injected commit failure'); END"
        )
    response = upload(client, saved["id"])
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "storage_failure"
    assert client.get(f"/api/projects/{saved['id']}").json()["clip_status"] == "empty"
    assert list((directory / "projects" / saved["id"]).iterdir()) == []
    assert list((directory / "project-staging").iterdir()) == []
    with projects.database() as connection:
        connection.execute("DROP TRIGGER fail_ready")
    assert upload(client, saved["id"]).status_code == 200


def test_restart_reconciles_interrupted_and_missing_media(local, monkeypatch):
    client, directory, _ = local
    pending = create(client, "Pending")
    projects.begin_upload(pending["id"])
    staged = directory / "project-staging" / f"clip-{uuid.uuid4().hex}.mp4"
    staged.write_bytes(b"abandoned")
    committed = create(client, "Interrupted move")
    projects.begin_upload(committed["id"])
    path = directory / "project-staging" / f"clip-{uuid.uuid4().hex}.mp4"
    path.write_bytes(b"video")
    details = clips.parse_probe(json.dumps(MP4).encode(), ".mp4", "clip.mp4", 5)
    with monkeypatch.context() as patch:

        def crash(_id):
            raise SystemExit("simulated process exit after move")

        patch.setattr(projects, "mark_ready", crash)
        with pytest.raises(SystemExit):
            projects.retain_clip(
                committed["id"], path, details, hashlib.sha256(b"video").hexdigest()
            )
    assert projects.get_project(committed["id"]).clip_status == "validated"
    projects.initialize()
    assert not staged.exists()
    assert projects.get_project(pending["id"]).clip_status == "empty"
    assert projects.get_project(committed["id"]).clip_status == "ready"
    media = next((directory / "projects" / committed["id"]).iterdir())
    media.write_bytes(b"wrong")  # Same size: startup must verify the digest.
    projects.initialize()
    assert projects.get_project(committed["id"]).clip_status == "unavailable"
    assert projects.get_project(committed["id"]).clip is None
    media.unlink()
    projects.initialize()
    assert projects.get_project(committed["id"]).clip_status == "unavailable"


def test_delete_failure_and_retry(local, monkeypatch):
    client, directory, _ = local
    saved = create(client)
    assert upload(client, saved["id"]).status_code == 200
    with monkeypatch.context() as patch:

        def fail(_path):
            raise OSError("private path")

        patch.setattr(projects.shutil, "rmtree", fail)
        response = client.delete(f"/api/projects/{saved['id']}")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "cleanup_failure"
    assert projects.get_project(saved["id"]).status == "deleting"
    assert (directory / "projects" / saved["id"]).exists()
    projects.initialize()
    assert projects.get_project(saved["id"]).status == "deleting"
    assert client.delete(f"/api/projects/{saved['id']}").status_code == 204
    assert not (directory / "projects" / saved["id"]).exists()
    assert client.get(f"/api/projects/{saved['id']}").status_code == 404


@pytest.mark.parametrize(
    "bad", ["not-a-uuid", "..", "%2e%2e", "%2e%2e%2fsecret", str(uuid.uuid4()).upper()]
)
def test_invalid_ids_and_paths(local, bad):
    client, directory, _ = local
    for method, suffix in [("GET", ""), ("DELETE", ""), ("POST", "/clip")]:
        response = client.request(method, f"/api/projects/{bad}{suffix}")
        assert response.status_code in {404, 422}
    assert list((directory / "projects").iterdir()) == []


def test_upload_delete_conflict_serializes_without_resurrection(local, monkeypatch):
    client, directory, _ = local
    saved = create(client)

    async def exercise():
        entered, release = asyncio.Event(), asyncio.Event()

        async def probe(path, extension, filename, size):
            entered.set()
            await release.wait()
            return clips.parse_probe(json.dumps(MP4).encode(), extension, filename, size)

        monkeypatch.setattr(clips, "probe_file", probe)
        body = (
            b'--b\r\nContent-Disposition: form-data; name="file"; filename="clip.mp4"\r\n'
            b"Content-Type: video/mp4\r\n\r\nvideo\r\n--b--\r\n"
        )

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/",
                "headers": [(b"content-type", b"multipart/form-data; boundary=b")],
            },
            receive,
        )
        upload_task = asyncio.create_task(projects.upload_clip(saved["id"], request))
        await entered.wait()
        delete_task = asyncio.create_task(projects.remove_project(saved["id"]))
        await asyncio.sleep(0)
        assert not delete_task.done()
        release.set()
        assert (await upload_task).clip_status == "ready"
        await delete_task
        with pytest.raises(projects.ReferenceError):
            projects.get_project(saved["id"])

    asyncio.run(exercise())
    assert not (directory / "projects" / saved["id"]).exists()
    assert not clips.inspection_lock.locked()
    assert not projects.operation_lock.locked()


def test_cancellation_during_validation_removes_staging(local, monkeypatch):
    client, directory, _ = local
    saved = create(client)

    async def exercise():
        entered = asyncio.Event()

        async def probe(*_args):
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(clips, "probe_file", probe)
        body = (
            b'--b\r\nContent-Disposition: form-data; name="file"; filename="clip.mp4"\r\n'
            b"Content-Type: video/mp4\r\n\r\nvideo\r\n--b--\r\n"
        )

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/",
                "headers": [(b"content-type", b"multipart/form-data; boundary=b")],
            },
            receive,
        )
        task = asyncio.create_task(projects.upload_clip(saved["id"], request))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert projects.get_project(saved["id"]).clip_status == "empty"
        assert list((directory / "project-staging").iterdir()) == []
        assert not clips.inspection_lock.locked()
        assert not projects.operation_lock.locked()

    asyncio.run(exercise())


def test_thread_cancellation_joins_before_releasing_ownership():
    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    def worker():
        started.set()
        assert release.wait(5)
        finished.set()

    async def exercise():
        task = asyncio.create_task(clips.finish_thread(worker))
        try:
            assert await asyncio.to_thread(started.wait, 5)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()  # Repeated cancellation must not abandon the worker.
            await asyncio.sleep(0)
            assert not task.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set()

    asyncio.run(exercise())


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg tools unavailable"
)
def test_real_retained_fixture_restart_and_delete(local, monkeypatch):
    client, directory, _ = local
    monkeypatch.setattr(clips, "probe_file", REAL_PROBE)
    source = directory / "generated.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=32x24:r=10",
            "-t",
            "1",
            "-c:v",
            "mpeg4",
            str(source),
        ],
        check=True,
        timeout=15,
    )
    saved = create(client)
    result = upload(client, saved["id"], source.read_bytes())
    assert result.status_code == 200
    assert result.json()["clip"]["width"] == 32
    assert result.json()["clip"]["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    projects.initialize()
    assert projects.get_project(saved["id"]).clip_status == "ready"
    assert client.delete(f"/api/projects/{saved['id']}").status_code == 204
    assert not (directory / "projects" / saved["id"]).exists()
    assert client.get(f"/api/projects/{saved['id']}").status_code == 404


def test_schema_version_and_foreign_keys(local):
    with projects.database() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 19
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO clips(project_id, state) VALUES (?, 'staging')", (str(uuid.uuid4()),)
            )
    with projects.database() as connection:
        connection.execute("PRAGMA user_version = 999")
    with pytest.raises(RuntimeError, match="Unsupported"):
        projects.initialize()


def test_actual_sqlite_commit_failure_does_not_leave_ready_clip(local, monkeypatch):
    client, directory, _ = local
    saved = create(client)
    real_connect = sqlite3.connect
    failed = False

    class FailingCommit(sqlite3.Connection):
        ready = False

        def execute(self, sql, parameters=()):
            nonlocal failed
            if sql.startswith("UPDATE clips SET state = 'ready'"):
                self.ready = True
            if sql == "COMMIT" and self.ready and not failed:
                failed = True
                raise sqlite3.OperationalError("injected COMMIT failure")
            return super().execute(sql, parameters)

    def connect(*args, **kwargs):
        return real_connect(*args, **kwargs, factory=FailingCommit)

    with monkeypatch.context() as patch:
        patch.setattr(projects.sqlite3, "connect", connect)
        response = upload(client, saved["id"])
    assert failed
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "storage_failure"
    assert projects.get_project(saved["id"]).clip_status == "empty"
    assert list((directory / "project-staging").iterdir()) == []
    assert list((directory / "projects" / saved["id"]).iterdir()) == []
    assert upload(client, saved["id"]).status_code == 200
