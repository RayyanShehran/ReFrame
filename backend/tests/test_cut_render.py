"""One identifiable four-section fixture: skip green, preserve tones, pad late silence."""

import array
import math
import shutil
import subprocess
import threading
import time

import pytest

import edit_plan
import projects
import video_render as render
from references import ReferenceError
from tests.test_color_analysis import finish, generated
from tests.test_edit_plan import seed_pacing
from tests.test_video_render import local as local  # noqa: F401
from tests.test_video_render import prepared, selected


def fixture(path):
    silent = path.with_name("sections.mp4")
    generated(silent, [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)])
    subprocess.run(
        [
            shutil.which("ffmpeg"),
            "-v",
            "error",
            "-i",
            str(silent),
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:sample_rate=48000:duration=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1320:sample_rate=48000:duration=1",
            "-filter_complex",
            "[1:a][2:a][3:a]concat=n=3:v=0:a=1[a]",
            "-map",
            "0:v",
            "-map",
            "[a]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=15,
    )


def test_real_cuts_skip_content_preserve_order_timing_audio_and_revisions(local):
    client, directory, _ = local
    source = directory / "with-tones.mp4"
    fixture(source)
    saved, recipe_url, _ = prepared(client, directory, source)
    pid = saved["id"]
    revision = selected(client, recipe_url, {}, 0)
    seed_pacing(pid)
    plan_url = f"/api/projects/{pid}/edit-plan"
    assert client.post(plan_url + "/generate", json={"expected_revision": 0}).status_code == 200
    response = client.post(
        plan_url, json={"expected_revision": 1, "source_starts_seconds": [0, 2, 3]}
    )
    assert response.status_code == 200
    url = f"/api/projects/{pid}/render"
    request = {"expected_revision": revision, "expected_plan_revision": 2}
    response = client.post(url, json=request)
    assert response.status_code == 202, response.text
    ready = finish(client, url)
    assert ready["status"] == "ready", (ready["failure_code"], ready["message"])
    output = render.Output.model_validate(ready["output"])
    assert output.spec.edit_plan.revision == 2 and output.spec.recipe_revision == revision
    assert output.decoded_frames == 90 and output.duration_seconds == pytest.approx(3, abs=1 / 30)
    path = render.destination(pid, output.output_id)
    ffmpeg = shutil.which("ffmpeg")
    rgb = subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-i",
            str(path),
            "-vf",
            "fps=1,scale=1:1",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "-",
        ],
        capture_output=True,
        check=True,
        timeout=10,
    ).stdout
    assert len(rgb) == 9
    assert rgb[0] > 200 and rgb[1] < 20 and rgb[2] < 20
    assert rgb[3] < 20 and rgb[4] < 20 and rgb[5] > 200
    assert rgb[6] > 200 and rgb[7] > 200 and rgb[8] < 20
    raw = subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-i",
            str(path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "48000",
            "-f",
            "s16le",
            "-",
        ],
        capture_output=True,
        check=True,
        timeout=10,
    ).stdout
    audio = array.array("h", raw)
    for start, frequency in [(0.3, 440), (1.3, 1320)]:
        samples = audio[int(start * 48000) : int((start + 0.3) * 48000)]
        crossings = sum(a < 0 <= b for a, b in zip(samples, samples[1:]))
        assert crossings / 0.3 == pytest.approx(frequency, abs=10)
    late = audio[120000:134400]
    assert math.sqrt(sum(s * s for s in late) / len(late)) < 5
    assert 144000 <= output.decoded_audio_samples <= 145024
    assert client.get(url).json()["output"] == ready["output"]
    assert client.post(url, json=request).json()["operation_id"] == ready["operation_id"]
    assert (
        client.get(
            f"/api/projects/{pid}/outputs/{output.output_id}/video", headers={"Range": "bytes=0-99"}
        ).status_code
        == 206
    )
    assert (
        client.post(
            plan_url, json={"expected_revision": 2, "source_starts_seconds": [0, 1.5, 3]}
        ).status_code
        == 200
    )
    assert client.get(url).json()["outdated"]
    assert client.post(url, json=request).json()["error"]["code"] == "revision_conflict"


def test_cut_publication_rechecks_pacing_and_old_whole_specs_remain_compatible(local):
    client, directory, _ = local
    saved, _, _ = prepared(client, directory)
    pid = saved["id"]
    seed_pacing(pid)
    edit_plan.generate(pid, edit_plan.GenerateRequest(expected_revision=0))
    operation, source = render.begin_operation(pid, 1, 1)
    old = source["spec"].model_dump()
    old.pop("edit_plan")
    assert render.Spec.model_validate(old).edit_plan is None
    # No encode needed: publication must reject changed pacing before touching output files.
    output = render.Output(
        output_id=operation.operation_id,
        spec=source["spec"],
        size_bytes=1,
        sha256="0" * 64,
        width=64,
        height=64,
        duration_seconds=3,
        has_audio=False,
        decoded_frames=90,
        decoded_audio_samples=0,
        ffmpeg_version="fixture",
        created_at=projects.now(),
    )
    seed_pacing(pid, (1, 2))
    with pytest.raises(render.engine.RetrievalFailure, match="sources changed"):
        render.commit(
            pid,
            operation.operation_id,
            directory / "nonexistent.mp4",
            output,
            threading.Event(),
            time.monotonic() + 10,
        )
    with pytest.raises(ReferenceError, match="valid cut plan"):
        render.specification(pid, 1, 1)
