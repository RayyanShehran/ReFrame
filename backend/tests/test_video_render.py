"""Real encodes/decodes, with typed stored analyses seeded without network access."""

import json
import shutil
import subprocess
import threading
import time

import pytest

import projects
import reference_engine as engine
import reference_jobs as jobs
import video_render as render
from tests.test_color_analysis import finish, generated
from tests.test_color_analysis import local as color_fixture
from tests.test_color_recipe import seed_analyses


@pytest.fixture
def local(monkeypatch, tmp_path):
    yield from color_fixture.__wrapped__(monkeypatch, tmp_path)


def prepared(client, directory, source=None):
    if source is None:
        source = directory / "real.mp4"
        generated(source, ((64, 64, 64), (128, 128, 128), (192, 192, 192)))
    saved, url = seed_analyses(client, directory)
    project_id = saved["id"]
    with projects.database() as connection:
        clip = connection.execute(
            "SELECT * FROM clips WHERE project_id=?", (project_id,)
        ).fetchone()
        reference = connection.execute(
            "SELECT * FROM reference_operations WHERE project_id=?", (project_id,)
        ).fetchone()
        clip_path = projects.media_path(project_id, clip["filename"])
        ref_path = jobs.destination(project_id, reference["operation_id"])
        for path in (clip_path, ref_path):
            shutil.copyfile(source, path)
        digest = render.footage.digest(clip_path)
        metadata = json.loads(clip["metadata"])
        metadata["size_bytes"] = source.stat().st_size
        connection.execute(
            "UPDATE clips SET sha256=?,metadata=? WHERE project_id=?",
            (digest, json.dumps(metadata), project_id),
        )
        metadata = json.loads(reference["metadata"])
        metadata.update(size_bytes=source.stat().st_size, sha256=digest)
        connection.execute(
            "UPDATE reference_operations SET metadata=? WHERE project_id=?",
            (json.dumps(metadata), project_id),
        )
        for table in ("color_operations", "footage_color_operations"):
            row = connection.execute(
                f"SELECT blueprint FROM {table} WHERE project_id=?", (project_id,)
            ).fetchone()
            blueprint = json.loads(row[0])
            blueprint["source"]["media_sha256"] = digest
            connection.execute(
                f"UPDATE {table} SET blueprint=?,source_hash=? WHERE project_id=?",
                (json.dumps(blueprint), digest, project_id),
            )
    assert client.post(url + "/generate", json={"expected_revision": 0}).status_code == 200
    return saved, url, clip_path


def selected(client, url, values, strength=1):
    revision = client.get(url).json()["recipe"]["revision"]
    response = client.post(
        url, json={"expected_revision": revision, "selected": values, "strength": strength}
    )
    assert response.status_code == 200
    return response.json()["recipe"]["revision"]


def run(client, saved, revision):
    url = f"/api/projects/{saved['id']}/render"
    response = client.post(url, json={"expected_revision": revision})
    assert response.status_code == 202, response.text
    operation = finish(client, url)
    assert operation["status"] == "ready", operation
    return render.Output.model_validate(operation["output"])


def pixels(path):
    return subprocess.run(
        [
            shutil.which("ffmpeg"),
            "-v",
            "error",
            "-i",
            str(path),
            "-vf",
            "fps=1,scale=16:16",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "-",
        ],
        check=True,
        capture_output=True,
        timeout=15,
    ).stdout


def mean(raw):
    return sum(raw) / len(raw)


def test_real_neutral_strength_effects_reuse_and_failed_replacement(local, monkeypatch):
    client, directory, _ = local
    saved, url, source = prepared(client, directory)
    before = source.read_bytes()
    neutral = {"brightness": 0, "contrast": 1, "saturation": 1}
    revision = selected(client, url, neutral)
    output = run(client, saved, revision)
    decoded = pixels(render.destination(saved["id"], output.output_id))
    original = pixels(source)
    assert len(decoded) == len(original)
    assert max(abs(a - b) for a, b in zip(decoded, original)) <= 6
    assert output.decoded_frames == 90 and output.decoded_audio_samples == 0
    assert run(client, saved, revision).output_id == output.output_id
    brighter_revision = selected(client, url, {**neutral, "brightness": 0.2}, 0.5)
    assert client.get(f"/api/projects/{saved['id']}/render").json()["outdated"]
    brighter = run(client, saved, brighter_revision)
    bright = pixels(render.destination(saved["id"], brighter.output_id))
    # eq adds .1 to limited Y: approximately 29 encoded RGB levels, not 14 (double strength).
    assert 25 < mean(bright) - mean(decoded) < 33
    assert brighter.spec.effective.brightness == pytest.approx(0.1)
    assert not render.destination(saved["id"], output.output_id).exists()
    contrast_revision = selected(client, url, {**neutral, "contrast": 1.4})
    contrasted = pixels(
        render.destination(saved["id"], run(client, saved, contrast_revision).output_id)
    )
    assert max(contrasted) - min(contrasted) > max(decoded) - min(decoded) + 25
    previous = client.get(f"/api/projects/{saved['id']}/render").json()["output"]
    latest = selected(client, url, neutral)

    def fail(*args):
        raise engine.RetrievalFailure("render_failed", "Injected encoder failure")

    monkeypatch.setattr(render, "pipeline", fail)
    client.post(f"/api/projects/{saved['id']}/render", json={"expected_revision": latest})
    failed = finish(client, f"/api/projects/{saved['id']}/render")
    assert failed["status"] == "failed" and failed["output"] == previous and failed["outdated"]
    assert source.read_bytes() == before
    assert not list((projects.DATA_DIR / render.STAGING).glob("*"))


