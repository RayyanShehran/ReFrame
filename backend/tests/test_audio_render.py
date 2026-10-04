"""Real tone outputs exercise audio composition, timeline offsets and saved revisions."""

import array
import json
import math
import shutil
import subprocess

import pytest

import projects
import reference_jobs as jobs
import video_render as render
from tests.test_audio_settings import reference_audio
from tests.test_color_analysis import finish
from tests.test_edit_plan import seed_pacing
from tests.test_video_render import local as local  # noqa: F401
from tests.test_video_render import prepared, selected


def fixtures(directory):
    original, reference = directory / "original.mp4", directory / "soundtrack.mp4"
    common = [shutil.which("ffmpeg"), "-v", "error", "-f", "lavfi", "-i"]
    subprocess.run(
        common
        + [
            "color=c=gray:s=64x64:r=30:d=4",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=4",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(original),
        ],
        check=True,
        capture_output=True,
        timeout=15,
    )
    subprocess.run(
        common
        + [
            "color=c=gray:s=64x64:r=30:d=3",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:sample_rate=48000:duration=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1320:sample_rate=48000:duration=1",
            "-filter_complex",
            "[0:v]setpts=PTS+2/TB[v];[1:a][2:a]concat=n=2:v=0:a=1,asetpts=PTS+2.25/TB[a]",
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-copyts",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(reference),
        ],
        check=True,
        capture_output=True,
        timeout=15,
    )
    return original, reference


def setup(client, directory):
    original, reference = fixtures(directory)
    saved, recipe, _ = prepared(client, directory, original)
    pid = saved["id"]
    with projects.database() as connection:
        row = connection.execute(
            "SELECT * FROM reference_operations WHERE project_id=?", (pid,)
        ).fetchone()
        target = jobs.destination(pid, row["operation_id"])
        shutil.copyfile(reference, target)
        digest = render.footage.digest(target)
        metadata = json.loads(row["metadata"]) | {
            "size_bytes": target.stat().st_size,
            "sha256": digest,
            "duration_seconds": 3,
        }
        connection.execute(
            "UPDATE reference_operations SET metadata=? WHERE project_id=?",
            (json.dumps(metadata), pid),
        )
        row = connection.execute(
            "SELECT blueprint FROM color_operations WHERE project_id=?", (pid,)
        ).fetchone()
        blueprint = json.loads(row[0])
        blueprint["source"]["media_sha256"] = digest
        connection.execute(
            "UPDATE color_operations SET blueprint=?,source_hash=? WHERE project_id=?",
            (json.dumps(blueprint), digest, pid),
        )
    reference_audio(pid)
    response = client.post(recipe + "/generate", json={"expected_revision": 1, "replace": True})
    assert response.status_code == 200, response.text
    revision = selected(client, recipe, {}, 0)
    seed_pacing(pid)
    return saved, revision


def samples(path):
    raw = subprocess.run(
        [
            shutil.which("ffmpeg"),
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
            "f32le",
            "-",
        ],
        check=True,
        capture_output=True,
        timeout=15,
    ).stdout
    return array.array("f", raw)


def amplitude(audio, start, frequency):
    part = audio[round(start * 48000) : round((start + 0.1) * 48000)]
    return (
        2
        * abs(
            sum(
                x
                * complex(
                    math.cos(i * 2 * math.pi * frequency / 48000),
                    math.sin(i * 2 * math.pi * frequency / 48000),
                )
                for i, x in enumerate(part)
            )
        )
        / len(part)
    )


