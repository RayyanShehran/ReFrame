import asyncio
import hashlib
import shutil
import subprocess
import sys
import threading
import time

import pytest
from pydantic import ValidationError

import color_analysis as color
import projects
import reference_engine as engine
import reference_jobs as jobs
from references import ReferenceError
from tests.test_projects import create, upload
from tests.test_projects import local as project_fixture


@pytest.fixture
def local(monkeypatch, tmp_path):
    monkeypatch.setattr(jobs, "start_lock", asyncio.Lock())
    monkeypatch.setattr(jobs, "active", None)
    monkeypatch.setattr(jobs, "closing", set())
    yield from project_fixture.__wrapped__(monkeypatch, tmp_path)


def generated(path, colors=((255, 0, 0),), frames_per_color=12, tags=True, hdr=False):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg required for generated-media integration")
    args = [
        ffmpeg,
        "-v",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        "64x64",
        "-r",
        "12",
        "-i",
        "pipe:0",
        "-c:v",
        "libx264rgb",
        "-crf",
        "0",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "rgb24",
    ]
    if tags:
        args += [
            "-vf",
            f"setparams=color_primaries={'bt2020' if hdr else 'bt709'}:"
            f"color_trc={'smpte2084' if hdr else 'iec61966-2-1'}:colorspace=gbr:range=full",
            "-color_primaries",
            "bt2020" if hdr else "bt709",
            "-color_trc",
            "smpte2084" if hdr else "iec61966-2-1",
            "-colorspace",
            "rgb",
            "-color_range",
            "pc",
        ]
    raw = b"".join(bytes(rgb) * 64 * 64 * frames_per_color for rgb in colors)
    subprocess.run(args + [str(path)], input=raw, check=True, capture_output=True, timeout=10)
    return len(colors) * frames_per_color / 12


def seed(saved, file, duration):
    operation, _ = jobs.begin_operation(saved["id"])
    path = jobs.destination(saved["id"], operation.operation_id)
    path.parent.mkdir(exist_ok=True)
    shutil.copyfile(file, path)
    media = jobs.ReferenceMedia(
        size_bytes=path.stat().st_size,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        duration_seconds=duration,
        width=64,
        height=64,
        video_codec="h264",
        has_audio=False,
        audio_codec=None,
        decoded_frame_bytes=12288,
        decoded_audio_samples=0,
        retrieved_at=projects.now(),
        versions={"ffmpeg": "generated fixture"},
    )
    with projects.database() as connection:
        connection.execute(
            "UPDATE reference_operations SET state='ready', finished_at=?, filename=?, "
            "metadata=? WHERE project_id=?",
            (projects.now(), path.name, media.model_dump_json(), saved["id"]),
        )
    return path


def endpoint(saved):
    return f"/api/projects/{saved['id']}/style-blueprint"


def finish(client, url):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        data = client.get(url).json()
        if data["status"] != "running" and jobs.active is None:
            return data
        time.sleep(0.01)
    pytest.fail("Analysis did not finish")


@pytest.mark.parametrize(
    "rgb", [(255, 0, 0), (0, 255, 0), (0, 0, 255), (128, 128, 128), (0, 0, 0), (255, 255, 255)]
)
def test_real_solid_colors_and_grayscale(tmp_path, rgb):
    path = tmp_path / "source.mkv"
    duration = generated(path, [rgb])
    stage = tmp_path / "stage"
    source = {
        "path": path,
        "reference_operation_id": "fixture",
        "identity": color.Source(
            video_id="123",
            canonical_url="https://www.tiktok.com/@fixture/video/123",
            media_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        ),
    }
    _, blueprint = color.pipeline(source, stage, threading.Event(), time.monotonic() + 10)
    assert blueprint.sampling.successful_samples == 12
    assert blueprint.sampling.duration_seconds == duration
    assert blueprint.sampling.decoded_bytes == 331776
    assert blueprint.color.rgb_mean == pytest.approx([v / 255 for v in rgb], abs=1 / 255)
    brightness = sum(w * v for w, v in zip([0.2126, 0.7152, 0.0722], rgb)) / 255
    assert blueprint.color.brightness_p50 == pytest.approx(brightness, abs=1 / 255)
    assert blueprint.color.contrast_spread == pytest.approx(0)
    assert blueprint.color.mean_hsv_saturation == pytest.approx(0 if len(set(rgb)) == 1 else 1)
    assert blueprint.color.palette[0].proportion == 1
    assert not blueprint.color_metadata.warnings
    assert blueprint.other_categories.audio == "not_analyzed"


