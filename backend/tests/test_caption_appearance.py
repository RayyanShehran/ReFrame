import json
import shutil
import subprocess
import time

import pytest

import caption_appearance as appearance
import captions
import font_assets as fonts
import font_match as match
import projects
from tests.test_video_render import local as local  # noqa: F401
from tests.test_video_render import prepared

TEXT = "REFRAME Caption"
KNOWN = {
    "font": "anton-regular",
    "placement": "center",
    "size_percent": 7.0,
    "horizontal": 0.36,
    "vertical": 0.64,
    "color": "#F3D848",
    "outline_color": "#080810",
    "shadow_percent": 0,
}


@pytest.fixture(scope="module")
def sources(tmp_path_factory):
    root = tmp_path_factory.mktemp("appearance-media")
    result = []
    for name, outline, crf in (
        ("clean", 0.0, "0"),
        ("outlined", 0.6, "0"),
        ("compressed", 0.6, "28"),
        ("varying-background", 0.6, "0"),
        ("tonal-gradient", 0.6, "0"),
    ):
        directory = root / name
        directory.mkdir()
        binding = fonts.builtin("anton-regular")
        cue = captions.Cue(start=0, end=1.25, text=TEXT)
        prepared_font = fonts.prepare("fixture", binding, [cue], directory, time.monotonic() + 15)
        track = captions.Track(
            style=captions.Style(**KNOWN, outline_percent=outline), font_binding=binding, cues=[cue]
        )
        subtitle = captions.subtitle_filter(track, directory, 640, 360, prepared_font[:2])
        path = directory / "reference.mp4"
        background = "color=c=0x204020:s=640x360:r=30:d=1.25"
        if name == "varying-background":
            background += ",drawbox=x=0:y=0:w=320:h=360:color=0x146414:t=fill"
            background += ",drawbox=x=320:y=0:w=320:h=360:color=0x5a4114:t=fill"
        if name == "tonal-gradient":
            background += ",format=rgb24,geq=r=20+70*X/W:g=30+70*X/W:b=10+70*X/W"
        subprocess.run(
            [
                shutil.which("ffmpeg"),
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                background,
                "-vf",
                subtitle,
                "-c:v",
                "libx264",
                "-crf",
                crf,
                "-pix_fmt",
                "yuv420p",
                str(path),
            ],
            capture_output=True,
            check=True,
            timeout=15,
        )
        result.append(path)
    return result


def selected(client, directory, source):
    project, _, _ = prepared(client, directory, source)
    pid = project["id"]
    catalog = match.catalog_hash(pid)
    with projects.database() as db:
        row = db.execute("SELECT * FROM reference_operations WHERE project_id=?", (pid,)).fetchone()
        media = json.loads(row["metadata"])
        value = match.Selection(
            method=match.METHOD,
            source={
                "video_id": project["reference"]["video_id"],
                "canonical_url": project["reference"]["canonical_url"],
                "media_sha256": media["sha256"],
            },
            reference_operation_id=row["operation_id"],
            requested_timestamp_seconds=1,
            timestamp_seconds=1,
            rectangle=match.Rectangle(x=0.05, y=0.4, width=0.75, height=0.4),
            frame_width=640,
            frame_height=360,
            text=TEXT,
            polarity="light",
            catalog_hash=catalog,
            reviewed_font=fonts.builtin("anton-regular"),
        )
        db.execute(
            "INSERT INTO caption_font_matches VALUES (?,?,?)", (pid, 1, value.model_dump_json())
        )
    return pid


def suggest(client, pid, revision=0):
    url = f"/api/projects/{pid}/caption-appearance"
    current = client.get(url).json()["current_selection"]
    style = {"font": "anton-regular", "placement": "center", "alignment": "center"}
    return client.post(
        url,
        json={
            "expected_revision": revision,
            "expected_selection_revision": current["revision"],
            "expected_selection_token": current["token"],
            "base_style": style,
        },
    )


