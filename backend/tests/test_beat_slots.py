import pytest

import beats
import projects
import sequence as sequences
from tests.test_edit_plan import seed_pacing
from tests.test_video_render import local as local  # noqa: F401
from tests.test_video_render import prepared


def generated(client, directory, lengths, monkeypatch, music=("reference", 0.0), rhythm=None):
    saved, _, _ = prepared(client, directory)
    pid = saved["id"]
    seed_pacing(pid, lengths, sum(lengths) + 1, rhythm=rhythm)
    monkeypatch.setattr(sequences, "music", lambda project_id: music)
    response = client.post(f"/api/projects/{pid}/sequence/generate", json={"expected_revision": 0})
    assert response.status_code == 200, response.text
    return pid, response.json()["sequence"]


def boundaries(sequence):
    return [slot["output_start_frame"] for slot in sequence["slots"][1:]]


def clear(times):
    return beats.Rhythm(tempo_bpm=120, strength=0.9, clear=True, beats=times)


def test_slots_keep_three_frame_shots_and_merge_shorter_pieces(local, monkeypatch):
    client, directory, _ = local
    # 1 s, a 3-frame and a 5-frame montage shot, a 1.5-frame piece, then 1 s.
    _, sequence = generated(client, directory, (1, 0.1, 1 / 6, 0.05, 1), monkeypatch)
    assert [slot["duration_frames"] for slot in sequence["slots"]] == [30, 3, 7, 29]
    assert all(
        slot["reference_start_frame"] == slot["output_start_frame"] for slot in sequence["slots"]
    )


def test_slots_follow_on_beat_cuts_when_the_music_is_offset(local, monkeypatch):
    client, directory, _ = local
    rhythm = clear([0.5 * k for k in range(1, 9)])  # Cuts below fall on every 2nd beat.
    _, sequence = generated(
        client, directory, (1, 1, 1, 1), monkeypatch, ("reference", 1 / 30), rhythm
    )
    assert boundaries(sequence) == [29, 59, 89]
    assert sequence["schema_version"] == 2
    assert sequence["algorithm_version"] == "reference-slots-30fps-v2"
    assert sequence["music"] == {
        "mode": "reference",
        "offset_seconds": pytest.approx(1 / 30),
        "clear_beats": True,
        "moved_cuts": 3,
    }


def test_slots_keep_the_reference_cuts_at_offset_zero_or_without_music(local, monkeypatch):
    client, directory, _ = local
    rhythm = clear([0.5 * k for k in range(1, 9)])
    for music in (("reference", 0.0), ("original", 1 / 30), ("mute", 0.0)):
        _, sequence = generated(client, directory, (1, 1, 1, 1), monkeypatch, music, rhythm)
        assert boundaries(sequence) == [30, 60, 90]
        assert sequence["music"]["moved_cuts"] == 0


def test_slots_keep_the_reference_cuts_without_clear_beats(local, monkeypatch):
    client, directory, _ = local
    _, sequence = generated(client, directory, (1, 1, 1, 1), monkeypatch, ("reference", 1 / 30))
    assert boundaries(sequence) == [30, 60, 90]
    assert sequence["music"]["clear_beats"] is False


def test_a_sequence_from_superseded_pacing_is_stale(local, monkeypatch):
    client, directory, _ = local
    pid, _ = generated(client, directory, (1, 1, 1), monkeypatch)
    assert sequences.read(pid).status != "stale"
    with projects.database() as connection:  # A result saved by the previous pacing algorithm.
        connection.execute(
            "UPDATE pacing_operations SET algorithm_version='scdet-consecutive-v1' "
            "WHERE project_id=?",
            (pid,),
        )
    assert sequences.read(pid).status == "stale"
