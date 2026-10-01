import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import color_analysis as color
import color_recipe as recipe
import footage_analysis as footage
import projects
import reference_engine as engine
from tests.test_color_analysis import local as color_fixture
from tests.test_color_analysis import seed
from tests.test_projects import create, upload


@pytest.fixture
def local(monkeypatch, tmp_path):
    yield from color_fixture.__wrapped__(monkeypatch, tmp_path)


def measurements(brightness=0.5, contrast=0.1, saturation=0.5):
    return SimpleNamespace(
        brightness_p50=brightness, contrast_spread=contrast, mean_hsv_saturation=saturation
    )


def seed_analyses(client, directory):
    """Persist typed synthetic measurements; generation must not decode or contact TikTok."""
    saved = create(client)
    path = directory / "source.mp4"
    path.write_bytes(b"retained fixture bytes")
    seed(saved, path, 1)
    assert upload(client, saved["id"], path.read_bytes()).status_code == 200
    for module, rgb in [(color, (200, 100, 50)), (footage, (100, 100, 100))]:
        operation, source = module.begin_operation(saved["id"])
        b = module.Blueprint(
            analyzed_at=projects.now(),
            source=source["identity"],
            tool_versions={"fixture": "stored synthetic pixels"},
            sampling=color.Sampling(
                method="12 evenly spaced midpoint targets; FFmpeg fps nearest rounding",
                timestamps_seconds=[(i + 0.5) / 12 for i in range(12)],
                successful_samples=12,
                duration_seconds=1,
                video_stream_index=0,
                decoded_bytes=color.OUTPUT_BYTES,
                pixel_weighting=(
                    "Equal resized pixels and equal time samples; "
                    "area resize to 96x96 without padding"
                ),
                temporal_rule=(
                    "PTS normalized, shifted by half an interval, "
                    "fps=12/duration round=near eof_action=pass; frames may repeat"
                ),
            ),
            color_metadata=color.inspect_colors({}),
            color=color.measure(bytes(rgb) * (color.OUTPUT_BYTES // 3)),
            interpretation_limits=color.LIMITS,
        )
        with projects.database() as connection:
            connection.execute(
                f"UPDATE {module.TABLE} SET state='ready',blueprint=?,finished_at=? "
                "WHERE project_id=?",
                (b.model_dump_json(), projects.now(), saved["id"]),
            )
    return saved, f"/api/projects/{saved['id']}/color-recipe"


def test_suggestion_neutral_clamp_and_denominator_boundary():
    suggested, notes = recipe.suggest(measurements(), measurements())
    assert suggested.model_dump() == {"brightness": 0, "contrast": 1, "saturation": 1}
    assert not notes
    suggested, _ = recipe.suggest(measurements(0.9, 0.9, 0.9), measurements(0.1, 0.02, 0.02))
    assert suggested.model_dump() == {"brightness": 0.1, "contrast": 1.2, "saturation": 1.2}
    suggested, _ = recipe.suggest(measurements(0.1, 0.02, 0.02), measurements(0.9, 0.9, 0.9))
    assert suggested.model_dump() == {"brightness": -0.1, "contrast": 0.8, "saturation": 0.8}
    suggested, notes = recipe.suggest(measurements(), measurements(contrast=0.01999, saturation=0))
    assert suggested.contrast == suggested.saturation == 1
    assert len(notes) == 2 and all("Insufficient" in note for note in notes)


@pytest.mark.parametrize("strength", [0, 0.5, 1])
def test_strength_interpolates_neutral(strength):
    selected = recipe.Values(brightness=-0.2, contrast=1.5, saturation=0.5)
    actual = recipe.effective(selected, strength)
    assert actual.model_dump() == pytest.approx(
        {
            "brightness": -0.2 * strength,
            "contrast": 1 + 0.5 * strength,
            "saturation": 1 - 0.5 * strength,
        }
    )


def test_generate_save_restore_revision_replacement_and_immutability(local, monkeypatch):
    client, directory, _ = local
    saved, url = seed_analyses(client, directory)
    original = (
        color.get_operation(saved["id"]).model_dump_json(),
        footage.get_operation(saved["id"]).model_dump_json(),
    )
    monkeypatch.setattr(
        engine, "run_command", lambda *a, **k: pytest.fail("Recipe must not decode")
    )
    assert client.get(url).json()["status"] == "empty"
    result = client.post(url + "/generate", json={"expected_revision": 0}).json()
    assert result["status"] == "ready" and result["recipe"]["revision"] == 1
    assert result["recipe"]["strength"] == 0.5
    assert result["recipe"]["reference"]["source"]["video_id"] == saved["reference"]["video_id"]
    selected = {"brightness": -0.15, "contrast": 1.3, "saturation": 0.8}
    request = {"expected_revision": 1, "selected": selected, "strength": 0.8}
    response = client.post(url, json=request)
    assert response.status_code == 200
    edited = response.json()
    assert edited["recipe"]["revision"] == 2
    assert edited["recipe"]["suggested"] == result["recipe"]["suggested"]
    assert edited["recipe"]["created_at"] == result["recipe"]["created_at"]
    assert edited["effective"] == pytest.approx(
        {"brightness": -0.12, "contrast": 1.24, "saturation": 0.84}
    )
    assert client.post(url, json=request).json()["error"]["code"] == "revision_conflict"
    assert (
        client.post(url + "/generate", json={"expected_revision": 2}).json()["error"]["code"]
        == "recipe_exists"
    )
    assert (
        client.post(url + "/generate", json={"expected_revision": 1, "replace": True}).status_code
        == 409
    )
    with projects.database() as connection:
        connection.execute("PRAGMA user_version=5")
    projects.initialize()
    assert client.get(url).json() == edited
    replaced = client.post(
        url + "/generate", json={"expected_revision": 2, "replace": True}
    ).json()["recipe"]
    assert replaced["revision"] == 3 and replaced["strength"] == 0.5
    assert replaced["selected"] == replaced["suggested"]
    assert replaced["created_at"] == edited["recipe"]["created_at"]
    assert original == (
        color.get_operation(saved["id"]).model_dump_json(),
        footage.get_operation(saved["id"]).model_dump_json(),
    )
    assert client.delete(f"/api/projects/{saved['id']}").status_code == 204
    assert client.get(url).status_code == 404
    with projects.database() as connection:
        assert connection.execute("SELECT count(*) FROM color_recipes").fetchone()[0] == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"strength": -0.01},
        {"strength": 1.01},
        {"strength": "NaN"},
        {"strength": True},
        {"selected": {"brightness": 0.201}},
        {"selected": {"contrast": 0.49}},
        {"selected": {"saturation": 1.51}},
        {"selected": {"brightness": "Infinity"}},
        {"expected_revision": 1.5},
        {"suggested": {}},
        {"reference": {"hash": "client"}},
    ],
)
def test_invalid_and_untrusted_input_rejected(local, changes):
    client, _, _ = local
    saved = create(client)
    url = f"/api/projects/{saved['id']}/color-recipe"
    response = client.post(
        url, json={"expected_revision": 1, "selected": {}, "strength": 0.5} | changes
    )
    assert response.status_code == 422 and response.json()["error"]["code"] == "invalid_request"
    assert client.get(url).json()["status"] == "empty"


