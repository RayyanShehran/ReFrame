import asyncio
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import starlette.formparsers
from fastapi.testclient import TestClient
from starlette.requests import Request

import clips
from main import app

client = TestClient(app)
MP4 = {
    "format": {
        "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
        "duration": "1.25",
        "tags": {"major_brand": "isom"},
    },
    "streams": [
        {
            "codec_type": "video",
            "codec_name": "h264",
            "width": 320,
            "height": 240,
            "avg_frame_rate": "30/1",
            "disposition": {"attached_pic": 0},
        },
        {"codec_type": "audio", "codec_name": "aac"},
    ],
}
MOV = {**MP4, "format": {**MP4["format"], "tags": {"major_brand": "qt  "}}}


def upload(content=b"video", filename="clip.mp4", mime="video/mp4", **kwargs):
    return client.post("/api/clips/inspect", files={"file": (filename, content, mime)}, **kwargs)


def fake_probe(monkeypatch, data=MP4):
    async def inspect(path, extension, filename, size):
        assert path.parent == clips.DATA_DIR
        assert path.name.startswith("clip-")
        assert path.is_file()
        return clips.parse_probe(json.dumps(data).encode(), extension, filename, size)

    monkeypatch.setattr(clips, "probe_file", inspect)


@pytest.mark.parametrize(
    "filename,mime,data",
    [
        ("clip.mp4", "video/mp4", MP4),
        ("clip.mov", "video/quicktime", MOV),
        ("clip.mp4", "application/octet-stream", MP4),
    ],
)
def test_valid_upload_and_cleanup(monkeypatch, tmp_path, filename, mime, data):
    monkeypatch.setattr(clips, "DATA_DIR", tmp_path)
    fake_probe(monkeypatch, data)
    response = upload(b"small video", "folder/" + filename, mime)
    assert response.status_code == 200
    assert response.json() == {
        "filename": filename,
        "size_bytes": 11,
        "duration_seconds": 1.25,
        "width": 320,
        "height": 240,
        "video_codec": "h264",
        "has_audio": True,
        "audio_codec": "aac",
        "frame_rate": 30.0,
        "validation_status": "accepted",
        "storage_status": "not_retained",
    }
    assert list(tmp_path.iterdir()) == []


def test_audio_absent_and_unknown_rate():
    data = {**MP4, "streams": [{**MP4["streams"][0], "avg_frame_rate": "0/0"}]}
    details = clips.parse_probe(json.dumps(data).encode(), ".mp4", "clip.mp4", 20)
    assert details.has_audio is False
    assert details.audio_codec is None
    assert details.frame_rate is None


def test_oversized_secondary_video_stream_is_rejected():
    data = {**MP4, "streams": [MP4["streams"][0], {**MP4["streams"][0], "width": 4097}]}
    with pytest.raises(clips.ReferenceError) as error:
        clips.parse_probe(json.dumps(data).encode(), ".mp4", "clip.mp4", 20)
    assert error.value.code == "dimension_limit"


@pytest.mark.parametrize(
    "filename,mime",
    [
        ("clip.avi", "video/x-msvideo"),
        ("clip.mp4", "video/quicktime"),
        ("clip.mov", "video/mp4"),
        ("clip.mp4", "text/plain"),
    ],
)
def test_extension_and_mime_rejection(filename, mime):
    response = upload(b"data", filename, mime)
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_media"


