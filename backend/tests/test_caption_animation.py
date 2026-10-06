"""A single reusable real clip exercises numeric ASS animation and saved revisions."""

import json

import pytest
from pydantic import ValidationError

import captions
import projects
import video_render as render
from tests.test_caption_style import setup  # noqa: F401
from tests.test_caption_style import source as source
from tests.test_color_analysis import finish
from tests.test_framing_render import frame
from tests.test_video_render import local as local  # noqa: F401


def test_animation_defaults_bounds_revision_and_preservation(local):
    from tests.test_color_recipe import seed_analyses

    client, directory, _ = local
    pid = seed_analyses(client, directory)[0]["id"]
    url = f"/api/projects/{pid}/captions"
    body = {
        "expected_revision": 0,
        "mode": "whole",
        "enabled": True,
        "cues": [{"start": 0.3, "end": 0.9, "text": r"literal {\b1}"}],
        "provenance": "srt_import",
    }
    first = client.post(url, json=body).json()["track"]
    assert first["animation"]["mode"] == "none"
    with projects.database() as db:
        legacy = dict(first)
        legacy.pop("animation")
        db.execute(
            "UPDATE caption_tracks SET track=? WHERE project_id=?", (json.dumps(legacy), pid)
        )
    assert client.get(url).json()["track"]["animation"]["mode"] == "none"
    saved = client.post(url, json=body | {"expected_revision": 1, "animation": {"mode": "pop"}})
    assert saved.status_code == 200, saved.text
    track = saved.json()["track"]
    assert client.get(url).json()["track"] == track
    assert track["cues"] == first["cues"] and track["provenance"] == first["provenance"]
    assert track["timeline"] == first["timeline"] and track["style"] == first["style"]
    assert client.post(url, json=body).status_code == 409
    for invalid in (
        {"mode": "invented"},
        {"initial_scale": 0.49},
        {"displacement": 0.26},
        {"entrance_seconds": True},
        {"exit_seconds": "NaN"},
    ):
        with pytest.raises(ValidationError):
            captions.Animation.model_validate(invalid)


