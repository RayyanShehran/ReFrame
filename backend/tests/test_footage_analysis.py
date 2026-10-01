import hashlib
import threading
import time

import pytest

import color_analysis as color
import footage_analysis as footage
import projects
import reference_engine as engine
from references import ReferenceError
from tests.test_color_analysis import finish, generated, seed
from tests.test_color_analysis import local as color_fixture
from tests.test_projects import create, upload


@pytest.fixture
def local(monkeypatch, tmp_path):
    yield from color_fixture.__wrapped__(monkeypatch, tmp_path)


def setup_clip(client, directory, rgb=(255, 0, 0)):
    saved = create(client)
    path = directory / "fixture.mp4"
    generated(path, [rgb])
    assert upload(client, saved["id"], path.read_bytes()).status_code == 200
    return saved, path, f"/api/projects/{saved['id']}/footage-color"


def test_independent_real_analysis_restores_and_migrates(local):
    client, directory, _ = local
    saved, path, url = setup_clip(client, directory)
    assert client.post(url).status_code == 202
    result = finish(client, url)
    assert result["status"] == "ready", result
    b = result["blueprint"]
    assert b["color"]["rgb_mean"] == pytest.approx([1, 0, 0])
    assert b["sampling"]["decoded_bytes"] == 331776
    assert b["source"]["project_id"] == saved["id"]
    assert b["source"]["media_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert client.get(f"/api/projects/{saved['id']}/style-blueprint").json()["status"] == "idle"
    # Additive schema upgrade retains the independent result and existing clip.
    with projects.database() as connection:
        connection.execute("PRAGMA user_version=4")
    projects.initialize()
    footage.recover()
    assert client.get(url).json() == result
    assert client.get(f"/api/projects/{saved['id']}").json()["clip_status"] == "ready"


def test_failure_retry_invalidation_preserves_reference(local, monkeypatch):
    client, directory, _ = local
    saved, path, url = setup_clip(client, directory)
    seed(saved, path, 1)
    ref_url = f"/api/projects/{saved['id']}/style-blueprint"
    assert client.post(ref_url).status_code == 202
    reference = finish(client, ref_url)
    real = footage.pipeline

    def fail(*args):
        raise engine.RetrievalFailure("decode_failed", "Fixture decode failed.")

    monkeypatch.setattr(footage, "pipeline", fail)
    assert client.post(url).status_code == 202
    assert finish(client, url)["failure_code"] == "decode_failed"
    assert client.get(ref_url).json() == reference
    monkeypatch.setattr(footage, "pipeline", real)
    assert client.post(url).status_code == 202
    ready = finish(client, url)
    assert ready["status"] == "ready"
    assert ready["blueprint"]["color"] == reference["blueprint"]["color"]
    # Same-size mutation must invalidate by hash, independently of project size checks.
    source = footage.source(saved["id"])["path"]
    original = source.read_bytes()
    source.write_bytes(bytes([original[0] ^ 1]) + original[1:])
    assert client.get(url).json()["failure_code"] == "source_changed"
    assert client.get(ref_url).json() == reference
    source.write_bytes(original)
    assert client.post(url).status_code == 202
    assert finish(client, url)["status"] == "ready"
    source.unlink()
    assert client.get(url).json()["status"] == "failed"
    assert client.get(ref_url).json() == reference


def test_real_footage_over_reference_limit(local):
    client, directory, _ = local
    saved, path, url = setup_clip(client, directory)
    # MP4 tolerates trailing padding; saved upload is genuinely >50 MiB, not a mocked digest.
    content = path.read_bytes() + bytes(51 * 1024 * 1024)
    second = create(client, "Large clip")
    assert upload(client, second["id"], content).status_code == 200
    current = footage.source(second["id"])
    assert current["path"].stat().st_size > engine.MAX_BYTES
    with pytest.raises(ReferenceError):
        color.digest(current["path"])
    url = f"/api/projects/{second['id']}/footage-color"
    assert client.post(url).status_code == 202
    assert finish(client, url)["status"] == "ready"


def test_real_differing_colors_signed_measurements(local):
    client, directory, _ = local
    saved, _, url = setup_clip(client, directory, (0, 255, 0))
    red = directory / "red.mp4"
    generated(red, [(255, 0, 0)])
    seed(saved, red, 1)
    ref_url = f"/api/projects/{saved['id']}/style-blueprint"
    assert client.post(ref_url).status_code == 202
    ref = finish(client, ref_url)["blueprint"]["color"]
    assert client.post(url).status_code == 202
    clip = finish(client, url)["blueprint"]["color"]
    assert [(a - b) * 100 for a, b in zip(ref["rgb_mean"], clip["rgb_mean"])] == pytest.approx(
        [100, -100, 0]
    )
    assert (ref["brightness_p50"] - clip["brightness_p50"]) * 100 == pytest.approx(-50.26)
    assert ref["contrast_spread"] == clip["contrast_spread"] == 0
    assert ref["mean_hsv_saturation"] == clip["mean_hsv_saturation"] == 1


def test_source_algorithm_stale_delete_and_restart(local):
    client, directory, _ = local
    saved, _, url = setup_clip(client, directory)
    operation, current = footage.begin_operation(saved["id"])
    stop, deadline = threading.Event(), time.monotonic() + 30
    current, blueprint = footage.pipeline(
        current, footage.staging(operation.operation_id), stop, deadline
    )
    footage.fail_operation(
        saved["id"], operation.operation_id, engine.RetrievalFailure("test", "Retry.")
    )
    replacement, _ = footage.begin_operation(saved["id"])
    with pytest.raises(engine.RetrievalFailure, match="no longer current"):
        footage.commit(saved["id"], operation.operation_id, current, blueprint, stop, deadline)
    footage.recover()
    assert footage.get_operation(saved["id"]).failure_code == "interrupted"
    assert not footage.staging(replacement.operation_id).exists()
    assert client.post(url).status_code == 202
    assert finish(client, url)["status"] == "ready"
    with projects.database() as connection:
        connection.execute(
            "UPDATE footage_color_operations SET algorithm_version='old' WHERE project_id=?",
            (saved["id"],),
        )
    assert client.get(url).json()["failure_code"] == "algorithm_changed"
    assert client.delete(f"/api/projects/{saved['id']}").status_code == 204
    with pytest.raises(ReferenceError):
        footage.commit(saved["id"], replacement.operation_id, current, blueprint, stop, deadline)
