import base64
import json
import shutil
import subprocess

import pytest

import frame_preview as preview
import projects
import reference_engine as engine
import reference_jobs as jobs
from tests.test_framing import local as local  # noqa: F401
from tests.test_framing_render import frame, pixel
from tests.test_video_render import prepared, selected


@pytest.fixture
def fixture(local):
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg is required for real previews")
    client, directory, _ = local
    source = directory / "edges.mp4"
    subprocess.run(
        [
            shutil.which("ffmpeg"),
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=green:s=640x360:r=30:d=3,"
            "drawbox=x=0:y=0:w=160:h=360:color=red:t=fill,"
            "drawbox=x=480:y=0:w=160:h=360:color=blue:t=fill,"
            "drawbox=x=0:y=0:w=640:h=30:color=yellow:t=fill,"
            "drawbox=x=0:y=330:w=640:h=30:color=cyan:t=fill,"
            "drawbox=x=160:y=30:w=320:h=300:color=white:t=fill:enable='gte(t,1.5)'",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-colorspace",
            "bt709",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-color_range",
            "tv",
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=15,
    )
    saved, url, path = prepared(client, directory, source)
    revision = selected(client, url, {"brightness": 0, "contrast": 1, "saturation": 1})
    return client, directory, saved["id"], url, path, revision


def request(client, pid, revision, second=0.5, framing_revision=0):
    return client.post(
        f"/api/projects/{pid}/frame-preview",
        json={
            "expected_recipe_revision": revision,
            "expected_framing_revision": framing_revision,
            "timestamp_seconds": second,
        },
    )


def pixels(image, directory, name):
    path = directory / f"{name}.png"
    path.write_bytes(base64.b64decode(image["png_base64"], validate=True))
    return frame(path, second=0, width=image["width"], height=image["height"])


def test_real_same_moment_neutral_adjustment_eof_and_no_saved_record_changes(fixture):
    client, directory, pid, url, source, revision = fixture
    routes = [
        "color-recipe",
        "framing",
        "style-blueprint",
        "footage-color",
        "render",
        "captions",
        "edit-plan",
    ]
    before = [client.get(f"/api/projects/{pid}/{route}").json() for route in routes]
    for time in (0.5, 2, 3):
        response = request(client, pid, revision, time)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["source"]["project_id"] == pid
        assert data["requested_timestamp_seconds"] == time
        assert data["timestamp_seconds"] == pytest.approx(min(time, 89 / 30), abs=1e-6)
        original, edited = (pixels(data[key], directory, key) for key in ("original", "edited"))
        assert original == edited
        expected = frame(source, second=data["timestamp_seconds"], width=640, height=360)
        assert max(abs(a - b) for a, b in zip(original, expected)) <= 3
        center = pixel(original, 320, 180, 640)
        assert (center[1] > center[0] + 80) if time < 1.5 else min(center) > 230
    assert [client.get(f"/api/projects/{pid}/{route}").json() for route in routes] == before
    assert not list((directory / "preview-staging").iterdir())
    revision = selected(client, url, {"brightness": 0.1, "contrast": 1, "saturation": 1})
    data = request(client, pid, revision).json()
    original, edited = (pixels(data[key], directory, key) for key in ("original", "edited"))
    assert sum(edited) / len(edited) > sum(original) / len(original) + 15


def test_real_fit_fill_and_right_crop_use_renderer_geometry(fixture):
    client, directory, pid, _, _, revision = fixture
    for index, choices in enumerate(
        [
            {"format": "portrait", "fit": "fit"},
            {"format": "square", "fit": "fill", "horizontal": 0},
            {"format": "square", "fit": "fill", "horizontal": 1},
        ],
        1,
    ):
        assert (
            client.post(
                f"/api/projects/{pid}/framing", json={"expected_revision": index - 1, **choices}
            ).status_code
            == 200
        )
        response = request(client, pid, revision, framing_revision=index)
        assert response.status_code == 200, response.text
        data = response.json()
        assert (data["original"]["width"], data["original"]["height"]) == (640, 360)
        edited = data["edited"]
        raw = pixels(edited, directory, "geometry")
        width, height = edited["width"], edited["height"]
        assert max(width, height) <= 960
        if index == 1:
            assert (width, height) == (540, 960)
            assert max(pixel(raw, width // 2, 10, width)) < 5
            assert pixel(raw, 10, height // 2, width)[0] > 220
        elif index == 2:
            assert (width, height) == (720, 720)
            assert pixel(raw, 10, height // 2, width)[0] > 220
            assert pixel(raw, width - 10, height // 2, width)[2] < 30
        else:
            assert pixel(raw, 10, height // 2, width)[0] < 30
            assert pixel(raw, width - 10, height // 2, width)[2] > 220


def test_conflicts_contention_invalid_time_and_failure_cleanup(fixture, monkeypatch):
    client, directory, pid, _, _, revision = fixture
    assert request(client, pid, revision + 1).json()["error"]["code"] == "revision_conflict"
    assert request(client, pid, revision, framing_revision=1).status_code == 409
    assert request(client, pid, revision, second=4).status_code == 422
    assert request(client, pid, revision, second=-1).status_code == 422
    monkeypatch.setattr(jobs, "active", (pid, "fixture", None, None))
    assert request(client, pid, revision).json()["error"]["code"] == "reference_busy"
    monkeypatch.setattr(jobs, "active", None)
    monkeypatch.setattr(
        preview, "moment", lambda *args: (_ for _ in ()).throw(engine.ProbeTimeout())
    )
    assert request(client, pid, revision).json()["error"]["code"] == "preview_timeout"
    assert not list((directory / "preview-staging").iterdir())
    monkeypatch.setattr(
        preview, "moment", lambda *args: (_ for _ in ()).throw(engine.ProcessCleanupError())
    )
    assert request(client, pid, revision).json()["error"]["code"] == "cleanup_failure"
    assert list((directory / "preview-staging").iterdir())
    assert request(client, pid, revision).json()["error"]["code"] == "cleanup_failure"


def test_rechecks_source_before_publication(fixture, monkeypatch):
    client, _, pid, _, path, revision = fixture
    with projects.database() as connection:
        before = connection.execute(
            "SELECT blueprint FROM footage_color_operations WHERE project_id=?", (pid,)
        ).fetchone()[0]
    original = preview.image
    calls = 0

    def change(path_arg, expected):
        nonlocal calls
        result = original(path_arg, expected)
        calls += 1
        if calls == 2:
            path.write_bytes(b"changed footage")
        return result

    monkeypatch.setattr(preview, "image", change)
    response = request(client, pid, revision)
    assert response.status_code == 409
    assert "original" not in response.json()
    with projects.database() as connection:
        assert (
            connection.execute(
                "SELECT blueprint FROM footage_color_operations WHERE project_id=?", (pid,)
            ).fetchone()[0]
            == before
        )


def test_rejects_nonfinite_request(local):
    client, _, _ = local
    from tests.test_projects import create

    pid = create(client)["id"]
    response = client.post(
        f"/api/projects/{pid}/frame-preview",
        content=json.dumps(
            {
                "expected_recipe_revision": 1,
                "expected_framing_revision": 0,
                "timestamp_seconds": float("inf"),
            }
        ),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