@pytest.mark.parametrize(
    "data,extension,status,code",
    [
        (MOV, ".mp4", 415, "unsupported_media"),
        (MP4, ".mov", 415, "unsupported_media"),
        (
            {**MP4, "format": {**MP4["format"], "format_name": "matroska"}},
            ".mp4",
            415,
            "unsupported_media",
        ),
        (
            {**MP4, "format": {**MP4["format"], "tags": {"major_brand": "3gp4"}}},
            ".mp4",
            415,
            "unsupported_media",
        ),
        ({**MP4, "format": {**MP4["format"], "duration": "121"}}, ".mp4", 422, "duration_limit"),
        ({**MP4, "format": {**MP4["format"], "duration": "nan"}}, ".mp4", 422, "invalid_metadata"),
        (
            {**MP4, "streams": [{**MP4["streams"][0], "width": 4097}]},
            ".mp4",
            422,
            "dimension_limit",
        ),
        ({**MP4, "streams": [{**MP4["streams"][0], "height": 0}]}, ".mp4", 422, "dimension_limit"),
        (
            {**MP4, "streams": [{**MP4["streams"][0], "disposition": {"attached_pic": 1}}]},
            ".mp4",
            415,
            "unsupported_media",
        ),
        (
            {**MP4, "streams": [{"codec_type": "audio", "codec_name": "aac"}]},
            ".mp4",
            415,
            "unsupported_media",
        ),
        (
            {**MP4, "streams": [{**MP4["streams"][0], "codec_name": "!!!"}]},
            ".mp4",
            422,
            "invalid_metadata",
        ),
        (
            {**MP4, "streams": [{**MP4["streams"][0], "disposition": None}]},
            ".mp4",
            415,
            "unsupported_media",
        ),
        (
            {**MP4, "streams": [MP4["streams"][0], {"codec_type": "audio"}]},
            ".mp4",
            422,
            "invalid_metadata",
        ),
    ],
)
def test_probe_metadata_rejections(data, extension, status, code):
    with pytest.raises(clips.ReferenceError) as error:
        clips.parse_probe(json.dumps(data).encode(), extension, "clip" + extension, 10)
    assert (error.value.status_code, error.value.code) == (status, code)


@pytest.mark.parametrize("raw", [b"", b"not-json", b"[]", b'{"format":{},"streams":{}}'])
def test_malformed_probe_output(raw):
    with pytest.raises(clips.ReferenceError) as error:
        clips.parse_probe(raw, ".mp4", "clip.mp4", 10)
    assert error.value.code == "invalid_metadata"


def test_malformed_container_brand_is_safe():
    data = {**MP4, "format": {**MP4["format"], "tags": {"major_brand": []}}}
    with pytest.raises(clips.ReferenceError) as error:
        clips.parse_probe(json.dumps(data).encode(), ".mp4", "clip.mp4", 10)
    assert error.value.status_code == 415


def test_truncated_multipart_closes_unfinished_spool(monkeypatch, tmp_path):
    monkeypatch.setattr(clips, "DATA_DIR", tmp_path)
    real_spool = clips.SpooledTemporaryFile
    spools = []

    def spool_factory(**kwargs):
        assert kwargs["dir"] == tmp_path
        spool = real_spool(**kwargs)
        spools.append(spool)
        return spool

    monkeypatch.setattr(clips, "SpooledTemporaryFile", spool_factory)
    body = (
        b'--boundary\r\nContent-Disposition: form-data; name="file"; filename="clip.mp4"\r\n'
        b"Content-Type: video/mp4\r\n\r\nunfinished data"
    )
    response = client.post(
        "/api/clips/inspect",
        content=body,
        headers={"content-type": "multipart/form-data; boundary=boundary"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_multipart"
    assert spools and all(spool.closed for spool in spools)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "body,opens_spool",
    [
        (b"--wrong\r\n", False),
        (b"--boundary\r\nBad Header: x\r\n\r\nx\r\n--boundary--\r\n", False),
        (
            b'--boundary\r\nContent-Disposition: form-data; name="file"; filename="clip.mp4"\r\n'
            b"Content-Type: video/mp4\r\n\r\nvideo\r\n--boundary\r\n"
            b"Bad Header: x\r\n\r\nx\r\n--boundary--\r\n",
            True,
        ),
    ],
    ids=["wrong-opening-boundary", "invalid-header", "invalid-header-after-file"],
)
def test_multipart_syntax_failure_cleanup_and_retry(monkeypatch, tmp_path, body, opens_spool):
    monkeypatch.setattr(clips, "DATA_DIR", tmp_path)
    real_spool = clips.SpooledTemporaryFile
    spools = []

    def spool_factory(*args, **kwargs):
        spool = real_spool(*args, **kwargs)
        spools.append(spool)
        return spool

    monkeypatch.setattr(clips, "SpooledTemporaryFile", spool_factory)
    monkeypatch.setattr(starlette.formparsers, "SpooledTemporaryFile", spool_factory)
    response = client.post(
        "/api/clips/inspect",
        content=body,
        headers={"content-type": "multipart/form-data; boundary=boundary"},
    )
    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "invalid_multipart",
            "message": "Send exactly one video file in the file field.",
        }
    }
    assert bool(spools) is opens_spool
    assert all(spool.closed for spool in spools)
    assert list(tmp_path.iterdir()) == []
    assert not clips.inspection_lock.locked()

    fake_probe(monkeypatch)
    assert upload().status_code == 200
    assert all(spool.closed for spool in spools)
    assert list(tmp_path.iterdir()) == []
    assert not clips.inspection_lock.locked()