def test_real_clean_outlined_compressed_fit_and_partial_uncertainty(local, sources):
    client, directory, _ = local
    for index, source in enumerate(sources):
        pid = selected(client, directory, source)
        response = suggest(client, pid)
        if index == 4:
            assert response.status_code == 422, response.text
            assert response.json()["error"]["code"] == "appearance_unusable"
            continue
        assert response.status_code == 200, response.text
        data = response.json()
        s = data["suggestion"]
        v = s["values"]
        print("APPEARANCE", index, v, s["outline_status"], s["fitting_attempts"])
        assert abs(v["size_percent"] - KNOWN["size_percent"]) < 0.5
        assert abs(v["horizontal"] - KNOWN["horizontal"]) * 640 < 4
        assert abs(v["vertical"] - KNOWN["vertical"]) * 360 < 4
        ink = tuple(int(v["color"][i : i + 2], 16) for i in (1, 3, 5))
        assert appearance.distance(ink, (243, 216, 72)) < 20
        if index == 0:
            assert s["outline_status"] == "none_detected" and v["outline_percent"] == 0
        elif index == 1:
            assert s["outline_status"] == "measured"
            assert abs(v["outline_percent"] - 0.6) < 0.35
            outline = tuple(int(v["outline_color"][i : i + 2], 16) for i in (1, 3, 5))
            assert appearance.distance(outline, (8, 8, 16)) < 25
        elif index == 3:
            assert s["outline_status"] == "not_estimated"
            assert v["outline_percent"] is None and v["outline_color"] is None
            assert v["color"] is not None
        else:
            assert s["outline_status"] in ("measured", "not_estimated")
        assert data["reconstruction"]["width"] == 640 and data["reference"]["height"] == 360
        assert client.get(f"/api/projects/{pid}/caption-appearance").json()["suggestion"] == s
        assert s["fitting_attempts"] <= appearance.MAX_FITS
    # Uncertain background cannot silently become a measured zero outline.
    raw = bytes(
        v for y in range(20) for x in range(20) for v in ((255, 0, 0) if x < 10 else (0, 0, 255))
    )
    assert appearance.outline(raw, {210, 211, 230, 231}, 20, 20, 360) == (
        "not_estimated",
        None,
        None,
    )
    assert not list((directory / "preview-staging").iterdir())


def test_apply_revision_font_source_and_unusable_selection(local, sources):
    client, directory, _ = local
    pid = selected(client, directory, sources[0])
    url = f"/api/projects/{pid}/caption-appearance"
    result = suggest(client, pid)
    assert result.status_code == 200, result.text
    data = result.json()
    captions_url = f"/api/projects/{pid}/captions"
    before = client.get(captions_url).json()
    base = {
        "font": "anton-regular",
        "placement": "center",
        "alignment": "center",
        "italic": False,
        "bold": False,
    }
    applied = client.post(
        url + "/apply",
        json={
            "expected_revision": 1,
            "token": data["token"],
            "fields": ["color", "size_percent"],
            "base_style": base,
        },
    )
    assert applied.status_code == 200, applied.text
    assert set(applied.json()["patch"]) == {"color", "size_percent"}
    assert client.get(captions_url).json() == before
    assert (
        client.post(
            url + "/apply",
            json={
                "expected_revision": 1,
                "token": data["token"],
                "fields": ["outline_color"],
                "base_style": base,
            },
        ).status_code
        == 422
    )
    assert (
        client.post(
            url + "/apply",
            json={
                "expected_revision": 2,
                "token": data["token"],
                "fields": ["color"],
                "base_style": base,
            },
        ).status_code
        == 409
    )
    with projects.database() as db:
        row = db.execute(
            "SELECT selection FROM caption_font_matches WHERE project_id=?", (pid,)
        ).fetchone()
        selection = json.loads(row[0])
        selection["reviewed_font"] = fonts.builtin("amiri-regular").model_dump(mode="json")
        db.execute(
            "UPDATE caption_font_matches SET selection=? WHERE project_id=?",
            (json.dumps(selection), pid),
        )
    assert client.get(url).json()["status"] == "stale"
    assert (
        client.post(
            url + "/apply",
            json={
                "expected_revision": 1,
                "token": data["token"],
                "fields": ["color"],
                "base_style": base,
            },
        ).status_code
        == 409
    )
    with projects.database() as db:
        selection["reviewed_font"] = fonts.builtin("anton-regular").model_dump(mode="json")
        selection["rectangle"] = {"x": 0, "y": 0, "width": 0.1, "height": 0.1}
        db.execute(
            "UPDATE caption_font_matches SET selection=? WHERE project_id=?",
            (json.dumps(selection), pid),
        )
    bad = suggest(client, pid, 1)
    assert bad.status_code == 422, bad.text
    assert client.get(url).json()["revision"] == 1
    with projects.database() as db:
        db.execute("DELETE FROM reference_operations WHERE project_id=?", (pid,))
    assert client.get(url).json()["status"] == "stale"
