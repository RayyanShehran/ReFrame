import hashlib
import threading
import uuid

import pytest

import captions
import projects
import reference_engine as engine
import reference_jobs as jobs
import transcription as trans
import video_render as render
from tests.test_color_analysis import finish
from tests.test_color_recipe import local as local  # noqa: F401
from tests.test_color_recipe import seed_analyses
from tests.test_edit_plan import seed_pacing


def completed(client, directory, cuts=False):
    saved, recipe = seed_analyses(client, directory)
    pid = saved["id"]
    assert client.post(recipe + "/generate", json={"expected_revision": 0}).status_code == 200
    if cuts:
        seed_pacing(pid)
        assert (
            client.post(
                f"/api/projects/{pid}/edit-plan/generate", json={"expected_revision": 0}
            ).status_code
            == 200
        )
    output = render.Output(
        output_id=uuid.uuid4(),
        spec=render.specification(pid, 1, 1 if cuts else None),
        size_bytes=10,
        sha256=hashlib.sha256(b"mock audio").hexdigest(),
        width=64,
        height=64,
        duration_seconds=3,
        has_audio=True,
        decoded_frames=90,
        decoded_audio_samples=144000,
        ffmpeg_version="mocked boundary",
        created_at=projects.now(),
    )
    render.destination(pid, output.output_id).write_bytes(b"mock audio")
    with projects.database() as connection:
        connection.execute(
            "INSERT INTO render_outputs VALUES (?,?,?)",
            (pid, str(output.output_id), output.model_dump_json()),
        )
    return pid, output


def mock_model(monkeypatch):
    monkeypatch.setattr(
        trans.adapter,
        "run",
        lambda *a, **k: (
            [captions.Cue(start=0.1, end=1, text="Hello world.")],
            "en",
            {"faster-whisper": "mock"},
        ),
    )


@pytest.mark.parametrize("cuts", [False, True])
def test_stale_automatic_track_can_be_disabled_without_losing_text_or_timing(
    local, monkeypatch, cuts
):
    client, directory, _ = local
    pid, _ = completed(client, directory, cuts=cuts)
    mock_model(monkeypatch)
    url = f"/api/projects/{pid}/transcription"
    assert client.post(url, json={}).status_code == 202
    proposal = finish(client, url)
    cap = f"/api/projects/{pid}/captions"
    body = {
        "expected_revision": 0,
        "mode": "cuts" if cuts else "whole",
        "expected_plan_revision": 1 if cuts else None,
        "enabled": True,
        "cues": proposal["proposal"]["cues"],
        "provenance": "automatic_transcription",
        "automatic_proposal_id": proposal["operation_id"],
    }
    saved = client.post(cap, json=body)
    assert saved.status_code == 200, saved.text
    original = saved.json()["track"]
    assert (
        client.post(
            f"/api/projects/{pid}/audio", json={"expected_revision": 0, "original_volume": 90}
        ).status_code
        == 200
    )
    if cuts:
        assert (
            client.post(
                f"/api/projects/{pid}/edit-plan",
                json={"expected_revision": 1, "source_starts_seconds": [0, 1, 2]},
            ).status_code
            == 200
        )
    assert client.get(cap).json()["status"] == "stale"
    disabled = client.post(cap, json=body | {"expected_revision": 1, "enabled": False})
    assert disabled.status_code == 200, disabled.text
    track = disabled.json()["track"]
    assert not track["enabled"] and disabled.json()["status"] == "stale"
    assert (
        track["cues"] == original["cues"]
        and track["timeline"] == original["timeline"]
        and track["automatic_binding"] == original["automatic_binding"]
    )
    spec = render.specification(pid, 1, 2 if cuts else None, 1, 2)
    assert not spec.captions.enabled and spec.audio.revision == 1
    assert (
        client.post(
            cap,
            json=body
            | {
                "expected_revision": 2,
                "expected_plan_revision": 2 if cuts else None,
                "confirm_rebind": True,
            },
        ).status_code
        == 409
    )


