import json

import pytest

import projects
from tests.test_color_recipe import local as local  # noqa: F401
from tests.test_color_recipe import seed_analyses


def reference_audio(project_id, present=True):
    """Settings-only tests seed retained metadata; rendering tests use real streams."""
    with projects.database() as connection:
        row = connection.execute(
            "SELECT metadata FROM reference_operations WHERE project_id=?", (project_id,)
        ).fetchone()
        metadata = json.loads(row[0]) | {
            "has_audio": present,
            "audio_codec": "aac" if present else None,
            "decoded_audio_samples": 16000 if present else 0,
        }
        connection.execute(
            "UPDATE reference_operations SET metadata=? WHERE project_id=?",
            (json.dumps(metadata), project_id),
        )


def test_default_save_restore_conflict_migration_and_analyses_immutable(local):
    client, directory, _ = local
    saved, _ = seed_analyses(client, directory)
    pid = saved["id"]
    reference_audio(pid)
    url = f"/api/projects/{pid}/audio"
    initial = client.get(url).json()
    assert initial["status"] == "default" and initial["settings"]["revision"] == 0
    assert (
        initial["settings"]["mode"] == "original" and initial["settings"]["original_volume"] == 100
    )
    original = [
        client.get(f"/api/projects/{pid}/{route}").json()
        for route in ["style-blueprint", "footage-color"]
    ]
    body = {
        "expected_revision": 0,
        "mode": "mix",
        "original_volume": 70,
        "reference_volume": 30,
        "reference_offset_seconds": 0.2,
    }
    response = client.post(url, json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert (
        result["settings"]["revision"] == 1
        and result["settings"]["reference"]["source"]["video_id"] == saved["reference"]["video_id"]
    )
    assert client.get(url).json() == result
    assert client.post(url, json=body).json()["error"]["code"] == "revision_conflict"
    with projects.database() as connection:
        connection.execute("PRAGMA user_version=8")
    projects.initialize()
    assert client.get(url).json() == result
    assert original == [
        client.get(f"/api/projects/{pid}/{route}").json()
        for route in ["style-blueprint", "footage-color"]
    ]
    assert client.delete(f"/api/projects/{pid}").status_code == 204
    assert client.get(url).status_code == 404
    with projects.database() as connection:
        assert connection.execute("SELECT count(*) FROM audio_settings").fetchone()[0] == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"mode": "music"},
        {"original_volume": -1},
        {"reference_volume": 101},
        {"reference_volume": "NaN"},
        {"original_volume": True},
        {"reference_offset_seconds": -0.1},
        {"reference_offset_seconds": "Infinity"},
        {"expected_revision": True},
        {"reference": {"source": "client"}},
    ],
)
def test_invalid_and_untrusted_settings_rejected(local, changes):
    client, _, _ = local
    from tests.test_projects import create

    saved = create(client)
    response = client.post(
        f"/api/projects/{saved['id']}/audio", json={"expected_revision": 0} | changes
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


def test_required_streams_offset_and_explicit_stale_source_rebinding(local):
    client, directory, _ = local
    saved, _ = seed_analyses(client, directory)
    pid = saved["id"]
    url = f"/api/projects/{pid}/audio"
    assert (
        client.post(url, json={"expected_revision": 0, "mode": "reference"}).json()["error"]["code"]
        == "reference_audio_unavailable"
    )
    reference_audio(pid)
    assert (
        client.post(
            url, json={"expected_revision": 0, "mode": "reference", "reference_offset_seconds": 1}
        ).status_code
        == 422
    )
    result = client.post(url, json={"expected_revision": 0, "mode": "reference"}).json()
    assert result["status"] == "ready"
    with projects.database() as connection:
        row = connection.execute(
            "SELECT settings FROM audio_settings WHERE project_id=?", (pid,)
        ).fetchone()
        stored = json.loads(row[0])
        stored["reference"]["source"]["media_sha256"] = "0" * 64
        connection.execute(
            "UPDATE audio_settings SET settings=? WHERE project_id=?", (json.dumps(stored), pid)
        )
    stale = client.get(url).json()
    assert stale["status"] == "stale" and stale["settings"]["revision"] == 1
    assert (
        client.post(url, json={"expected_revision": 1, "mode": "reference"}).json()["error"]["code"]
        == "audio_stale"
    )
    assert (
        client.post(
            url, json={"expected_revision": 1, "mode": "reference", "refresh_sources": True}
        ).json()["settings"]["revision"]
        == 2
    )
    with projects.database() as connection:
        row = connection.execute("SELECT metadata FROM clips WHERE project_id=?", (pid,)).fetchone()
        meta = json.loads(row[0]) | {"has_audio": False, "audio_codec": None}
        connection.execute(
            "UPDATE clips SET metadata=? WHERE project_id=?", (json.dumps(meta), pid)
        )
    assert (
        client.post(url, json={"expected_revision": 2, "mode": "mix"}).json()["error"]["code"]
        == "original_audio_unavailable"
    )
    # Original-only silent footage is valid; mute needs no audio source.
    assert (
        client.post(url, json={"expected_revision": 2, "mode": "original"}).json()["status"]
        == "ready"
    )
    assert (
        client.post(url, json={"expected_revision": 3, "mode": "mute"}).json()["settings"][
            "reference"
        ]
        is None
    )
