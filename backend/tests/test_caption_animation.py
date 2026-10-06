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
