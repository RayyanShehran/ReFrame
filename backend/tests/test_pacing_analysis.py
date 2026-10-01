import hashlib
import math
import shutil
import subprocess
import threading
import time

import pytest

import color_analysis as color
import pacing_analysis as pacing
import projects
import reference_engine as engine
import reference_jobs as jobs
from references import ReferenceError
from tests.test_color_analysis import finish, generated, prepared
from tests.test_color_analysis import local as local  # noqa: F401


def fixture(path, segments):
    colors = [rgb for rgb, frames in segments for _ in range(frames)]
    return generated(path, colors, frames_per_color=1)


def analyze(tmp_path, segments):
    path = tmp_path / "fixture.mkv"
    fixture(path, segments)
    source = {
        "path": path,
        "identity": color.Source(
            video_id="123",
            canonical_url="https://www.tiktok.com/@fixture/video/123",
            media_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        ),
    }
    return pacing.pipeline(source, tmp_path / "stage", threading.Event(), time.monotonic() + 10)[1]


def test_real_constant_zero_cuts_one_shot(tmp_path):
    result = analyze(tmp_path, [((128, 128, 128), 24)])
    assert result.successful_frames == 24
    assert result.candidate_cut_timestamps == []
    assert result.shot_count == 1 and result.estimated_cut_count == 0
    assert result.mean_shot_seconds == result.median_shot_seconds == 2
    assert result.shots[0].start_seconds == 0 and result.shots[0].end_seconds == 2
    assert result.cuts_per_minute == 0


def test_real_abrupt_changes_unequal_lengths(tmp_path):
    result = analyze(tmp_path, [((0, 0, 0), 12), ((255, 255, 255), 24), ((0, 0, 0), 48)])
    assert result.successful_frames == 84
    assert result.candidate_cut_timestamps == pytest.approx([1, 3], abs=1 / 12)
    assert [s.duration_seconds for s in result.shots] == pytest.approx([1, 2, 4], abs=1 / 12)
    assert result.shot_count == 3
    assert result.mean_shot_seconds == pytest.approx(7 / 3)
    assert result.median_shot_seconds == 2
    assert result.cuts_per_minute == pytest.approx(120 / 7)
    assert result.threshold == 10 and result.processing_width == result.processing_height == 64
    assert result.tool_versions["ffmpeg"]
    assert result.other_categories.transitions == "not_analyzed"


def test_real_one_frame_flash_is_false_positive(tmp_path):
    result = analyze(tmp_path, [((0, 0, 0), 12), ((255, 255, 255), 1), ((0, 0, 0), 12)])
    # A flash within one shot is mistaken for a cut; returning from it is missed by scdet.
    assert result.candidate_cut_timestamps == pytest.approx([1], abs=1 / 12)
    assert result.shot_count == 2
    assert any("Flashes" in warning for warning in result.interpretation_limits)


def test_real_wide_video_downscales_without_crop(tmp_path):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg required for generated-media integration")
    path = tmp_path / "wide.mkv"
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=gray:s=640x360:r=12:d=1",
            "-c:v",
            "libx264",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=10,
    )
    source = {
        "path": path,
        "identity": color.Source(
            video_id="123",
            canonical_url="https://www.tiktok.com/@fixture/video/123",
            media_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        ),
    }
    _, result = pacing.pipeline(
        source, tmp_path / "stage", threading.Event(), time.monotonic() + 10
    )
    assert (result.processing_width, result.processing_height) == (320, 180)
    assert result.successful_frames == 12 and result.shot_count == 1


def record(index, timestamp, score=0, cut=False):
    return (
        f"frame:{index} pts:{index} pts_time:{timestamp}\n"
        f"lavfi.scd.mafd={score}\nlavfi.scd.score={score}\n"
        + (f"lavfi.scd.time={timestamp}\n" if cut else "")
    ).encode()


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"frame:0 pts:0 pts_time:0\n",
        record(0, "nan"),
        record(0, 0, math.inf),
        record(0, 0) + record(2, 1),
        record(0, 0) + record(1, -1),
        record(0, 0)[:-1],
    ],
)
def test_missing_incomplete_nonfinite_detector_output_fails(raw):
    with pytest.raises((engine.RetrievalFailure, engine.ToolOutputError, ValueError)):
        pacing.parse_detector(raw, 2, threading.Event(), time.monotonic() + 5)


def test_sorted_unique_interior_boundaries_and_candidate_cap():
    raw = record(0, 0) + record(1, 1, 50, True) + record(2, 1, 50, True) + record(3, 2, 50, True)
    cuts, count = pacing.parse_detector(raw, 2, None, time.monotonic() + 5)
    assert cuts == [1] and count == 4
    raw = record(0, 0) + b"".join(record(i, i / 100, 50, True) for i in range(1, 1002))
    with pytest.raises(engine.RetrievalFailure) as error:
        pacing.parse_detector(raw, 20, None, time.monotonic() + 5)
    assert error.value.code == "cut_limit"