def test_generate_review_apply_save_preserves_captions_and_style(local, monkeypatch):
    client, directory, _ = local
    pid, output = completed(client, directory)
    cap = f"/api/projects/{pid}/captions"
    assert (
        client.post(
            cap,
            json={
                "expected_revision": 0,
                "mode": "whole",
                "style": {"color": "yellow"},
                "cues": [{"start": 0, "end": 1, "text": "Existing"}],
            },
        ).status_code
        == 200
    )
    mock_model(monkeypatch)
    url = f"/api/projects/{pid}/transcription"
    assert client.post(url, json={"language": "en"}).status_code == 202
    result = finish(client, url)
    assert result["status"] == "ready" and result["proposal"]["output_sha256"] == output.sha256
    assert client.get(cap).json()["track"]["cues"][0]["text"] == "Existing"
    body = {
        "operation_id": result["operation_id"],
        "expected_caption_revision": 1,
        "cues": [{"start": 0.1, "end": 1, "text": "Edited automatic text"}],
    }
    assert (
        client.post(url + "/apply", json=body).json()["error"]["code"] == "caption_replace_required"
    )
    applied = client.post(url + "/apply", json=body | {"replace": True}).json()
    assert applied["provenance"] == "automatic_transcription"
    assert client.get(cap).json()["track"]["revision"] == 1  # apply is not save
    saved = client.post(
        cap,
        json={
            "expected_revision": 1,
            "mode": "whole",
            "enabled": True,
            "style": {"color": "yellow"},
            "cues": applied["cues"],
            "provenance": applied["provenance"],
            "automatic_proposal_id": applied["automatic_proposal_id"],
        },
    )
    assert saved.status_code == 200, saved.text
    track = saved.json()["track"]
    assert (
        track["style"]["color"] == "yellow" and track["automatic_binding"]["audio"]["revision"] == 0
    )
    assert client.post(url + "/apply", json=body | {"replace": True}).status_code == 409
    assert not list(trans.staging(result["operation_id"]).glob("*"))
    assert client.delete(url).status_code == 200
    assert client.get(cap).json()["status"] == "ready"  # saved provenance survives proposal discard
    styled = client.post(
        cap,
        json={
            "expected_revision": 2,
            "mode": "whole",
            "enabled": True,
            "style": {
                "color": "yellow",
                "size_percent": 8.0,
                "bold": True,
                "font": "amiri-regular",
                "font_origin": "assisted",
            },
            "cues": track["cues"],
            "provenance": track["provenance"],
            "automatic_proposal_id": track["automatic_proposal_id"],
        },
    )
    assert styled.status_code == 200, styled.text
    updated = styled.json()["track"]
    for key in ("cues", "timeline", "provenance", "automatic_binding", "automatic_proposal_id"):
        assert updated[key] == track[key]
    assert updated["style"]["size_percent"] == 8 and updated["style"]["bold"]
    assert updated["font_binding"]["candidate_id"] == "amiri-regular"
    assert updated["style"]["font_origin"] == "assisted"