def test_empty_file_and_cleanup(monkeypatch, tmp_path):
    monkeypatch.setattr(clips, "DATA_DIR", tmp_path)
    response = upload(b"")
    assert response.status_code == 415
    assert list(tmp_path.iterdir()) == []


def test_file_limit(monkeypatch, tmp_path):
    monkeypatch.setattr(clips, "DATA_DIR", tmp_path)
    monkeypatch.setattr(clips, "MAX_FILE", 3)
    response = upload(b"1234")
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "file_too_large"
    assert list(tmp_path.iterdir()) == []


def test_request_limit_without_content_length(monkeypatch, tmp_path):
    monkeypatch.setattr(clips, "DATA_DIR", tmp_path)
    monkeypatch.setattr(clips, "MAX_REQUEST", 128)
    request = client.build_request(
        "POST", "/api/clips/inspect", files={"file": ("clip.mp4", b"x" * 100, "video/mp4")}
    )
    request.headers.pop("content-length", None)
    response = client.send(request)
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"
    assert list(tmp_path.iterdir()) == []


def test_misleading_content_length_is_bounded(monkeypatch, tmp_path):
    monkeypatch.setattr(clips, "DATA_DIR", tmp_path)
    monkeypatch.setattr(clips, "MAX_REQUEST", 128)
    response = upload(b"x" * 100, headers={"content-length": "1"})
    assert response.status_code == 413
    assert list(tmp_path.iterdir()) == []


def test_extra_fields_and_files():
    extra_field = client.post(
        "/api/clips/inspect", files={"file": ("clip.mp4", b"x", "video/mp4")}, data={"other": "x"}
    )
    two_files = client.post(
        "/api/clips/inspect",
        files=[("file", ("one.mp4", b"x", "video/mp4")), ("file", ("two.mp4", b"x", "video/mp4"))],
    )
    missing = client.post("/api/clips/inspect", data={"foo": "bar"})
    for response in (extra_field, two_files, missing):
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_multipart"


@pytest.mark.parametrize("failure", [FileNotFoundError, PermissionError])
def test_missing_ffprobe(monkeypatch, tmp_path, failure):
    monkeypatch.setattr(clips, "DATA_DIR", tmp_path)

    async def absent(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(clips.asyncio, "create_subprocess_exec", absent)
    response = upload(b"data")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "inspector_unavailable"
    assert list(tmp_path.iterdir()) == []


def test_busy_rejected_without_queue(monkeypatch, tmp_path):
    monkeypatch.setattr(clips, "DATA_DIR", tmp_path)

    async def exercise():
        await clips.inspection_lock.acquire()
        try:
            scope = {"type": "http", "method": "POST", "path": "/api/clips/inspect", "headers": []}
            request = Request(scope)
            with pytest.raises(clips.ReferenceError) as error:
                await clips.inspect_clip(request)
            assert error.value.code == "inspection_busy"
        finally:
            clips.inspection_lock.release()

    asyncio.run(exercise())


def test_probe_timeout_kills_and_reaps(monkeypatch, tmp_path):
    monkeypatch.setattr(clips, "PROBE_SECONDS", 0.01)
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"x")

    real_spawn = asyncio.create_subprocess_exec

    async def spawn(*_args, **_kwargs):
        return await real_spawn(
            sys.executable,
            "-c",
            "import time; time.sleep(30)",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )

    monkeypatch.setattr(clips.asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(clips.ReferenceError) as error:
        asyncio.run(clips.probe_file(path, ".mp4", "clip.mp4", 1))
    assert error.value.code == "inspection_timeout"


def test_probe_output_limit_kills_process(monkeypatch, tmp_path):
    class FakeProcess:
        returncode = None
        killed = False
        waited = False

        class Output:
            async def read(self, _size):
                return b"x" * 4096

        stdout = Output()

        def kill(self):
            self.killed = True
            self.returncode = -9

        async def wait(self):
            self.waited = True
            return self.returncode

    process = FakeProcess()

    async def spawn(*_args, **_kwargs):
        return process

    monkeypatch.setattr(clips.asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(clips.ReferenceError) as error:
        asyncio.run(clips.probe_file(tmp_path / "clip.mp4", ".mp4", "clip.mp4", 10))
    assert error.value.code == "invalid_metadata"
    assert process.killed and process.waited


def test_probe_cancellation_kills_and_reaps(monkeypatch, tmp_path):
    entered = asyncio.Event()

    class FakeProcess:
        returncode = None
        killed = False
        waited = False

        class Output:
            async def read(self, _size):
                entered.set()
                await asyncio.Event().wait()

        stdout = Output()

        def kill(self):
            self.killed = True
            self.returncode = -9

        async def wait(self):
            self.waited = True
            return self.returncode

    process = FakeProcess()

    async def spawn(*_args, **_kwargs):
        return process

    monkeypatch.setattr(clips.asyncio, "create_subprocess_exec", spawn)

    async def exercise():
        task = asyncio.create_task(clips.probe_file(tmp_path / "clip.mp4", ".mp4", "clip.mp4", 10))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())
    assert process.killed and process.waited


def test_cancellation_removes_temp_file(monkeypatch, tmp_path):
    monkeypatch.setattr(clips, "DATA_DIR", tmp_path)
    entered = asyncio.Event()

    async def blocked(*_args):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(clips, "probe_file", blocked)

    async def exercise():
        body = (
            b'--boundary\r\nContent-Disposition: form-data; name="file"; filename="clip.mp4"\r\n'
            b"Content-Type: video/mp4\r\n\r\nvideo\r\n--boundary--\r\n"
        )
        sent = False

        async def receive():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.request", "body": b"", "more_body": False}

        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/clips/inspect",
                "headers": [(b"content-type", b"multipart/form-data; boundary=boundary")],
            },
            receive,
        )
        task = asyncio.create_task(clips.inspect_clip(request))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert list(tmp_path.iterdir()) == []
        assert not clips.inspection_lock.locked()

    asyncio.run(exercise())


