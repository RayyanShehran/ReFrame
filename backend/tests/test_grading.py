import math
import threading
import time

import pytest
from pydantic import ValidationError

import clip_library
import color_recipe
import color_transfer as transfer
import grading
import projects
from references import ReferenceError
from tests.test_color_analysis import local as color_fixture
from tests.test_color_recipe import seed_analyses


@pytest.fixture
def local(monkeypatch, tmp_path):
    yield from color_fixture.__wrapped__(monkeypatch, tmp_path)


def textured(offset=0):
    return [
        tuple(transfer.clamp(v + offset) for v in (r / 10, g / 10, b / 10))
        for r in range(1, 10, 2)
        for g in range(1, 10, 2)
        for b in range(1, 10, 2)
    ]


def test_stable_tone_chroma_flat_clipped_and_strength(tmp_path):
    source = textured()
    model = transfer.fit(source, source)
    for rgb in source:
        assert transfer.apply(rgb, model, transfer.Controls(strength=1)) == pytest.approx(
            rgb, abs=1e-8
        )
    target = [(0.08 + r * 0.9, g * 0.85, b * 0.8) for r, g, b in source]
    model = transfer.fit(source, target)
    original = (0.2, 0.4, 0.6)
    assert transfer.apply(original, model, transfer.Controls(strength=0)) == original
    half = transfer.apply(original, model, transfer.Controls(strength=0.5))
    full = transfer.apply(original, model, transfer.Controls(strength=1))
    assert half == pytest.approx(tuple((a + b) / 2 for a, b in zip(original, full)))
    assert full != pytest.approx(original, abs=0.01)
    for samples in (
        [(0.0, 0.0, 0.0)] * 100,
        [(0.999, 0.999, 0.999)] * 100,
        [(0.5, 0.5, 0.5)] * 100,
    ):
        model = transfer.fit(samples, target)
        for rgb in ((0, 0, 0), (0.5, 0.5, 0.5), (1, 1, 1)):
            actual = transfer.apply(rgb, model, transfer.Controls(strength=1))
            assert all(math.isfinite(v) and 0 <= v <= 1 for v in actual)
        transfer.cube(tmp_path / "generated.cube", model, transfer.Controls())
        assert (tmp_path / "generated.cube").stat().st_size < 160000
    with pytest.raises(ValidationError):
        transfer.Controls(strength=float("nan"))
    with pytest.raises(ValueError):
        transfer.fit([(float("nan"), 0, 0)], target)


def test_persistent_modes_revision_immutable_analyses_and_source_staleness(local):
    client, directory, _ = local
    saved, url = seed_analyses(client, directory)
    id = saved["id"]
    original = color_recipe.bindings(id)
    assert grading.read_settings(id).mode == "basic"  # Old recipe behavior until explicit choice.
    assert client.post(url + "/generate", json={"expected_revision": 0}).status_code == 200
    prior_recipe = color_recipe.read(id).recipe
    clip = clip_library.read(id).clips[0]
    match = grading.Match(
        key=f"clip:{clip.id}",
        clip_id=clip.id,
        source_hash=clip.sha256,
        reference=grading.reference_binding(id),
        model=transfer.fit(textured(), textured(0.1)),
        ffmpeg_version="fixture",
        prepared_at=projects.now(),
    )
    with projects.database() as db:
        db.execute(
            "INSERT INTO color_matches VALUES(?,?,?)", (id, match.key, match.model_dump_json())
        )
    endpoint = f"/api/projects/{id}/grading"
    body = {"expected_revision": 0, "mode": "transfer", "controls": {match.key: {"strength": 0.7}}}
    assert client.post(endpoint, json=body).status_code == 200
    assert client.post(endpoint, json=body).status_code == 409
    result = client.get(endpoint).json()
    assert result["settings"]["mode"] == "transfer" and result["entries"][0]["valid"]
    assert result["settings"]["controls"][match.key]["strength"] == 0.7
    assert color_recipe.bindings(id) == original and color_recipe.read(id).recipe == prior_recipe
    bound = grading.snapshot(id, [clip.id])
    assert grading.routed(bound, clip.id)[0] == match
    path, _ = clip_library.source(id, clip.id)
    path.write_bytes(b"changed")
    assert client.get(endpoint).json()["entries"][0]["valid"] is False
    with pytest.raises(ReferenceError):
        grading.snapshot(id, [clip.id])
    assert (
        client.post(endpoint, json={"expected_revision": 1, "mode": "transfer"}).status_code == 409
    )
    assert (
        client.post(endpoint, json={"expected_revision": 1, "mode": "original"}).status_code == 200
    )
    assert grading.read_settings(id).mode == "original"


def test_worker_publication_rejects_changed_revision_and_preserves_prior(local, monkeypatch):
    client, directory, _ = local
    saved, _ = seed_analyses(client, directory)
    id = saved["id"]
    operation, data = grading.begin_operation(id, grading.Prepare(expected_revision=0))
    stage = grading.staging(operation.operation_id)
    stage.mkdir(parents=True)
    target = data["targets"][0]
    match = grading.Match(
        key=target["key"],
        clip_id=target["clip"].id,
        source_hash=target["clip"].sha256,
        reference=data["binding"],
        model=transfer.fit(textured(), textured(0.1)),
        ffmpeg_version="fixture",
        prepared_at=projects.now(),
    )
    prepared = grading.Prepared(matches=[match])
    grading.save(id, grading.Save(expected_revision=0, mode="original"))
    with pytest.raises(ReferenceError) as exc:
        grading.commit(
            id, operation.operation_id, data, prepared, threading.Event(), time.monotonic() + 10
        )
    assert exc.value.code == "revision_conflict"
    assert not grading.get_operation(id).entries
    grading.clean_stage(operation.operation_id)
