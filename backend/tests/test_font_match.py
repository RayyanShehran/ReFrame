import shutil
import subprocess
import time

import pytest

import captions
import font_assets as fonts
import font_match as match
import projects
import video_render as render
from references import ReferenceError
from tests.test_color_analysis import finish
from tests.test_font_assets import upload
from tests.test_frame_preview import pixels
from tests.test_framing_render import frame
from tests.test_video_render import local as local  # noqa: F401
from tests.test_video_render import prepared

TEXT = "REFRAME Caption"


@pytest.fixture(scope="module")
def sources(tmp_path_factory):
    root = tmp_path_factory.mktemp("font-match-media")
    result = []
    for name, outline, crf in (("clean", 0.0, "0"), ("outlined", 0.8, "28")):
        directory = root / name
        directory.mkdir()
        binding = fonts.builtin("anton-regular")
        cue = captions.Cue(start=0, end=1.25, text=TEXT)
        font = fonts.prepare("fixture", binding, [cue], directory, time.monotonic() + 15)
        track = captions.Track(
            style=captions.Style(
                font="anton-regular", size_percent=10.0, placement="center", outline_percent=outline
            ),
            font_binding=binding,
            cues=[cue],
        )
        subtitle = captions.subtitle_filter(track, directory, 640, 360, font[:2])
        path = directory / "reference.mp4"
        subprocess.run(
            [
                shutil.which("ffmpeg"),
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=0x204020:s=640x360:r=30:d=1.25",
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
            check=True,
            capture_output=True,
            timeout=15,
        )
        result.append(path)
    return result


def request(client, pid, revision=0, **changes):
    media = client.get(f"/api/projects/{pid}/reference-media").json()
    return {
        "expected_revision": revision,
        "expected_reference_operation_id": media["operation_id"],
        "expected_source_hash": media["media"]["sha256"],
        "timestamp_seconds": 1.0,
        "rectangle": {"x": 0.1, "y": 0.3, "width": 0.8, "height": 0.4},
        "text": TEXT,
        "polarity": "light",
    } | changes


def test_known_font_ranking_review_preview_export_and_restore(local, sources):
    client, directory, _ = local
    saved, _, _ = prepared(client, directory, sources[0])
    pid = saved["id"]
    url = f"/api/projects/{pid}/font-match"
    custom = upload(client, pid, fonts.BUNDLED.read_bytes())
    assert custom.status_code == 200
    custom_before = custom.json()
    assert len(match.candidates(pid)) == 5
    assert client.get(url).json()["status"] == "empty"
    result = client.post(url, json=request(client, pid))
    assert result.status_code == 200, result.text
    data = result.json()
    scores = [(c["font"]["candidate_id"], c["visual_similarity"]) for c in data["ranked"]]
    print("CLEAN_RANKING", scores)
    assert scores[0][0] == "anton-regular", scores
    assert scores[0][1] > 70 and scores[-1][1] < scores[0][1] - 5
    chosen = data["ranked"][0]["font"]
    applied = client.post(
        url + "/review",
        json={
            "expected_revision": 1,
            "token": data["token"],
            "choice": "anton-regular",
            "expected_font_hash": chosen["sha256"],
        },
    )
    assert applied.status_code == 200, applied.text
    assert client.get(f"/api/projects/{pid}/caption-font").json() == custom_before
    before = client.get(f"/api/projects/{pid}/captions").json()["track"]
    cue = {"start": 0.2, "end": 1.1, "text": TEXT}
    cap = client.post(
        f"/api/projects/{pid}/captions",
        json={
            "expected_revision": 0,
            "mode": "whole",
            "enabled": True,
            "style": {"font": "anton-regular", "font_origin": "assisted"},
            "cues": [cue],
        },
    )
    assert cap.status_code == 200, cap.text
    assert cap.json()["track"]["font_binding"] == chosen
    assert client.get(url).json()["selection"]["reviewed_font"] == chosen
    # Reload and migration preserve the project font/caption records.
    projects.initialize()
    assert client.get(url).json()["status"] == "ready"
    assert client.get(f"/api/projects/{pid}/captions").json()["track"]["cues"] == [cue]
    assert before["cues"] == []
    preview = client.post(
        f"/api/projects/{pid}/caption-preview",
        json={
            "expected_recipe_revision": 1,
            "expected_caption_revision": 1,
            "expected_framing_revision": 0,
            "cue_index": 0,
        },
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["font"] == chosen and preview.json()["font_origin"] == "assisted"
    endpoint = f"/api/projects/{pid}/render"
    assert (
        client.post(
            endpoint, json={"expected_revision": 1, "expected_caption_revision": 1}
        ).status_code
        == 202
    )
    output = finish(client, endpoint)["output"]
    assert output["spec"]["captions"]["font_binding"] == chosen
    actual = frame(render.destination(pid, output["output_id"]), second=1.0, width=640, height=360)
    png = pixels(preview.json()["image"], directory, "match-preview")
    assert sum(abs(a - b) for a, b in zip(actual, png)) / len(png) < 3
    assert (
        client.post(
            url + "/review",
            json={
                "expected_revision": 1,
                "token": "0" * 64,
                "choice": "anton-regular",
                "expected_font_hash": chosen["sha256"],
            },
        ).status_code
        == 409
    )
    assert not list((directory / "preview-staging").iterdir())


def test_compressed_outline_and_failed_selection_preserve_review(local, sources):
    client, directory, _ = local
    saved, _, _ = prepared(client, directory, sources[1])
    pid = saved["id"]
    url = f"/api/projects/{pid}/font-match"
    response = client.post(url, json=request(client, pid))
    assert response.status_code == 200, response.text
    scores = [
        (c["font"]["candidate_id"], c["visual_similarity"]) for c in response.json()["ranked"]
    ]
    print("OUTLINED_RANKING", scores)
    assert scores[0][0] == "anton-regular" and scores[0][1] > 60
    body = request(client, pid, 1, rectangle={"x": 0.0, "y": 0.0, "width": 0.1, "height": 0.1})
    failed = client.post(url, json=body)
    assert failed.status_code == 422 and failed.json()["error"]["code"] == "no_useful_font_match"
    assert client.get(url).json()["revision"] == 1
    assert client.post(url, json=request(client, pid, 1, text=" ")).status_code == 422
    assert client.post(url, json=request(client, pid, 0)).status_code == 409
    assert (
        client.post(url, json=request(client, pid, 1, expected_source_hash="0" * 64)).status_code
        == 409
    )
    with projects.database() as db:
        db.execute("DELETE FROM reference_operations WHERE project_id=?", (pid,))
    assert client.get(url).json()["status"] == "stale"


def test_shape_normalization_rejects_solid_and_flat_backgrounds():
    for data in (bytes([127]) * 400, bytes([255]) * 400, bytes([0]) * 400):
        with pytest.raises(ReferenceError):
            match.mask(data, 20, 20)
