import shutil
import subprocess
import time

import captions
import font_assets as fonts
from tests.test_color_recipe import local as local  # noqa: F401
from tests.test_color_recipe import seed_analyses


def test_save_restores_cue_appearance_resolves_font_server_side(local):
    client, directory, _ = local
    project = seed_analyses(client, directory)[0]["id"]
    url = f"/api/projects/{project}/captions"
    body = {
        "expected_revision": 0,
        "mode": "whole",
        "enabled": True,
        "cues": [
            {
                "start": 0.0,
                "end": 1.0,
                "text": "First",
                "appearance": {
                    "style": {"font": "poppins-bold", "color": "yellow"},
                    "font_binding": fonts.Binding().model_dump(mode="json"),
                },
            },
            {"start": 1.0, "end": 1.2, "text": "Second"},
        ],
    }
    response = client.post(url, json=body)
    assert response.status_code == 200, response.text
    track = response.json()["track"]
    assert track["schema_version"] == 2
    assert track["cues"][0]["appearance"]["font_binding"] == fonts.builtin(
        "poppins-bold"
    ).model_dump(mode="json", exclude_none=True)
    assert track["cues"][1] == body["cues"][1]
    assert client.get(url).json()["track"] == track
    assert client.post(url, json=body).status_code == 409
    body["expected_revision"] = 1
    body["cues"] = track["cues"]
    body["cues"][0]["text"] = "Edited"
    assert (
        client.post(url, json=body).json()["track"]["cues"][0]["appearance"]
        == (track["cues"][0]["appearance"])
    )


def test_real_cue_styles_arabic_and_literal_text_on_final_canvas(tmp_path):
    first = fonts.builtin("poppins-bold")
    second = fonts.builtin("amiri-bold")
    track = captions.Track(
        schema_version=2,
        enabled=True,
        cues=[
            captions.Cue(
                start=0.0,
                end=0.5,
                text="First {\\b1}",
                appearance=captions.Appearance(
                    style=captions.Style(
                        font=first.choice,
                        color="yellow",
                        size_percent=12.0,
                        horizontal=0.5,
                        vertical=0.3,
                    ),
                    font_binding=first,
                ),
            ),
            captions.Cue(
                start=0.5,
                end=1.0,
                text="\u0645\u0631\u062d\u0628\u0627",
                appearance=captions.Appearance(
                    style=captions.Style(
                        font=second.choice,
                        color="white",
                        size_percent=12.0,
                        horizontal=0.5,
                        vertical=0.8,
                    ),
                    font_binding=second,
                ),
            ),
        ],
    )
    prepared = fonts.prepare_track("fixture", track, tmp_path, time.monotonic() + 20)
    subtitle = captions.subtitle_filter(track, tmp_path, 320, 240, prepared[:2])
    ass = (tmp_path / "captions.ass").read_text(encoding="utf-8")
    assert "Style: Cue0," in ass and "Style: Cue1," in ass
    assert "\\{\\\u2060b1\\}" in ass
    raw = subprocess.run(
        [
            shutil.which("ffmpeg"),
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=black:s=320x240:r=30:d=1",
            "-vf",
            subtitle,
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "-",
        ],
        capture_output=True,
        check=True,
        timeout=20,
    ).stdout
    assert len(raw) == 30 * 320 * 240 * 3
    frames = [raw[index * 320 * 240 * 3 : (index + 1) * 320 * 240 * 3] for index in (7, 22)]
    yellow = [
        (i // 320)
        for i, (r, g, b) in enumerate(zip(frames[0][0::3], frames[0][1::3], frames[0][2::3]))
        if r > 160 and g > 160 and b < 80
    ]
    white = [
        (i // 320)
        for i, (r, g, b) in enumerate(zip(frames[1][0::3], frames[1][1::3], frames[1][2::3]))
        if min(r, g, b) > 160
    ]
    assert len(yellow) > 100 and len(white) > 20
    assert sum(yellow) / len(yellow) < 100
    assert sum(white) / len(white) > 150
    print(
        "CUE_APPEARANCE",
        len(yellow),
        len(white),
        sum(yellow) / len(yellow),
        sum(white) / len(white),
    )
