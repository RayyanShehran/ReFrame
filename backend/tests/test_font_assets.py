import pytest

import captions
import font_assets as fonts
import projects
from tests.test_captions import local as local  # noqa: F401
from tests.test_color_recipe import seed_analyses


def upload(client, pid, raw, revision=0, caption_revision=0, replace=False):
    return client.post(
        f"/api/projects/{pid}/caption-font",
        params={
            "expected_revision": revision,
            "expected_caption_revision": caption_revision,
            "replace": str(replace).lower(),
        },
        content=raw,
        headers={"Content-Type": "application/octet-stream"},
    )


def test_real_font_atomic_replacement_restart_binding_and_deletion(local):
    client, directory, _ = local
    saved, _ = seed_analyses(client, directory)
    pid = saved["id"]
    raw = fonts.BUNDLED.read_bytes()  # Licensed static fixture; no font download.
    first = upload(client, pid, raw)
    assert first.status_code == 200, first.text
    result = first.json()
    assert result["font"]["family"] == "DejaVu Sans"
    assert result["font"]["sha256"] == fonts.BUNDLED_HASH
    assert upload(client, pid, raw).status_code == 409
    url = f"/api/projects/{pid}/captions"
    body = {
        "expected_revision": 0,
        "mode": "whole",
        "enabled": True,
        "style": {"font": "custom", "size_percent": 7, "color": "#FFEEDD"},
        "cues": [{"start": 0, "end": 0.8, "text": "Hello مرحبا {\\b1}"}],
    }
    response = client.post(url, json=body)
    assert response.status_code == 200, response.text
    before = response.json()["track"]
    assert before["font_binding"] == result["font"]
    bad = upload(client, pid, b"not a font", 1, 1, True)
    assert bad.status_code == 422
    assert client.get(f"/api/projects/{pid}/caption-font").json() == result
    assert client.get(url).json()["track"] == before
    assert not list((directory / "preview-staging").iterdir())
    assert upload(client, pid, raw, 1, 1).json()["error"]["code"] == "font_replace_required"
    second = upload(client, pid, raw, 1, 1, True)
    assert second.status_code == 200, second.text
    after = client.get(url).json()["track"]
    assert after["revision"] == 2 and after["font_binding"] != before["font_binding"]
    assert all(
        after[k] == before[k] for k in ("cues", "timeline", "provenance", "automatic_binding")
    )
    projects.initialize()
    assert client.get(url).json()["track"] == after
    assert client.get(f"/api/projects/{pid}/caption-font").json() == second.json()
    assert client.delete(f"/api/projects/{pid}").status_code == 204
    with projects.database() as db:
        assert db.execute("SELECT count(*) FROM project_fonts").fetchone()[0] == 0


def test_font_limits_unavailable_glyphs_and_no_silent_fallback(local):
    client, directory, _ = local
    saved, _ = seed_analyses(client, directory)
    pid = saved["id"]
    assert upload(client, pid, b"x" * (fonts.MAX_BYTES + 1)).status_code == 413
    assert upload(client, pid, b"ttcf" + bytes(128)).status_code == 422
    url = f"/api/projects/{pid}/captions"
    body = {
        "expected_revision": 0,
        "mode": "whole",
        "enabled": True,
        "style": {"font": "custom"},
        "cues": [{"start": 0, "end": 0.8, "text": "Hello"}],
    }
    assert client.post(url, json=body).json()["error"]["code"] == "caption_font_unavailable"
    assert upload(client, pid, fonts.BUNDLED.read_bytes()).status_code == 200
    bad = body | {"cues": [{"start": 0, "end": 0.8, "text": "\U0010ffff"}]}
    assert client.post(url, json=bad).json()["error"]["code"] == "unsupported_font_glyph"
    assert client.post(url, json=body).status_code == 200
    with projects.database() as db:
        db.execute("UPDATE project_fonts SET content=? WHERE project_id=?", (b"changed", pid))
    assert client.get(url).json()["font_available"] is False
    assert client.post(url, json=body | {"expected_revision": 1}).status_code == 409
    recovered = client.post(url, json=body | {"expected_revision": 1, "style": {"font": "default"}})
    assert recovered.status_code == 200  # Font correction does not rebind timing.


def test_legacy_style_ass_defaults_and_literal_text(tmp_path):
    track = captions.Track.model_validate(
        {
            "cues": [{"start": 0.1, "end": 1, "text": r"{\pos(0,0)}literal"}],
            "style": {"size": "large", "color": "yellow", "placement": "center"},
        }
    )
    captions.subtitle_filter(track, tmp_path, 480, 270)
    ass = (tmp_path / "captions.ass").read_text(encoding="utf-8")
    assert (
        "Style: Default,DejaVu Sans,18.900,&H0000FFFF,&H0000FFFF,&H00000000,"
        "&H00000000,0,0,0,0,100,100,0,0,1,0.810,0.000,5,19,19,16,-1"
    ) in ass
    assert r"\{" in ass and r"{\pos(0,0)}" not in ass


@pytest.mark.parametrize(
    "style",
    [
        {"size_percent": 1},
        {"size_percent": 16},
        {"outline_percent": -1},
        {"shadow_percent": 4},
        {"horizontal": 1.1},
        {"vertical": "nan"},
        {"bold": 1},
        {"color": "red,\\pos(0,0)"},
    ],
)
def test_invalid_style_bounds(style):
    with pytest.raises(ValueError):
        captions.Style(**style)
