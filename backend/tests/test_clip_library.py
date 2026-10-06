import json

import clip_library
import projects
from tests.test_projects import create, upload
from tests.test_projects import local as local


def test_library_migration_upload_rename_remove_range(local):
    client, directory, _ = local
    pid = create(client)["id"]
    assert upload(client, pid).status_code == 200
    original = client.get(f"/api/projects/{pid}").json()
    url = f"/api/projects/{pid}/clips"
    first = client.get(url).json()["clips"][0]
    assert first["primary"]
    response = client.post(url, files={"file": ("second.mp4", b"second", "video/mp4")})
    assert response.status_code == 200, response.text
    second = response.json()["clips"][1]
    assert first["id"] != second["id"]
    assert (
        client.post(f"{url}/{second['id']}", json={"name": "Camera B"}).json()["clips"][1]["name"]
        == "Camera B"
    )
    playback = client.get(f"{url}/{second['id']}/video", headers={"Range": "bytes=1-3"})
    assert playback.status_code == 206 and playback.content == b"eco"
    projects.initialize()
    assert client.get(f"/api/projects/{pid}").json()["clip"] == original["clip"]
    assert client.get(url).json()["clips"][0]["id"] == first["id"]
    assert client.delete(f"{url}/{second['id']}").status_code == 200
    assert len(client.get(url).json()["clips"]) == 1
    assert not list((directory / "project-staging").iterdir())


def test_upload_count_and_streaming_budget(local, monkeypatch):
    client, directory, _ = local
    pid = create(client)["id"]
    url = f"/api/projects/{pid}/clips"
    for index in range(10):
        assert (
            client.post(url, files={"file": (f"{index}.mp4", b"1234", "video/mp4")}).status_code
            == 200
        )
    assert (
        client.post(url, files={"file": ("extra.mp4", b"x", "video/mp4")}).json()["error"]["code"]
        == "clip_limit"
    )
    second = create(client)["id"]
    monkeypatch.setattr(clip_library, "MAX_TOTAL", 6)
    url = f"/api/projects/{second}/clips"
    assert client.post(url, files={"file": ("a.mp4", b"1234", "video/mp4")}).status_code == 200
    assert client.post(url, files={"file": ("b.mp4", b"123", "video/mp4")}).status_code == 413
    assert client.post(url, files={"file": ("b.mp4", b"12", "video/mp4")}).status_code == 200
    assert client.post(url, files={"file": ("c.mp4", b"1", "video/mp4")}).status_code == 413
    assert not list((directory / "project-staging").iterdir())


def test_assigned_clip_cannot_be_removed(local):
    client, _, _ = local
    pid = create(client)["id"]
    assert upload(client, pid).status_code == 200
    clip = clip_library.read(pid).clips[0]
    with projects.database() as db:
        db.execute(
            "INSERT INTO sequences VALUES(?,?,?)",
            (pid, 1, json.dumps({"slots": [{"clip_id": str(clip.id)}]})),
        )
    response = client.delete(f"/api/projects/{pid}/clips/{clip.id}")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "clip_assigned"
    assert clip_library.source(pid, clip.id)[0].exists()
