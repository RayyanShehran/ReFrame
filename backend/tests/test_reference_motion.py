import json
import shutil
import subprocess
import time

import pytest

import captions
import font_assets as fonts
import projects
from tests.test_caption_appearance import KNOWN, TEXT, selected
from tests.test_video_render import local as local  # noqa: F401


@pytest.fixture(scope="module")
def sources(tmp_path_factory):
    root = tmp_path_factory.mktemp("reference-motion-media")
    paths = {}
    for mode in ("none", "fade", "pop", "slide-up", "combined"):
        stage = root / mode
        stage.mkdir()
        binding = fonts.builtin("anton-regular")
        cue = captions.Cue(start=0.3, end=2.1, text=TEXT)
        font = fonts.prepare("fixture", binding, [cue], stage, time.monotonic() + 15)
        track = captions.Track(
            style=captions.Style(**KNOWN, outline_percent=0.0),
            font_binding=binding,
            cues=[cue],
            animation=captions.Animation(
                mode=mode if mode != "combined" else "pop",
                entrance_seconds=0.4,
                exit_seconds=0.4,
                initial_scale=0.6,
                displacement=0.08,
            ),
        )
        vf = captions.subtitle_filter(track, stage, 640, 360, font[:2])
        if mode == "combined":
            p = stage / "captions.ass"
            if not p.exists():
                p = next(stage.glob("*.ass"))
            s = p.read_text(encoding="utf-8")
            s = s.replace("\\fscx60", "\\fad(400,400)\\fscx60")
            p.write_text(s, encoding="utf-8")
        target = stage / "reference.mp4"
        subprocess.run(
            [
                shutil.which("ffmpeg"),
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=0x204020:s=640x360:r=30:d=2.4",
                "-vf",
                vf,
                "-c:v",
                "libx264",
                "-crf",
                "0",
                "-pix_fmt",
                "yuv420p",
                str(target),
            ],
            check=True,
            capture_output=True,
            timeout=15,
        )
        paths[mode] = target
    return paths


def request(client, pid, start=0.0, end=2.4, revision=0):
    state = client.get(f"/api/projects/{pid}/caption-appearance").json()
    return {
        "expected_revision": revision,
        "expected_selection_revision": state["current_selection"]["revision"],
        "expected_selection_token": state["current_selection"]["token"],
        "base_style": captions.Style(**KNOWN, outline_percent=0.0).model_dump(mode="json"),
        "start": start,
        "end": end,
        "region": {"x": 0.04, "y": 0.42, "width": 0.8, "height": 0.4},
    }


def test_real_families_and_incomplete_interval(local, sources):
    client, directory, _ = local
    for mode in ("none", "fade", "pop", "slide-up", "combined"):
        pid = selected(client, directory, sources[mode])
        body = request(client, pid)
        response = client.post(f"/api/projects/{pid}/reference-motion", json=body)
        assert response.status_code == 200, response.text
        result = response.json()["suggestion"]
        print(
            mode,
            json.dumps(
                {k: result[k] for k in ("outcome", "values", "cue_start", "cue_end", "notes")}
            ),
        )
        if mode == "combined":
            assert result["outcome"] == "inconclusive"
            assert result["values"]["mode"] is None
        else:
            assert result["values"]["mode"] == mode, result
            if mode != "none":
                assert abs(result["values"]["entrance_seconds"] - 0.4) <= 0.15
            if mode == "fade":
                assert abs(result["values"]["exit_seconds"] - 0.4) <= 0.15
            if mode == "pop":
                assert abs(result["values"]["initial_scale"] - 0.6) <= 0.15
            if mode == "slide-up":
                assert abs(result["values"]["displacement"] - 0.08) <= 0.02
        if mode == "fade":
            partial = client.post(
                f"/api/projects/{pid}/reference-motion",
                json=body | {"expected_revision": 1, "end": 1.5},
            )
            assert partial.status_code == 200, partial.text
            value = partial.json()["suggestion"]
            assert value["outcome"] == "partial", value
            assert value["values"]["exit_seconds"] is None
            assert value["cue_end"] is None
        assert not list((directory / "preview-staging").iterdir())


def test_saved_bindings_and_apply_does_not_edit_captions(local, sources):
    client, directory, _ = local
    pid = selected(client, directory, sources["slide-up"])
    url = f"/api/projects/{pid}/reference-motion"
    body = request(client, pid)
    before = client.get(f"/api/projects/{pid}/captions").json()
    saved = client.post(url, json=body).json()
    assert client.get(url).json() == saved
    apply = body | {"expected_revision": saved["revision"], "token": saved["token"]}
    applied = client.post(url + "/apply", json=apply)
    assert applied.status_code == 200, applied.text
    assert set(applied.json()["patch"]) == {"mode", "entrance_seconds", "displacement"}
    assert client.get(f"/api/projects/{pid}/captions").json() == before
    assert client.post(url + "/apply", json=apply | {"start": 0.1}).status_code == 409
    assert (
        client.post(
            url + "/apply",
            json=apply | {"region": {"x": 0.05, "y": 0.4, "width": 0.8, "height": 0.4}},
        ).status_code
        == 409
    )
    with projects.database() as db:
        row = db.execute(
            "SELECT selection FROM caption_font_matches WHERE project_id=?", (pid,)
        ).fetchone()
        value = json.loads(row[0])
        value["reviewed_font"] = fonts.builtin("amiri-regular").model_dump(mode="json")
        db.execute(
            "UPDATE caption_font_matches SET revision=revision+1,selection=? WHERE project_id=?",
            (json.dumps(value), pid),
        )
    assert client.get(url).json()["status"] == "stale"
    assert client.post(url + "/apply", json=apply).status_code == 409
    assert (
        client.post(
            url,
            content=json.dumps(body | {"start": float("nan")}),
            headers={"Content-Type": "application/json"},
        ).status_code
        == 422
    )
