import threading
import time

import pytest

import candidate_analysis as candidates
import color_analysis as color
import projects
from tests.test_projects import create
from tests.test_sequence import make_fixtures
from tests.test_video_render import local as local  # noqa: F401


def test_measure_dark_scene_motion_and_empty():
    black = bytes(8 * 8 * 3)
    white = bytes([255]) * len(black)
    samples, boundaries = candidates.measure(
        black + black + white, 8, 8, None, time.monotonic() + 60
    )
    assert samples[0].brightness == 0 and samples[0].sharpness == 0
    assert samples[2].brightness == pytest.approx(1)
    assert samples[2].motion == 1 and boundaries == [30]
    with pytest.raises(candidates.engine.RetrievalFailure):
        candidates.measure(b"", 8, 8, None, time.monotonic() + 60)


def test_real_decode_and_hash_version_cache(local, monkeypatch):
    client, directory, _ = local
    project = create(client)
    a, _ = make_fixtures(directory)
    digest = color.digest(a, max_bytes=100 * 1024 * 1024)
    stage = directory / "candidate-stage"
    stage.mkdir()
    measured, cached = candidates.analyze(
        project["id"], a, digest, stage, threading.Event(), time.monotonic() + 60
    )
    assert not cached and len(measured.samples) == 8
    assert measured.duration_frames == 120 and measured.boundaries == [60]
    monkeypatch.setattr(color, "inspect_video", lambda *a: pytest.fail("cache must avoid decoding"))
    restored, cached = candidates.analyze(
        project["id"], a, digest, stage, None, time.monotonic() + 60
    )
    assert cached and restored == measured
    with projects.database() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 18
