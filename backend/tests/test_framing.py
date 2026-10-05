import pytest
from pydantic import ValidationError

import framing
import projects
from tests.test_color_recipe import local as local  # noqa: F401
from tests.test_color_recipe import seed_analyses


def test_default_save_restore_migration_conflict_and_immutable_analyses(local):
    client, directory, _ = local
    saved, _ = seed_analyses(client, directory)
    pid = saved["id"]
    url = f"/api/projects/{pid}/framing"
    initial = client.get(url).json()
    assert initial["status"] == "default"
    assert initial["settings"]["format"] == "original"
    assert initial["settings"]["revision"] == 0
    routes = ["style-blueprint", "footage-color", "audio", "captions"]
    before = [client.get(f"/api/projects/{pid}/{route}").json() for route in routes]
    body = {"expected_revision": 0, "format": "portrait", "fit": "fill", "horizontal": 0.25}
    response = client.post(url, json=body)
    assert response.status_code == 200, response.text
    assert response.json()["settings"]["revision"] == 1
    assert client.get(url).json() == response.json()
    assert client.post(url, json=body).json()["error"]["code"] == "revision_conflict"
    with projects.database() as connection:
        connection.execute("PRAGMA user_version=11")
    projects.initialize()
    assert client.get(url).json() == response.json()
    assert before == [client.get(f"/api/projects/{pid}/{route}").json() for route in routes]
    assert client.delete(f"/api/projects/{pid}").status_code == 204
    with projects.database() as connection:
        assert connection.execute("SELECT count(*) FROM framing_settings").fetchone()[0] == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"format": "cinema"},
        {"fit": "stretch"},
        {"horizontal": -0.01},
        {"vertical": 1.01},
        {"horizontal": "NaN"},
        {"vertical": True},
        {"expected_revision": True},
        {"schema_version": 2},
    ],
)
def test_invalid_settings(local, changes):
    from tests.test_projects import create

    client, _, _ = local
    pid = create(client)["id"]
    assert (
        client.post(
            f"/api/projects/{pid}/framing", json={"expected_revision": 0} | changes
        ).status_code
        == 422
    )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_position(value):
    with pytest.raises(ValidationError):
        framing.Choices(horizontal=value)


def test_geometry_rotation_pixels_fit_fill_and_bounds():
    video = {"width": 640, "height": 360}
    assert framing.geometry(video, framing.Settings()) == ((640, 360), (640, 360), "")
    assert framing.geometry(video, framing.Settings(format="square")) == (
        (720, 404),
        (720, 720),
        "pad=720:720:(ow-iw)/2:(oh-ih)/2:color=black",
    )
    for position, expected in [(0, 0), (0.5, 280), (1, 560)]:
        scaled, canvas, crop = framing.geometry(
            video, framing.Settings(format="square", fit="fill", horizontal=position)
        )
        assert scaled == (1280, 720) and canvas == (720, 720)
        assert crop == f"crop=720:720:{expected}:0"
    rotated = video | {"sample_aspect_ratio": "2:1", "side_data_list": [{"rotation": 90}]}
    assert framing.display_dimensions(rotated) == (360, 1280)
    scaled, canvas, crop = framing.geometry(
        rotated, framing.Settings(format="square", fit="fill", vertical=1)
    )
    assert scaled == (720, 2560) and canvas == (720, 720)
    assert crop == "crop=720:720:0:1840"
    for format in framing.FORMATS:
        for fit in ["fit", "fill"]:
            scaled, canvas, _ = framing.geometry(video, framing.Settings(format=format, fit=fit))
            assert all(n % 2 == 0 for n in scaled)
            assert all((s <= c if fit == "fit" else s >= c) for s, c in zip(scaled, canvas))