def test_real_saturation(local):
    client, directory, _ = local
    source = directory / "colored.mp4"
    generated(source, ((150, 100, 80),))
    saved, url, _ = prepared(client, directory, source)
    revision = selected(client, url, {"brightness": 0, "contrast": 1, "saturation": 0.5})
    result = run(client, saved, revision)
    raw = pixels(render.destination(saved["id"], result.output_id))
    assert max(raw) - min(raw) < 45  # Source channel spread is 70.


def test_real_serving_ranges_download_hash_deletion_and_replacement(local):
    client, directory, _ = local
    saved, url, _ = prepared(client, directory)
    neutral = {"brightness": 0, "contrast": 1, "saturation": 1}
    output = run(client, saved, selected(client, url, neutral))
    endpoint = f"/api/projects/{saved['id']}/outputs/{output.output_id}"
    video = client.get(endpoint + "/video")
    assert video.status_code == 200 and video.headers["content-type"] == "video/mp4"
    assert video.headers["accept-ranges"] == "bytes"
    assert video.headers["content-disposition"].startswith("inline")
    assert video.headers["x-render-outdated"] == "false"
    seek = client.get(endpoint + "/video", headers={"Range": "bytes=100-199"})
    assert seek.status_code == 206 and seek.content == video.content[100:200]
    assert seek.headers["content-range"] == f"bytes 100-199/{len(video.content)}"
    download = client.get(endpoint + "/download")
    assert download.content == video.content and download.headers["content-disposition"].startswith(
        "attachment"
    )
    assert client.get(endpoint + "/video", headers={"Range": "bytes=9999999-"}).status_code == 416
    revision = selected(client, url, {**neutral, "brightness": 0.1})
    assert client.get(endpoint + "/video").headers["x-render-outdated"] == "true"
    replacement = run(client, saved, revision)
    assert client.get(endpoint + "/video").status_code == 404
    new_url = f"/api/projects/{saved['id']}/outputs/{replacement.output_id}/video"
    path = render.destination(saved["id"], replacement.output_id)
    changed = bytearray(path.read_bytes())
    changed[-1] ^= 1
    path.write_bytes(changed)
    assert client.get(new_url).json()["error"]["code"] == "output_changed"
    assert client.delete(f"/api/projects/{saved['id']}").status_code == 204
    assert client.get(new_url).status_code == 404


@pytest.mark.parametrize("rotate,audio", [(False, False), (True, False), (False, True)])
def test_real_portrait_rotation_and_short_audio(local, rotate, audio):
    client, directory, _ = local
    source = directory / "portrait.mp4"
    args = [
        shutil.which("ffmpeg"),
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=48x80:rate=30:duration=2",
    ]
    if audio:
        args += [
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=0.5",
            "-c:a",
            "aac",
        ]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)]
    subprocess.run(args, check=True, capture_output=True, timeout=15)
    if rotate:
        rotated = directory / "rotated.mp4"
        subprocess.run(
            [
                shutil.which("ffmpeg"),
                "-v",
                "error",
                "-display_rotation",
                "90",
                "-i",
                str(source),
                "-c",
                "copy",
                str(rotated),
            ],
            check=True,
            capture_output=True,
            timeout=15,
        )
        source = rotated
    saved, url, _ = prepared(client, directory, source)
    revision = selected(client, url, {"brightness": 0, "contrast": 1, "saturation": 1})
    output = run(client, saved, revision)
    assert (output.width, output.height) == ((80, 48) if rotate else (48, 80))
    assert output.has_audio == audio
    assert output.duration_seconds == pytest.approx(2, abs=2 / 30)
    assert output.decoded_frames == 60
    assert (
        (20000 < output.decoded_audio_samples < 28000)
        if audio
        else output.decoded_audio_samples == 0
    )