def test_real_replacement_offset_continuous_cuts_mix_padding_mute_and_revisions(local):
    client, directory, _ = local
    saved, recipe_revision = setup(client, directory)
    pid = saved["id"]
    audio_url, render_url = f"/api/projects/{pid}/audio", f"/api/projects/{pid}/render"
    revision = 0

    def output(mode, offset=0, cuts=False, original=100, reference=100):
        nonlocal revision
        response = client.post(
            audio_url,
            json={
                "expected_revision": revision,
                "mode": mode,
                "reference_offset_seconds": offset,
                "original_volume": original,
                "reference_volume": reference,
            },
        )
        assert response.status_code == 200, response.text
        revision += 1
        request = {"expected_revision": recipe_revision, "expected_audio_revision": revision}
        if cuts:
            request["expected_plan_revision"] = 2
        response = client.post(render_url, json=request)
        assert response.status_code == 202, response.text
        result = finish(client, render_url)
        assert result["status"] == "ready", result
        rendered = render.Output.model_validate(result["output"])
        assert rendered.spec.audio.revision == revision and rendered.spec.audio.mode == mode
        return rendered, render.destination(pid, rendered.output_id)

    replaced, path = output("reference")
    sound = samples(path)
    assert replaced.decoded_frames == 120
    assert amplitude(sound, 0.05, 880) < 0.002  # genuine late start is retained
    assert amplitude(sound, 0.5, 880) > 0.08
    assert amplitude(sound, 1.5, 1320) > 0.08
    assert amplitude(sound, 0.5, 440) < 0.002
    assert max(abs(x) for x in sound[round(2.5 * 48000) : round(3.5 * 48000)]) < 0.002
    assert replaced.duration_seconds == pytest.approx(4, abs=1 / 30)
    plan_url = f"/api/projects/{pid}/edit-plan"
    assert client.post(plan_url + "/generate", json={"expected_revision": 0}).status_code == 200
    assert (
        client.post(
            plan_url, json={"expected_revision": 1, "source_starts_seconds": [0, 2, 3]}
        ).status_code
        == 200
    )
    cut, path = output("reference", 0.5, True)
    sound = samples(path)
    assert cut.decoded_frames == 90
    assert amplitude(sound, 0.2, 880) > 0.08
    assert amplitude(sound, 0.9, 1320) > 0.08 and amplitude(sound, 1.1, 1320) > 0.08
    assert max(abs(x) for x in sound[96000:134400]) < 0.002
    mixed, path = output("mix", 0.5, True, 70, 30)
    sound = samples(path)
    assert amplitude(sound, 0.2, 440) / amplitude(sound, 0.2, 880) == pytest.approx(7 / 3, rel=0.08)
    assert amplitude(sound, 2.2, 440) == pytest.approx(amplitude(sound, 0.2, 440), rel=0.08)
    assert mixed.decoded_audio_samples >= 144000
    muted, _ = output("mute")
    assert not muted.has_audio and muted.decoded_audio_samples == 0 and muted.decoded_frames == 120
    assert client.get(render_url).json()["output"]["spec"]["audio"]["mode"] == "mute"
    assert (
        client.post(audio_url, json={"expected_revision": revision, "mode": "original"}).status_code
        == 200
    )
    assert client.get(render_url).json()["outdated"]
    assert (
        client.post(
            render_url,
            json={"expected_revision": recipe_revision, "expected_audio_revision": revision},
        ).status_code
        == 409
    )


def test_reference_audio_revalidated_before_publication(local, monkeypatch):
    client, directory, _ = local
    saved, revision = setup(client, directory)
    pid = saved["id"]
    assert (
        client.post(
            f"/api/projects/{pid}/audio",
            json={
                "expected_revision": 0,
                "mode": "reference",
            },
        ).status_code
        == 200
    )
    real = render.pipeline

    def changed(*args):
        result = real(*args)
        reference_audio(pid, False)  # unchanged bytes/analyses; audio availability changed
        return result

    monkeypatch.setattr(render, "pipeline", changed)
    url = f"/api/projects/{pid}/render"
    assert (
        client.post(
            url, json={"expected_revision": revision, "expected_audio_revision": 1}
        ).status_code
        == 202
    )
    result = finish(client, url)
    assert result["status"] == "failed" and result["output"] is None
    assert client.get(f"/api/projects/{pid}/audio").json()["status"] == "stale"
    assert not list((projects.DATA_DIR / render.STAGING).glob("*"))