def test_color_rerender_keeps_proposal_but_audio_and_plan_changes_stale(local, monkeypatch):
    client, directory, _ = local
    pid, output = completed(client, directory, cuts=True)
    mock_model(monkeypatch)
    url = f"/api/projects/{pid}/transcription"
    assert client.post(url, json={}).status_code == 202
    result = finish(client, url)
    assert (
        client.post(
            f"/api/projects/{pid}/color-recipe",
            json={
                "expected_revision": 1,
                "strength": 0,
                "selected": {"brightness": 0, "contrast": 1, "saturation": 1},
            },
        ).status_code
        == 200
    )
    assert client.get(url).json()["stale"] is False
    changed = output.model_copy(update={"output_id": uuid.uuid4(), "sha256": "a" * 64})
    with projects.database() as connection:
        connection.execute(
            "UPDATE render_outputs SET output_id=?,metadata=? WHERE project_id=?",
            (str(changed.output_id), changed.model_dump_json(), pid),
        )
    assert client.get(url).json()["stale"] is False  # provenance hash is not a reuse key
    assert (
        client.post(
            f"/api/projects/{pid}/audio", json={"expected_revision": 0, "original_volume": 90}
        ).status_code
        == 200
    )
    assert client.get(url).json()["stale"]

    body = {
        "operation_id": result["operation_id"],
        "expected_caption_revision": 0,
        "cues": result["proposal"]["cues"],
    }
    assert client.post(url + "/apply", json=body).status_code == 409
    assert client.post(url, json={"replace": True}).status_code == 409  # rerender required
    assert (
        client.post(
            f"/api/projects/{pid}/edit-plan",
            json={"expected_revision": 1, "source_starts_seconds": [0, 1, 2]},
        ).status_code
        == 200
    )
    assert client.get(url).json()["stale"]


def test_changed_footage_cannot_apply_proposal_or_generate_from_old_output(local, monkeypatch):
    client, directory, _ = local
    pid, _ = completed(client, directory)
    mock_model(monkeypatch)
    url = f"/api/projects/{pid}/transcription"
    assert client.post(url, json={}).status_code == 202
    result = finish(client, url)
    with projects.database() as connection:
        row = connection.execute("SELECT filename FROM clips WHERE project_id=?", (pid,)).fetchone()
    projects.media_path(pid, row[0]).write_bytes(b"changed footage")
    assert client.get(url).json()["stale"]
    assert (
        client.post(
            url + "/apply",
            json={
                "operation_id": result["operation_id"],
                "expected_caption_revision": 0,
                "cues": result["proposal"]["cues"],
            },
        ).status_code
        == 409
    )
    assert client.post(url, json={"replace": True}).status_code == 409


def test_silent_empty_failure_retry_recovery_and_new_slot(local, monkeypatch):
    client, directory, _ = local
    pid, output = completed(client, directory)
    url = f"/api/projects/{pid}/transcription"
    silent = output.model_copy(update={"has_audio": False, "decoded_audio_samples": 0})
    with projects.database() as connection:
        connection.execute(
            "UPDATE render_outputs SET metadata=? WHERE project_id=?",
            (silent.model_dump_json(), pid),
        )
    assert client.post(url, json={}).json()["error"]["code"] == "audio_unavailable"
    with projects.database() as connection:
        connection.execute(
            "UPDATE render_outputs SET metadata=? WHERE project_id=?",
            (output.model_dump_json(), pid),
        )
    monkeypatch.setattr(
        trans.adapter,
        "run",
        lambda *a, **k: (_ for _ in ()).throw(
            engine.RetrievalFailure("mock_failure", "Safe failure")
        ),
    )
    assert client.post(url, json={}).status_code == 202
    assert finish(client, url)["failure_code"] == "mock_failure"
    monkeypatch.setattr(
        trans.adapter, "run", lambda *a, **k: ([], "en", {"fixture": "mock silence"})
    )
    assert client.post(url, json={}).status_code == 202
    result = finish(client, url)
    assert result["status"] == "ready" and result["proposal"]["cues"] == []
    assert client.get(f"/api/projects/{pid}/captions").json()["track"]["revision"] == 0
    assert client.delete(url).status_code == 200
    operation, value = trans.begin_operation(pid, trans.GenerateRequest())
    trans.recover()
    assert trans.get_operation(pid).failure_code == "interrupted"
    proposal = trans.Proposal(
        versions={"mock": "1"},
        requested_language="en",
        detected_language="en",
        output_id=output.output_id,
        output_sha256=output.sha256,
        binding=value["binding"],
        cues=[],
        generated_at=projects.now(),
    )
    with pytest.raises(engine.RetrievalFailure):
        trans.commit(
            pid,
            operation.operation_id,
            value,
            proposal,
            threading.Event(),
            __import__("time").monotonic() + 10,
        )
    assert jobs.active is None
