import threading
import uuid
from types import SimpleNamespace

import assembly
import candidate_analysis as candidates
import projects
import reference_engine as engine
import sequence
from tests.test_color_analysis import finish
from tests.test_sequence import setup
from tests.test_video_render import local as local  # noqa: F401


def synthetic(frames, motion=0.1):
    return candidates.Analysis(
        source_hash="a" * 64,
        duration_frames=frames,
        width=8,
        height=8,
        samples=[
            candidates.Sample(frame=i, brightness=0.5, motion=motion, sharpness=0.02)
            for i in range(0, frames, 15)
        ],
        boundaries=[],
        ffmpeg_version="fixture",
        warnings=[],
    )


def test_selection_duration_overlap_locks_insufficiency_reuse_and_determinism():
    ids = [uuid.uuid4() for _ in range(3)]
    clip = uuid.UUID(int=1)
    slots = [
        SimpleNamespace(
            id=id,
            duration_frames=30,
            output_start_frame=i * 30,
            output_end_frame=(i + 1) * 30,
            clip_id=clip if i == 0 else None,
            source_start_frame=0,
            source_end_frame=30,
            model_dump=lambda: {},
        )
        for i, id in enumerate(ids)
    ]
    # Use actual Slot models for immutable locked assignments.
    slots = [
        sequence.Slot(
            id=s.id,
            duration_frames=30,
            output_start_frame=s.output_start_frame,
            output_end_frame=s.output_end_frame,
            clip_id=s.clip_id,
            source_start_frame=0,
            source_end_frame=30,
            source_hash="a" * 64 if s.clip_id else None,
        )
        for s in slots
    ]
    seq = SimpleNamespace(slots=slots, output_frames=90)
    settings = assembly.Settings(expected_sequence_revision=1, locked_slot_ids=[ids[0]])
    data = {"sequence": seq, "settings": settings}
    measured = {str(clip): synthetic(60)}
    reference = synthetic(90)
    chosen = assembly.select(data, measured, reference)
    assert chosen == assembly.select(data, measured, reference)
    assert chosen[0].source_start_frame == 0 and chosen[0].clip_id == clip
    assert chosen[1].source_start_frame == 30 and chosen[1].duration_frames == 30
    assert chosen[2].clip_id is None and "No full" in chosen[2].explanation
    data["settings"] = settings.model_copy(update={"allow_reused_ranges": True})
    chosen = assembly.select(data, measured, reference)
    assert all(c.clip_id == clip for c in chosen) and chosen[-1].warnings


def test_real_proposal_restore_cache_stale_and_cancellation(local, monkeypatch):
    client, directory, _ = local
    pid, _, seq_url, saved = setup(client, directory)
    url = f"/api/projects/{pid}/assembly"
    settings = {"expected_sequence_revision": 1, "allow_reused_ranges": True, "locked_slot_ids": []}
    response = client.post(url, json=settings)
    assert response.status_code == 202, response.text
    result = finish(client, url)
    assert result["status"] == "ready", result
    proposal = result["proposal"]
    assert all(c["duration_frames"] == 30 and c["clip_id"] for c in proposal["choices"])
    assert client.get(seq_url).json()["sequence"] == saved
    assert client.get(url).json()["proposal"] == proposal
    apply = client.post(url + "/apply", json=settings | {"proposal_id": proposal["id"]})
    assert apply.status_code == 200, apply.text
    assert client.get(seq_url).json()["sequence"]["revision"] == 1
    again = client.post(url, json=settings).json()
    assert again["proposal"]["id"] == proposal["id"]
    assert (
        client.post(
            url + "/apply",
            json=settings | {"allow_reused_ranges": False, "proposal_id": proposal["id"]},
        ).status_code
        == 409
    )
    # Cancellation joins the same lifecycle owner, preserving the previous completed result.
    started = threading.Event()

    def blocked(data, directory, stop, deadline):
        directory.mkdir(parents=True)
        started.set()
        while not stop.wait(0.01):
            engine.remaining(deadline, 120)
        raise engine.RetrievalFailure("interrupted", "Canceled fixture")

    monkeypatch.setattr(assembly, "pipeline", blocked)
    response = client.post(url, json=settings | {"allow_reused_ranges": False})
    assert response.status_code == 202 and started.wait(2)
    canceled = client.post(url + "/cancel").json()
    assert canceled["status"] == "failed" and canceled["proposal"] == proposal
    assert not assembly.staging(response.json()["operation_id"]).exists()
    assert client.get(seq_url).json()["sequence"] == saved
    assert client.post(seq_url, json=apply.json()).status_code == 200
    assert client.get(url).json()["proposal_stale"]
    assert (
        client.post(url + "/apply", json=settings | {"proposal_id": proposal["id"]}).status_code
        == 409
    )
    # Cached measurements survived cancellation; identical source bytes share one analysis.
    with projects.database() as db:
        assert (
            db.execute(
                "SELECT COUNT(*) FROM candidate_cache WHERE project_id=?", (pid,)
            ).fetchone()[0]
            == 1
        )