def test_real_changing_color_samples_span_time(tmp_path):
    path = tmp_path / "changing.mkv"
    generated(path, [(255, 0, 0), (0, 255, 0), (0, 0, 255)])
    source = {
        "path": path,
        "identity": color.Source(
            video_id="123",
            canonical_url="https://www.tiktok.com/@fixture/video/123",
            media_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        ),
    }
    _, b = color.pipeline(source, tmp_path / "stage", threading.Event(), time.monotonic() + 10)
    assert b.sampling.timestamps_seconds == pytest.approx([0.125 + i * 0.25 for i in range(12)])
    assert b.color.rgb_mean == pytest.approx([1 / 3] * 3, abs=1 / 255)
    assert [c.hex for c in b.color.palette] == ["#0000ff", "#00ff00", "#ff0000"]
    assert [c.proportion for c in b.color.palette] == pytest.approx([1 / 3] * 3)
    assert b.color.palette_coverage == 1


def test_real_short_clip_and_missing_tags_warn(tmp_path):
    path = tmp_path / "short.mkv"
    generated(path, [(128, 128, 128)], frames_per_color=1, tags=False)
    source = {
        "path": path,
        "identity": color.Source(
            video_id="123",
            canonical_url="https://www.tiktok.com/@fixture/video/123",
            media_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        ),
    }
    _, b = color.pipeline(source, tmp_path / "stage", threading.Event(), time.monotonic() + 10)
    assert b.sampling.successful_samples == 12
    assert b.sampling.duration_seconds < 0.1
    assert b.color_metadata.warnings and "SDR" in b.color_metadata.warnings[0]
    assert b.color.mean_hsv_saturation == 0


def test_real_hdr_tags_rejected(tmp_path):
    path = tmp_path / "hdr.mkv"
    generated(path, hdr=True)
    source = {
        "path": path,
        "identity": color.Source(
            video_id="123",
            canonical_url="https://www.tiktok.com/@fixture/video/123",
            media_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        ),
    }
    with pytest.raises(engine.RetrievalFailure, match="SDR") as error:
        color.pipeline(source, tmp_path / "stage", threading.Event(), time.monotonic() + 10)
    assert error.value.code == "unsupported_color"