def test_revision_changes_during_render_sources_and_deleted_operations(local, monkeypatch):
    client, directory, _ = local
    saved, url, source = prepared(client, directory)
    revision = selected(client, url, {"brightness": 0, "contrast": 1, "saturation": 1})
    started, release = threading.Event(), threading.Event()
    real = render.pipeline

    def blocked(*args):
        result = real(*args)
        started.set()
        assert release.wait(5)
        return result

    monkeypatch.setattr(render, "pipeline", blocked)
    endpoint = f"/api/projects/{saved['id']}/render"
    first = client.post(endpoint, json={"expected_revision": revision}).json()
    assert started.wait(5)
    assert (
        client.post(endpoint, json={"expected_revision": revision}).json()["operation_id"]
        == first["operation_id"]
    )
    selected(client, url, {"brightness": 0.1, "contrast": 1, "saturation": 1})
    release.set()
    result = finish(client, endpoint)
    assert result["status"] == "ready" and result["outdated"]
    assert result["output"]["spec"]["recipe_revision"] == revision
    assert (
        client.post(endpoint, json={"expected_revision": revision}).json()["error"]["code"]
        == "revision_conflict"
    )
    source.write_bytes(b"changed")
    assert (
        client.post(endpoint, json={"expected_revision": revision + 1}).json()["error"]["code"]
        == "recipe_stale"
    )
    assert client.get(endpoint).json()["outdated"]
    assert client.delete(f"/api/projects/{saved['id']}").status_code == 204
    assert client.get(endpoint).status_code == 404


def test_unpublished_stale_operation_cleanup_and_recovery(local):
    client, directory, _ = local
    saved, url, _ = prepared(client, directory)
    spec_revision = selected(client, url, {"brightness": 0, "contrast": 1, "saturation": 1})
    operation, source = render.begin_operation(saved["id"], spec_revision)
    stop = threading.Event()
    deadline = time.monotonic() + 30
    path, output = render.pipeline(source, render.staging(operation.operation_id), stop, deadline)
    with projects.database() as connection:
        connection.execute(
            "UPDATE render_operations SET state='failed' WHERE project_id=?", (saved["id"],)
        )
    with pytest.raises(engine.RetrievalFailure, match="no longer current"):
        render.commit(saved["id"], operation.operation_id, path, output, stop, deadline)
    render.recover()
    assert not path.exists()
    operation, _ = render.begin_operation(saved["id"], spec_revision)
    render.staging(operation.operation_id).mkdir()
    render.recover()
    assert render.get_operation(saved["id"]).failure_code == "interrupted"
    assert not render.staging(operation.operation_id).exists()


def test_empty_decode_invalid_requests_and_bounds(local):
    client, directory, _ = local
    saved, _, _ = prepared(client, directory)
    endpoint = f"/api/projects/{saved['id']}/render"
    for body in ({}, {"expected_revision": 0}, {"expected_revision": 1, "path": "other.mp4"}):
        assert client.post(endpoint, json=body).status_code == 422
    with pytest.raises(ValueError, match="Missing decoded"):
        render.decode_counts(b"# no packets\n", False)
    with pytest.raises(ValueError):
        render.decode_counts(b"0,0,0,1,10," + b"a" * 64 + b"\n", True)
    assert render.dimensions({"width": 1920, "height": 1080}) == (1280, 720)
    assert render.dimensions({"width": 1080, "height": 1920}) == (720, 1280)
    assert render.dimensions({"width": 321, "height": 241}) == (320, 240)


@pytest.mark.parametrize(
    "failure,code",
    [
        (engine.SizeLimit, "staging_limit"),
        (engine.ToolOutputError, "tool_output_limit"),
        (engine.ProcessCleanupError, "cleanup_failure"),
    ],
)
def test_inherited_runner_failures_are_explicit(local, monkeypatch, failure, code):
    client, directory, _ = local
    saved, _, _ = prepared(client, directory)
    operation, source = render.begin_operation(saved["id"], 1)

    def failed(*a, **k):
        raise failure

    monkeypatch.setattr(engine, "run_command", failed)
    with pytest.raises(engine.RetrievalFailure) as caught:
        render.pipeline(
            source, render.staging(operation.operation_id), threading.Event(), time.monotonic() + 20
        )
    assert caught.value.code == code
    assert caught.value.cleanup_safe == (failure is not engine.ProcessCleanupError)