def test_failure_retry_migration_and_hash_invalidation_preserve_color(local, tmp_path, monkeypatch):
    client, saved = prepared(local, tmp_path)
    color_url = f"/api/projects/{saved['id']}/style-blueprint"
    url = f"/api/projects/{saved['id']}/pacing"
    client.post(color_url)
    original = finish(client, color_url)["blueprint"]
    with projects.database() as connection:
        connection.execute("DROP TABLE pacing_operations")
        connection.execute("PRAGMA user_version=3")
    projects.initialize()
    assert client.get(color_url).json()["blueprint"] == original
    assert client.get(url).json()["status"] == "idle"
    real = engine.run_command

    def empty(args, stage, name, *args_rest, **kwargs):
        if name == "pacing-detector":
            return subprocess.CompletedProcess(args, 0, b"", "")
        return real(args, stage, name, *args_rest, **kwargs)

    monkeypatch.setattr(engine, "run_command", empty)
    first = client.post(url).json()
    assert finish(client, url)["failure_code"] == "detector_output_missing"
    assert client.get(color_url).json()["blueprint"] == original
    monkeypatch.setattr(engine, "run_command", real)
    second = client.post(url).json()
    result = finish(client, url)
    assert result["status"] == "ready" and second["operation_id"] != first["operation_id"]
    pacing.recover()
    assert client.get(url).json()["blueprint"] == result["blueprint"]
    assert client.post(url).json()["operation_id"] == second["operation_id"]
    assert client.get(color_url).json()["blueprint"] == original
    reference = jobs.get_operation(saved["id"])
    path = jobs.destination(saved["id"], reference.operation_id)
    data = path.read_bytes()
    path.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
    invalid = client.get(url).json()
    assert invalid["failure_code"] == "source_changed" and invalid["blueprint"] is None


def test_stale_and_deleted_pacing_cannot_publish(local, tmp_path):
    _, saved = prepared(local, tmp_path)
    operation, source = pacing.begin_operation(saved["id"])
    current, result = pacing.pipeline(
        source, pacing.staging(operation.operation_id), threading.Event(), time.monotonic() + 10
    )
    pacing.recover()
    newer, _ = pacing.begin_operation(saved["id"])
    with pytest.raises(engine.RetrievalFailure):
        pacing.commit(
            saved["id"],
            operation.operation_id,
            current,
            result,
            threading.Event(),
            time.monotonic() + 10,
        )
    assert pacing.get_operation(saved["id"]).operation_id == newer.operation_id
    projects.delete_project(saved["id"])
    with pytest.raises(ReferenceError):
        pacing.commit(
            saved["id"],
            newer.operation_id,
            current,
            result,
            threading.Event(),
            time.monotonic() + 10,
        )


def test_pacing_uses_shared_slot_and_owned_deletion(local, tmp_path, monkeypatch):
    client, saved = prepared(local, tmp_path)
    entered, released = threading.Event(), threading.Event()

    def waiting(source, stage, stop, deadline):
        stage.mkdir()
        entered.set()
        assert stop.wait(5)
        released.set()
        raise engine.RetrievalFailure("interrupted", "Pacing stopped")

    monkeypatch.setattr(pacing, "pipeline", waiting)
    url = f"/api/projects/{saved['id']}/pacing"
    first = client.post(url).json()
    assert entered.wait(2)
    try:
        assert client.post(url).json()["operation_id"] == first["operation_id"]
        assert client.post(f"/api/projects/{saved['id']}/style-blueprint").status_code == 503
        assert client.get(f"/api/projects/{saved['id']}").status_code == 200
        assert client.delete(f"/api/projects/{saved['id']}").status_code == 204
        assert released.is_set() and jobs.active is None
        assert not pacing.staging(first["operation_id"]).exists()
    finally:
        if jobs.active:
            jobs.active[2].set()
            client.portal.call(jobs.shutdown)


def test_pacing_quarantine_blocks_color_and_survives_recovery(local, tmp_path, monkeypatch):
    client, saved = prepared(local, tmp_path)

    def unconfirmed(*args, **kwargs):
        raise engine.ProcessCleanupError

    monkeypatch.setattr(engine, "run_command", unconfirmed)
    url = f"/api/projects/{saved['id']}/pacing"
    client.post(url)
    result = finish(client, url)
    assert result["failure_code"] == "cleanup_failure"
    stage = pacing.staging(result["operation_id"])
    pacing.recover()
    assert stage.exists()
    assert client.post(f"/api/projects/{saved['id']}/style-blueprint").status_code == 500
    assert client.delete(f"/api/projects/{saved['id']}").status_code == 500