def test_palette_ties_coverage_and_interpolated_quantiles():
    # Six equally frequent distinct bins: only five shown, each proportion is 1/6.
    colors = [(0, 0, 0), (0, 0, 64), (0, 64, 0), (64, 0, 0), (128, 128, 128), (255, 255, 255)]
    raw = b"".join(bytes(rgb) * (color.OUTPUT_BYTES // 18) for rgb in colors)
    result = color.measure(raw)
    assert result.palette_coverage == pytest.approx(5 / 6)
    assert result.palette[0].hex == "#000000"
    assert result.brightness_p50 == pytest.approx((0.2126 * 64 / 255 + 0.7152 * 64 / 255) / 2)
    assert color.measure(raw).model_dump() == result.model_dump()
    # Put one transition at the exact p05 interpolation position.
    n = color.OUTPUT_BYTES // 3
    split = int((n - 1) * 0.05) + 1
    result = color.measure(bytes([0, 0, 0]) * split + bytes([255, 255, 255]) * (n - split))
    assert result.brightness_p05 == pytest.approx(((n - 1) * 0.05) % 1)


def test_schema_rejects_inconsistent_and_nonfinite_values():
    m = color.measure(bytes([128, 128, 128]) * (color.OUTPUT_BYTES // 3)).model_dump()
    for changes in [
        {"contrast_spread": 0.2},
        {"rgb_mean": [float("nan"), 0, 0]},
        {"palette_coverage": 0.2},
    ]:
        with pytest.raises(ValidationError):
            color.Measurements(**(m | changes))


def test_success_persistence_reuse_migration_and_source_invalidation(local, tmp_path, monkeypatch):
    client, directory, _ = local
    saved = create(client)
    assert upload(client, saved["id"]).status_code == 200
    file = tmp_path / "red.mkv"
    duration = generated(file)
    path = seed(saved, file, duration)
    with projects.database() as connection:
        connection.execute("DROP TABLE color_operations")
        connection.execute("PRAGMA user_version=2")
    projects.initialize()
    with projects.database() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 9
    assert jobs.get_operation(saved["id"]).status == "ready"
    assert projects.get_project(saved["id"]).clip_status == "ready"
    assert client.get(endpoint(saved)).json()["status"] == "idle"
    assert client.post(endpoint(saved)).status_code == 202
    result = finish(client, endpoint(saved))
    assert result["status"] == "ready", result
    assert result["blueprint"]["color"]["rgb_mean"] == [1, 0, 0]

    def forbidden(*args, **kwargs):
        raise AssertionError("No decoding or downloading when reusing")

    monkeypatch.setattr(engine, "retrieve_media", forbidden)
    monkeypatch.setattr(color, "pipeline", forbidden)
    assert client.post(endpoint(saved)).json()["operation_id"] == result["operation_id"]
    jobs.recover()
    color.recover()
    assert client.get(endpoint(saved)).json() == result
    assert projects.get_project(saved["id"]).clip_status == "ready"
    assert not list((directory / "color-staging").iterdir())
    contents = path.read_bytes()
    path.write_bytes(b"x" + contents[1:])
    invalid = client.get(endpoint(saved)).json()
    assert invalid["status"] == "failed" and invalid["failure_code"] == "source_changed"
    assert invalid["blueprint"] is None
    assert client.post(endpoint(saved)).status_code == 409


def prepared(local, tmp_path):
    client, _, _ = local
    saved = create(client)
    file = tmp_path / (saved["id"] + ".mkv")
    duration = generated(file)
    seed(saved, file, duration)
    return client, saved


def test_duplicate_and_both_contention_directions(local, tmp_path, monkeypatch):
    client, saved = prepared(local, tmp_path)
    other = create(client)
    entered, release = threading.Event(), threading.Event()
    real = color.pipeline

    def blocked(*args):
        entered.set()
        assert release.wait(5)
        return real(*args)

    monkeypatch.setattr(color, "pipeline", blocked)
    try:
        op = client.post(endpoint(saved)).json()
        assert entered.wait(2)
        assert client.post(endpoint(saved)).json()["operation_id"] == op["operation_id"]
        assert client.post(f"/api/projects/{other['id']}/reference-media").status_code == 503
        assert client.get("/health").status_code == 200
        assert client.get("/api/projects").status_code == 200
        assert not projects.operation_lock.locked()
    finally:
        release.set()
    assert finish(client, endpoint(saved))["status"] == "ready"
    # An active retrieval also blocks analysis on a project with ready reference media.
    with projects.database() as connection:
        connection.execute("DELETE FROM color_operations WHERE project_id=?", (saved["id"],))
    entered.clear()
    release.clear()

    def retrieval(*args):
        entered.set()
        assert release.wait(5)
        raise engine.RetrievalFailure("extraction_failure", "Mocked unavailable reference")

    monkeypatch.setattr(engine, "retrieve_media", retrieval)
    try:
        client.post(f"/api/projects/{other['id']}/reference-media")
        assert entered.wait(2)
        assert client.post(endpoint(saved)).status_code == 503
    finally:
        release.set()
    finish(client, f"/api/projects/{other['id']}/reference-media")


@pytest.mark.parametrize(
    "raw",
    [b"", b"x" * 100, color.OUTPUT_BYTES * b"x" + b"x"],
    ids=["empty", "truncated", "oversized"],
)
def test_empty_truncated_oversized_decode_and_explicit_retry(local, tmp_path, monkeypatch, raw):
    client, saved = prepared(local, tmp_path)
    real = engine.run_command

    def command(args, stage, name, deadline, limit, **kwargs):
        if name == "color-frames":
            return subprocess.CompletedProcess(args, 0, raw, "")
        return real(args, stage, name, deadline, limit, **kwargs)

    monkeypatch.setattr(engine, "run_command", command)
    op = client.post(endpoint(saved)).json()
    assert finish(client, endpoint(saved))["failure_code"] == "decode_failed"
    monkeypatch.setattr(engine, "run_command", real)
    new = client.post(endpoint(saved)).json()
    assert new["operation_id"] != op["operation_id"]
    assert finish(client, endpoint(saved))["status"] == "ready"


def test_actual_deadline_and_cleanup(local, tmp_path, monkeypatch):
    client, saved = prepared(local, tmp_path)
    real = engine.run_command

    def helper(args, stage, name, deadline, limit, **kwargs):
        return real(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            stage,
            name,
            time.monotonic() + 0.2,
            0.2,
        )

    monkeypatch.setattr(engine, "run_command", helper)
    client.post(endpoint(saved))
    assert finish(client, endpoint(saved))["failure_code"] == "deadline"
    assert not list((projects.DATA_DIR / "color-staging").iterdir())


@pytest.mark.parametrize("action", ["delete", "shutdown", "cancel"])
def test_owned_cancellation_and_join(local, tmp_path, monkeypatch, action):
    client, saved = prepared(local, tmp_path)
    entered = threading.Event()
    real = engine.run_command

    def helper(args, stage, name, deadline, limit, **kwargs):
        entered.set()
        return real(
            [sys.executable, "-c", "import time; time.sleep(30)"], stage, name, deadline, limit
        )

    monkeypatch.setattr(engine, "run_command", helper)
    client.post(endpoint(saved))
    assert entered.wait(2)
    if action == "delete":
        assert client.delete(f"/api/projects/{saved['id']}").status_code == 204
        assert client.get(endpoint(saved)).status_code == 404
    elif action == "shutdown":
        client.portal.call(jobs.shutdown)
        assert client.get(endpoint(saved)).json()["failure_code"] == "interrupted"
    else:

        async def cancel():
            task = jobs.active[3]
            task.cancel()
            await task

        client.portal.call(cancel)
        assert client.get(endpoint(saved)).json()["failure_code"] == "interrupted"
    assert jobs.active is None
    assert not list((projects.DATA_DIR / "color-staging").iterdir())


def test_recovery_stale_retry_deleted_and_algorithm_invalidation(local, tmp_path):
    client, saved = prepared(local, tmp_path)
    operation, source = color.begin_operation(saved["id"])
    current, b = color.pipeline(
        source, color.staging(operation.operation_id), threading.Event(), time.monotonic() + 10
    )
    color.recover()
    assert color.get_operation(saved["id"]).failure_code == "interrupted"
    newer, _ = color.begin_operation(saved["id"])
    with pytest.raises(engine.RetrievalFailure):
        color.commit(
            saved["id"],
            operation.operation_id,
            current,
            b,
            threading.Event(),
            time.monotonic() + 10,
        )
    assert color.get_operation(saved["id"]).operation_id == newer.operation_id
    color.commit(
        saved["id"], newer.operation_id, current, b, threading.Event(), time.monotonic() + 10
    )
    with projects.database() as connection:
        connection.execute(
            "UPDATE color_operations SET algorithm_version='old' WHERE project_id=?", (saved["id"],)
        )
    assert color.get_operation(saved["id"]).failure_code == "algorithm_changed"
    projects.delete_project(saved["id"])
    with pytest.raises(ReferenceError):
        color.commit(
            saved["id"], newer.operation_id, current, b, threading.Event(), time.monotonic() + 10
        )
    assert client.get(endpoint(saved)).status_code == 404


def test_uncertain_cleanup_quarantines_and_blocks_jobs_and_deletion(local, tmp_path, monkeypatch):
    client, saved = prepared(local, tmp_path)
    other = create(client)

    def failure(*args, **kwargs):
        raise engine.ProcessCleanupError

    monkeypatch.setattr(engine, "run_command", failure)
    client.post(endpoint(saved))
    result = finish(client, endpoint(saved))
    assert result["failure_code"] == "cleanup_failure"
    stage = color.staging(result["operation_id"])
    assert stage.exists()
    color.recover()
    assert stage.exists()
    assert client.post(endpoint(saved)).status_code == 500
    assert client.post(f"/api/projects/{other['id']}/reference-media").status_code == 500
    assert client.delete(f"/api/projects/{saved['id']}").status_code == 500


def test_cleanup_error_never_exposes_partial_blueprint(local, tmp_path, monkeypatch):
    client, saved = prepared(local, tmp_path)

    def fail(_):
        raise ReferenceError(500, "cleanup_failure", "Unable to remove staging")

    monkeypatch.setattr(color, "clean_stage", fail)
    client.post(endpoint(saved))
    result = finish(client, endpoint(saved))
    assert result["status"] == "failed" and result["failure_code"] == "cleanup_failure"
    assert result["blueprint"] is None


def test_missing_source_invalidates_ready_result(local, tmp_path):
    client, saved = prepared(local, tmp_path)
    client.post(endpoint(saved))
    assert finish(client, endpoint(saved))["status"] == "ready"
    reference = jobs.get_operation(saved["id"])
    jobs.destination(saved["id"], reference.operation_id).unlink()
    result = client.get(endpoint(saved)).json()
    assert result["status"] == "failed" and result["blueprint"] is None
    assert client.post(endpoint(saved)).status_code == 409


def test_ready_commit_failure_rolls_back_whole_blueprint(local, tmp_path):
    client, saved = prepared(local, tmp_path)
    with projects.database() as connection:
        connection.execute("""CREATE TRIGGER fail_color_commit
            BEFORE UPDATE OF state ON color_operations WHEN NEW.state='ready'
            BEGIN SELECT RAISE(ABORT, 'fixture commit failure'); END""")
    client.post(endpoint(saved))
    result = finish(client, endpoint(saved))
    assert result["status"] == "failed" and result["failure_code"] == "storage_failure"
    assert result["blueprint"] is None
    assert not list((projects.DATA_DIR / "color-staging").iterdir())
    with projects.database() as connection:
        assert connection.execute("SELECT blueprint FROM color_operations").fetchone()[0] is None
