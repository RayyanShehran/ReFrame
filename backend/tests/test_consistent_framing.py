import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

import framing
from references import ReferenceError
from tests.test_color_recipe import seed_analyses
from tests.test_framing import local as local  # noqa: F401


def picture(width=100, height=100, left=0, top=0, right=0, bottom=0, dark=False):
    return bytes(
        0
        if x < left or x >= width - right or y < top or y >= height - bottom
        else (8 if dark else 70 + (x + y) % 100)
        for y in range(height)
        for x in range(width)
    )


@pytest.mark.parametrize("edges", [(0, 20, 0, 20), (20, 0, 20, 0), (10, 12, 8, 14)])
def test_persistent_both_axes_and_dark_scene_guard(edges):
    data = picture(left=edges[0], top=edges[1], right=edges[2], bottom=edges[3])
    bounds, note = framing.detect_borders([data] * 5, 100, 100)
    assert bounds == pytest.approx(tuple(max(0, e / 100 - 0.008) for e in edges))
    assert "removed" in note
    assert framing.detect_borders([picture(dark=True)] * 5, 100, 100)[0] == (0, 0, 0, 0)
    assert framing.detect_borders([data] * 3 + [picture()] * 2, 100, 100)[0] == (0, 0, 0, 0)


def test_border_text_is_not_treated_as_disposable_black_and_no_partial_decode():
    image = bytearray(picture(top=20, bottom=20))
    for y in range(10, 15):
        image[y * 100 + 40 : y * 100 + 60] = bytes([220]) * 20
    insets, _ = framing.detect_borders([image] * 5, 100, 100)
    # It stops above the text, retaining every letter rather than cropping to the inner image.
    assert insets[1] < 0.1
    with pytest.raises(ValueError):
        framing.detect_borders([b""], 100, 100)


def test_slot_routing_stable_crop_manual_position_fallback_and_stale_range():
    sid, cid = uuid4(), uuid4()
    slot = SimpleNamespace(
        id=sid, clip_id=cid, source_hash="a" * 64, source_start_frame=30, source_end_frame=90
    )
    decision = framing.Decision(
        clip_id=cid,
        source_hash="a" * 64,
        start_frame=30,
        end_frame=90,
        insets=(0, 0.2, 0, 0.2),
        note="sampled",
    )
    settings = framing.Settings(
        format="square", fit="automatic", bars="auto", decisions={str(sid): decision}
    )
    result = framing.transform({"width": 640, "height": 600}, settings, slot)
    assert "crop=640:360:0:120" in result[0]
    assert result[2] == (720, 720)
    assert "Fit:" in result[-1]
    settings.slots[sid] = framing.SlotChoices(fit="fill", horizontal=1, zoom=1.1, bars="off")
    active, scale, canvas, final, _ = framing.transform(
        {"width": 640, "height": 600}, settings, slot
    )
    assert not active and canvas == (720, 720)
    assert (
        final == f"crop=720:720:{round((scale[0] - 720) / 2) * 2}:{round((scale[1] - 720) / 4) * 2}"
    )
    assert framing.transform({"width": 640, "height": 600}, settings, slot) == framing.transform(
        {"width": 640, "height": 600}, settings, slot
    )
    slot.source_end_frame = 91
    with pytest.raises(ReferenceError, match="framing evidence is stale"):
        framing.transform({"width": 640, "height": 600}, settings, slot)


def test_saved_whole_override_restoration_revisions_and_analysis_preservation(local):
    client, directory, _ = local
    saved, _ = seed_analyses(client, directory)
    pid = saved["id"]
    base = f"/api/projects/{pid}"
    before = [
        client.get(base + "/" + route).json()
        for route in ["captions", "audio", "style-blueprint", "footage-color"]
    ]
    response = client.post(
        base + "/framing",
        json={"expected_revision": 0, "format": "square", "fit": "automatic", "bars": "off"},
    )
    assert response.status_code == 200, response.text
    assert client.get(base + "/framing").json() == response.json()
    assert client.post(base + "/framing", json={"expected_revision": 0}).status_code == 409
    assert before == [
        client.get(base + "/" + route).json()
        for route in ["captions", "audio", "style-blueprint", "footage-color"]
    ]
    assert (
        client.post(base + "/framing", json={"expected_revision": 1, "decisions": {}}).status_code
        == 422
    )
    loaded = framing.Settings.model_validate_json(json.dumps(response.json()["settings"]))
    assert loaded.decisions["whole"].source_hash