def test_nonfinite_models_and_generation_requires_analyses(local):
    for value in [float("nan"), float("inf"), -float("inf")]:
        with pytest.raises(ValidationError):
            recipe.Values(brightness=value)
        with pytest.raises(ValidationError):
            recipe.SaveRequest(expected_revision=1, selected={}, strength=value)
    client, _, _ = local
    saved = create(client)
    url = f"/api/projects/{saved['id']}/color-recipe"
    assert (
        client.post(url + "/generate", json={"expected_revision": 0}).json()["error"]["code"]
        == "analyses_not_ready"
    )
    assert (
        client.post(url + "/generate", json={"expected_revision": 0, "suggested": {}}).status_code
        == 422
    )


@pytest.mark.parametrize(
    "cause", ["changed", "missing", "analysis_version", "analysis_identity", "suggestion_version"]
)
def test_stale_preserves_settings_and_requires_explicit_regeneration(local, cause):
    client, directory, _ = local
    saved, url = seed_analyses(client, directory)
    initial = client.post(url + "/generate", json={"expected_revision": 0}).json()["recipe"]
    with projects.database() as connection:
        originals = {
            table: dict(
                connection.execute(
                    f"SELECT * FROM {table} WHERE project_id=?", (saved["id"],)
                ).fetchone()
            )
            for table in ("color_operations", "footage_color_operations")
        }
    source = footage.source(saved["id"])["path"]
    original = source.read_bytes()
    if cause == "changed":
        source.write_bytes(b"X" + original[1:])
    elif cause == "missing":
        source.unlink()
    else:
        with projects.database() as connection:
            if cause == "analysis_version":
                connection.execute(
                    "UPDATE color_operations SET algorithm_version='old' WHERE project_id=?",
                    (saved["id"],),
                )
            elif cause == "analysis_identity":
                connection.execute(
                    "UPDATE color_operations SET operation_id="
                    "'11111111-1111-4111-8111-111111111111' WHERE project_id=?",
                    (saved["id"],),
                )
            else:
                altered = initial | {"suggestion_algorithm_version": "old"}
                connection.execute(
                    "UPDATE color_recipes SET recipe=? WHERE project_id=?",
                    (json.dumps(altered), saved["id"]),
                )
    stale = client.get(url).json()
    assert stale["status"] == "stale" and stale["effective"] is None
    assert stale["recipe"]["selected"] == initial["selected"] and stale["recipe"]["revision"] == 1
    assert (
        client.post(url, json={"expected_revision": 1, "selected": {}, "strength": 0.5}).json()[
            "error"
        ]["code"]
        == "recipe_stale"
    )
    # Restore the fixture sources/compatible measurements; read must not silently revive the recipe.
    source.write_bytes(original)
    with projects.database() as connection:
        connection.execute("UPDATE clips SET state='ready' WHERE project_id=?", (saved["id"],))
        for table, row in originals.items():
            connection.execute(
                f"UPDATE {table} SET state='ready',blueprint=?,algorithm_version=?,"
                "operation_id=?,failure_code=NULL,message=NULL WHERE project_id=?",
                (row["blueprint"], row["algorithm_version"], row["operation_id"], saved["id"]),
            )
    assert client.get(url).json()["status"] == "stale"
    regenerated = client.post(url + "/generate", json={"expected_revision": 1, "replace": True})
    assert regenerated.status_code == 200
    assert regenerated.json()["status"] == "ready" and regenerated.json()["recipe"]["revision"] == 2