def test_request_deadline_releases_slot(monkeypatch):
    monkeypatch.setattr(clips, "REQUEST_SECONDS", 0.01)

    async def receive():
        await asyncio.sleep(1)
        return {"type": "http.request", "body": b"", "more_body": False}

    async def exercise():
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/clips/inspect",
                "headers": [(b"content-type", b"multipart/form-data; boundary=x")],
            },
            receive,
        )
        with pytest.raises(clips.ReferenceError) as error:
            await clips.inspect_clip(request)
        assert error.value.code == "inspection_timeout"
        assert not clips.inspection_lock.locked()

    asyncio.run(exercise())


def test_cleanup_failure_is_reported(monkeypatch, tmp_path):
    monkeypatch.setattr(clips, "DATA_DIR", tmp_path)
    fake_probe(monkeypatch)
    real_unlink = Path.unlink

    def fail_clip(path, *args, **kwargs):
        if path.name.startswith("clip-"):
            raise OSError("secret")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_clip)
    response = upload(b"video")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "cleanup_failure"


def test_startup_cleanup_scoped(monkeypatch, tmp_path):
    monkeypatch.setattr(clips, "DATA_DIR", tmp_path)
    stale = tmp_path / "clip-old.mp4"
    unrelated = tmp_path / "other.mp4"
    stale.write_bytes(b"old")
    unrelated.write_bytes(b"keep")
    clips.cleanup_stale_files()
    assert not stale.exists()
    assert unrelated.exists()


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg tools unavailable"
)
@pytest.mark.parametrize(
    "extension,format_name,mime", [(".mp4", "mp4", "video/mp4"), (".mov", "mov", "video/quicktime")]
)
def test_real_ffprobe_generated_fixture(monkeypatch, tmp_path, extension, format_name, mime):
    inspection_dir = tmp_path / "inspection"
    monkeypatch.setattr(clips, "DATA_DIR", inspection_dir)
    path = tmp_path / ("fixture" + extension)
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
            "-f",
            format_name,
            str(path),
        ],
        check=True,
        timeout=15,
    )
    response = upload(path.read_bytes(), path.name, mime)
    assert response.status_code == 200
    details = clips.ClipDetails(**response.json())
    assert details.width == 32 and details.height == 24
    assert details.video_codec == "mpeg4" and details.has_audio is False
    assert details.duration_seconds == 1.0
    assert list(inspection_dir.iterdir()) == []


@pytest.mark.skipif(not shutil.which("ffprobe"), reason="FFprobe unavailable")
def test_real_ffprobe_rejects_corrupt_file(tmp_path):
    path = tmp_path / "corrupt.mp4"
    path.write_bytes(b"not a video")
    with pytest.raises(clips.ReferenceError) as error:
        asyncio.run(clips.probe_file(path, ".mp4", path.name, path.stat().st_size))
    assert error.value.status_code == 415