def ink(raw):
    points = [
        (i % 480, i // 480) for i in range(480 * 60, 480 * 200) if min(raw[i * 3 : i * 3 + 3]) > 220
    ]
    energy = sum(max(0, raw[i * 3] - 60) for i in range(480 * 60, 480 * 200))
    return energy, (
        min(x for x, y in points),
        min(y for x, y in points),
        max(x for x, y in points),
        max(y for x, y in points),
    ) if points else None


def test_real_none_fade_pop_slide_short_cues_and_export_lifetime(local, source):
    client, directory, _ = local
    pid = setup(client, directory, source)
    base = f"/api/projects/{pid}"
    body = {
        "mode": "whole",
        "enabled": True,
        "style": {
            "placement": "center",
            "horizontal": 0.5,
            "vertical": 0.45,
            "size_percent": 9,
            "outline_percent": 0.5,
            "shadow_percent": 0.3,
        },
        "cues": [
            {"start": 0.3, "end": 1.8, "text": "Motion"},
            {"start": 2.3, "end": 2.34, "text": "Short"},
        ],
        "provenance": "srt_import",
    }
    results = {}
    for revision, mode in enumerate(("none", "fade", "pop", "slide-up"), 1):
        before = client.get(base + "/render").json().get("output")
        saved = client.post(
            base + "/captions",
            json=body
            | {
                "expected_revision": revision - 1,
                "animation": {
                    "mode": mode,
                    "entrance_seconds": 0.4,
                    "exit_seconds": 0.4,
                    "initial_scale": 0.5,
                },
            },
        )
        assert saved.status_code == 200, saved.text
        if before:
            state = client.get(base + "/render").json()
            assert state["outdated"] and state["output"]["output_id"] == before["output_id"]
        assert (
            client.post(
                base + "/render",
                json={"expected_revision": 1, "expected_caption_revision": revision},
            ).status_code
            == 202
        )
        state = finish(client, base + "/render")
        assert state["status"] == "ready", state
        out = render.Output.model_validate(state["output"])
        assert out.has_audio and abs(out.duration_seconds - 4) < 0.05
        assert out.spec.captions.cues == [captions.Cue.model_validate(c) for c in body["cues"]]
        path = render.destination(pid, out.output_id)
        samples = {
            t: ink(frame(path, second=t, width=480, height=270))
            for t in (0.2, 0.3, 0.4, 0.8, 1.7, 1.9, 2.3)
        }
        assert samples[0.2][1] is None and samples[1.9][1] is None
        assert samples[2.3][1] is not None  # sub-three-frame cues stay visible/static
        results[mode] = samples
        print("ANIMATION", mode, samples)
    static = results["none"]
    assert results["fade"][0.3][0] < results["fade"][0.4][0] < static[0.8][0] * 0.7
    assert results["fade"][1.7][0] < static[1.7][0] * 0.7
    for mode in ("fade", "pop", "slide-up"):
        assert results[mode][0.8][1] == static[0.8][1]
    pop = results["pop"][0.3][1]
    full = static[0.3][1]
    assert pop[2] - pop[0] < (full[2] - full[0]) * 0.7
    assert results["slide-up"][0.3][1][1] > full[1] + 15
    assert not list((directory / "render-staging").iterdir())


def test_real_motion_preview_crosses_cut_preserves_phase_and_supports_fonts(local, source):
    import base64

    import font_assets
    from tests.test_edit_plan import seed_pacing
    from tests.test_font_assets import upload

    client, directory, _ = local
    pid = setup(client, directory, source)
    base = f"/api/projects/{pid}"
    seed_pacing(pid)
    assert (
        client.post(base + "/edit-plan/generate", json={"expected_revision": 0}).status_code == 200
    )
    changed = client.post(
        base + "/edit-plan", json={"expected_revision": 1, "source_starts_seconds": [0, 2, 3]}
    )
    assert changed.status_code == 200, changed.text
    body = {
        "mode": "cuts",
        "expected_plan_revision": 2,
        "enabled": True,
        "style": {
            "font": "anton-regular",
            "placement": "center",
            "horizontal": 0.5,
            "vertical": 0.45,
            "size_percent": 9,
            "outline_percent": 0.5,
        },
        "animation": {"mode": "pop", "entrance_seconds": 0.4, "initial_scale": 0.5},
        "cues": [{"start": 0.6, "end": 1.5, "text": "Motion"}],
    }
    req = {
        "cue_index": 0,
        "expected_recipe_revision": 1,
        "expected_framing_revision": 0,
        "expected_caption_revision": 1,
        "expected_plan_revision": 2,
    }
    url = base + "/caption-motion-preview"
    saved = client.post(base + "/captions", json=body | {"expected_revision": 0})
    assert saved.status_code == 200, saved.text
    unchanged = client.get(base + "/render").json()
    response = client.post(url, json=req)
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["silent"] and value["start_frame"] == 12 and value["end_frame"] == 51
    assert value["decoded_frames"] == 39 and value["duration_seconds"] == 1.3
    assert client.get(base + "/render").json() == unchanged
    path = directory / "preview.mp4"
    path.write_bytes(base64.b64decode(value["video_base64"]))
    early = ink(frame(path, second=0.2, width=480, height=270))[1]
    settled = ink(frame(path, second=0.7, width=480, height=270))[1]
    assert early[2] - early[0] < (settled[2] - settled[0]) * 0.7
    assert ink(frame(path, second=0, width=480, height=270))[1] is None
    assert ink(frame(path, second=1.2, width=480, height=270))[1] is None
    green = frame(path, second=0.5, width=480, height=270)[:3]
    blue = frame(path, second=0.7, width=480, height=270)[:3]
    assert green[1] > green[2] + 10 and blue[2] > blue[1] + 25
    print(
        "CUT MOTION",
        value["start_frame"],
        value["end_frame"],
        value["decoded_frames"],
        value["size_bytes"],
        early,
        settled,
        tuple(green),
        tuple(blue),
    )
    assert (
        upload(client, pid, font_assets.BUNDLED.read_bytes(), caption_revision=1).status_code == 200
    )
    body["style"]["font"] = "custom"
    saved = client.post(base + "/captions", json=body | {"expected_revision": 1})
    assert saved.status_code == 200, saved.text
    assert client.post(url, json=req).status_code == 409
    framing = client.post(base + "/framing", json={"expected_revision": 0, "format": "square"})
    assert framing.status_code == 200
    custom = client.post(
        url, json=req | {"expected_caption_revision": 2, "expected_framing_revision": 1}
    )
    assert custom.status_code == 200, custom.text
    assert custom.json()["spec"]["captions"]["font_binding"]["kind"] == "custom"
    assert (custom.json()["width"], custom.json()["height"]) == (640, 640)
    assert not list((directory / "preview-staging").iterdir())


def test_motion_bounds_timeout_and_stale_publication(local, source, monkeypatch):
    import caption_motion as motion
    import caption_preview
    import reference_engine as engine

    client, directory, _ = local
    pid = setup(client, directory, source)
    base = f"/api/projects/{pid}"
    body = {
        "expected_revision": 0,
        "mode": "whole",
        "enabled": True,
        "cues": [{"start": 0.3, "end": 1.8, "text": "Caption"}],
    }
    assert client.post(base + "/captions", json=body).status_code == 200
    req = {
        "cue_index": 0,
        "expected_recipe_revision": 1,
        "expected_framing_revision": 0,
        "expected_caption_revision": 1,
    }
    before = client.get(base + "/captions").json()
    url = base + "/caption-motion-preview"
    original = caption_preview.snapshot
    calls = 0

    def stale(*args):
        nonlocal calls
        calls += 1
        spec = original(*args)
        return spec.model_copy(update={"recipe_revision": 2}) if calls > 1 else spec

    monkeypatch.setattr(caption_preview, "snapshot", stale)
    assert client.post(url, json=req).json()["error"]["code"] == "source_changed"
    monkeypatch.setattr(caption_preview, "snapshot", original)
    original_tool = motion.preview.tool

    def timeout(*args, **kwargs):
        raise engine.ProbeTimeout()

    monkeypatch.setattr(motion.preview, "tool", timeout)
    assert client.post(url, json=req).status_code == 408
    monkeypatch.setattr(motion.preview, "tool", original_tool)
    monkeypatch.setattr(motion, "MAX_BYTES", 1)
    assert client.post(url, json=req).status_code == 413
    assert before == client.get(base + "/captions").json()
    assert not list((directory / "preview-staging").iterdir())
    assert motion.interval(captions.Cue(start=0, end=100, text="Long"), 3000) == (0, 120)
