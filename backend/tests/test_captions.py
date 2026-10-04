import pytest

import projects
from tests.test_color_recipe import local as local  # noqa: F401
from tests.test_color_recipe import seed_analyses
from tests.test_edit_plan import seed_pacing


def test_save_restore_revisions_timeline_rebind_preserves_text_and_analyses(local):
    client, directory, _ = local
    saved, recipe = seed_analyses(client, directory)
    pid = saved["id"]
    seed_pacing(pid)
    url = f"/api/projects/{pid}/captions"
    assert client.get(url).json()["track"]["enabled"] is False
    originals = [
        client.get(f"/api/projects/{pid}/{r}").json()
        for r in ["style-blueprint", "footage-color", "audio"]
    ]
    body = {
        "expected_revision": 0,
        "mode": "whole",
        "enabled": True,
        "cues": [{"start": 0.5, "end": 1.5, "text": "Hello\nمرحبا {\\b1}"}],
    }
    response = client.post(url, json=body)
    assert response.status_code == 200, response.text
    assert client.get(url).json() == response.json()
    assert client.post(url, json=body).json()["error"]["code"] == "revision_conflict"
    assert client.post(recipe + "/generate", json={"expected_revision": 0}).status_code == 200
    assert client.get(url).json()["status"] == "ready"  # color changes are independent
    plan = f"/api/projects/{pid}/edit-plan"
    assert client.post(plan + "/generate", json={"expected_revision": 0}).status_code == 200
    changed = body | {"expected_revision": 1, "mode": "cuts", "expected_plan_revision": 1}
    assert client.post(url, json=changed).json()["error"]["code"] == "caption_rebind_required"
    rebound = client.post(url, json=changed | {"confirm_rebind": True})
    assert rebound.status_code == 200 and rebound.json()["track"]["cues"] == body["cues"]
    assert (
        client.post(
            plan, json={"expected_revision": 1, "source_starts_seconds": [0, 1, 2]}
        ).status_code
        == 200
    )
    assert client.get(url).json()["status"] == "stale"
    invalid = changed | {
        "expected_revision": 2,
        "expected_plan_revision": 2,
        "confirm_rebind": True,
        "cues": [{"start": 2, "end": 4, "text": "keep"}],
    }
    assert "Cue 1" in client.post(url, json=invalid).json()["error"]["message"]
    assert client.get(url).json()["track"]["cues"] == body["cues"]
    assert (
        client.post(
            url,
            json=changed
            | {"expected_revision": 2, "expected_plan_revision": 2, "confirm_rebind": True},
        ).status_code
        == 200
    )
    assert originals == [
        client.get(f"/api/projects/{pid}/{r}").json()
        for r in ["style-blueprint", "footage-color", "audio"]
    ]
    with projects.database() as connection:
        connection.execute("PRAGMA user_version=9")
    projects.initialize()
    assert client.get(url).json()["track"]["revision"] == 3
    assert client.delete(f"/api/projects/{pid}").status_code == 204
    with projects.database() as connection:
        assert connection.execute("SELECT count(*) FROM caption_tracks").fetchone()[0] == 0


@pytest.mark.parametrize(
    "cues",
    [
        [{"start": 0, "end": 1, "text": ""}],
        [{"start": 0, "end": 1, "text": "a\nb\nc"}],
        [{"start": 0, "end": 1, "text": "x" * 201}],
        [{"start": True, "end": 1, "text": "x"}],
        [{"start": "NaN", "end": 1, "text": "x"}],
        [{"start": 1, "end": 1, "text": "x"}],
        [{"start": 0, "end": 1, "text": "a"}, {"start": 0.5, "end": 2, "text": "b"}],
        [{"start": 0, "end": 1, "text": "a\x00b"}],
    ],
)
def test_cue_errors_explain_constraint_without_echoing_input(local, cues):
    client, directory, _ = local
    pid = seed_analyses(client, directory)[0]["id"]
    response = client.post(
        f"/api/projects/{pid}/captions",
        json={
            "expected_revision": 0,
            "mode": "whole",
            "enabled": True,
            "cues": cues,
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_caption"
    assert response.json()["error"]["message"] != "The request is invalid."
    assert client.get(f"/api/projects/{pid}/captions").json()["track"]["revision"] == 0


def test_srt_real_body_preview_limits_plain_markup_and_no_persistence(local):
    client, directory, _ = local
    pid = seed_analyses(client, directory)[0]["id"]
    url = f"/api/projects/{pid}/captions"
    text = "1\r\n00:00:00,100 --> 00:00:00,900\r\n<i>Hello</i> {\\b1}\r\nمرحبا\r\n"
    response = client.post(url + "/import", content=text.encode("utf-8"))
    assert response.status_code == 200, response.text
    assert response.json()["cues"][0]["text"] == "<i>Hello</i> {\\b1}\nمرحبا"
    assert response.json()["provenance"] == "srt_import"
    assert client.get(url).json()["track"]["revision"] == 0
    for content in [
        b"bad",
        b"\xff",
        text.replace("00:00:00,900", "00:00:00,000").encode(),
        (text + "\n2\n00:00:00,500 --> 00:00:01,000\nOverlap").encode(),
        b"1\n00:00:00,000 --> 00:00:01,000 align:start\nUnsupported timing",
    ]:
        result = client.post(url + "/import", content=content)
        assert result.status_code == 422 and result.json()["error"]["code"] == "invalid_srt"
    assert client.post(url + "/import", content=b"x" * (128 * 1024 + 1)).status_code == 413
    assert not list((directory / "project-staging").iterdir())
    with projects.database() as connection:
        assert connection.execute("SELECT count(*) FROM caption_tracks").fetchone()[0] == 0
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 10


def test_count_limit_adjacent_half_open_cues_and_nonfinite_times(local):
    client, directory, _ = local
    pid = seed_analyses(client, directory)[0]["id"]
    seed_pacing(pid)
    url = f"/api/projects/{pid}/captions"
    cues = [{"start": i / 100, "end": (i + 1) / 100, "text": "x" * 200} for i in range(200)]
    body = {"expected_revision": 0, "mode": "whole", "enabled": True, "cues": cues}
    assert client.post(url, json=body).status_code == 200
    assert (
        client.post(
            url, json=body | {"expected_revision": 1, "cues": cues + [cues[-1]]}
        ).status_code
        == 422
    )
    assert (
        client.post(
            url,
            content=b'{"expected_revision":1,"mode":"whole","cues":[{"start":NaN,"end":1,"text":"x"}]}',
            headers={"Content-Type": "application/json"},
        ).status_code
        == 422
    )
