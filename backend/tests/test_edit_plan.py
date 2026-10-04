import json
import statistics

import pytest

import color_analysis as color
import edit_plan as plan
import pacing_analysis as pacing
import projects
from references import ReferenceError
from tests.test_color_recipe import local as local  # noqa: F401
from tests.test_color_recipe import seed_analyses


def seed_pacing(project_id, lengths=(1, 1, 1), footage_duration=4):
    with projects.database() as connection:
        connection.execute("DELETE FROM pacing_operations WHERE project_id=?", (project_id,))
    operation, source = pacing.begin_operation(project_id)
    boundaries = [0]
    for length in lengths:
        boundaries.append(boundaries[-1] + length)
    blueprint = pacing.Blueprint(
        analyzed_at=projects.now(),
        source=source["identity"],
        tool_versions={"fixture": "synthetic timing"},
        duration_seconds=boundaries[-1],
        video_stream_index=0,
        processing_width=64,
        processing_height=64,
        successful_frames=120,
        method=(
            "Consecutive decoded frames; normalized PTS; area resize, aspect preserved "
            "within even-pixel rounding, no crop; YUV420P; scdet threshold=10 sc_pass=0"
        ),
        candidate_cut_timestamps=boundaries[1:-1],
        estimated_cut_count=len(lengths) - 1,
        shot_count=len(lengths),
        shots=[
            pacing.Shot(start_seconds=a, end_seconds=b, duration_seconds=b - a)
            for a, b in zip(boundaries, boundaries[1:])
        ],
        mean_shot_seconds=statistics.mean(lengths),
        median_shot_seconds=statistics.median(lengths),
        cuts_per_minute=(len(lengths) - 1) * 60 / boundaries[-1],
        color_metadata=color.inspect_colors({}),
        interpretation_limits=pacing.LIMITS,
    )
    with projects.database() as connection:
        connection.execute(
            "UPDATE pacing_operations SET state='ready',blueprint=?,finished_at=? "
            "WHERE project_id=?",
            (blueprint.model_dump_json(), projects.now(), project_id),
        )
        clip = connection.execute(
            "SELECT metadata FROM clips WHERE project_id=?", (project_id,)
        ).fetchone()
        metadata = json.loads(clip[0]) | {"duration_seconds": footage_duration}
        connection.execute(
            "UPDATE clips SET metadata=? WHERE project_id=?", (json.dumps(metadata), project_id)
        )


def test_generation_rounding_subframes_gaps_and_complexity():
    segments, merged, available, total = plan.ranges([1, 2, 4], 5, 7)
    assert [(s.output_start_frame, s.output_end_frame) for s in segments] == [
        (0, 30),
        (30, 90),
        (90, 150),
    ]
    assert [(s.source_start_frame, s.source_end_frame) for s in segments] == [
        (0, 30),
        (60, 120),
        (150, 210),
    ]
    assert total == 150 and available == 210 and merged == 0
    assert plan.ranges([10], 2, 4)[0][0].source_start_frame == 30
    segments, merged, _, total = plan.ranges([0.01, 0.09, 0.01, 0.89], 0.955, 2)
    assert merged >= 2 and total == 28
    assert [(s.output_start_frame, s.output_end_frame) for s in segments] == [(0, 3), (3, 28)]
    with pytest.raises(ReferenceError, match="more than 60"):
        plan.ranges([1] * 61, 61, 65)
    assert len(plan.ranges([1] * 61, 60, 65)[0]) == 60
    with pytest.raises(ReferenceError, match="one output frame"):
        plan.ranges([1], 0.01, 1)


def test_save_restore_conflicts_stale_and_independent_recipe(local):
    client, directory, _ = local
    saved, recipe_url = seed_analyses(client, directory)
    pid = saved["id"]
    seed_pacing(pid)
    url = f"/api/projects/{pid}/edit-plan"
    assert client.get(url).json()["max_duration_seconds"] == 3
    assert (
        client.post(url + "/generate", json={"expected_revision": 0}).json()["error"]["code"]
        == "recipe_not_ready"
    )
    assert client.post(recipe_url + "/generate", json={"expected_revision": 0}).status_code == 200
    initial = client.post(url + "/generate", json={"expected_revision": 0}).json()
    assert initial["status"] == "ready", initial
    assert [s["source_start_frame"] for s in initial["plan"]["segments"]] == [0, 45, 90]
    analyses = [
        client.get(f"/api/projects/{pid}/{route}").json()
        for route in ["style-blueprint", "footage-color", "pacing"]
    ]
    response = client.post(url, json={"expected_revision": 1, "source_starts_seconds": [0, 2, 3]})
    assert response.status_code == 200, response.text
    edited = response.json()
    assert edited["plan"]["revision"] == 2
    assert edited["plan"]["segments"][1]["source_start_frame"] == 60
    assert client.get(url).json() == edited
    assert (
        client.post(url, json={"expected_revision": 1, "source_starts_seconds": [0, 2, 3]}).json()[
            "error"
        ]["code"]
        == "revision_conflict"
    )
    for starts in [[0, 0.5, 3], [0, 3, 2], [0, 2, 3.1], [0, 2], [-1, 2, 3], ["NaN", 2, 3]]:
        assert (
            client.post(
                url, json={"expected_revision": 2, "source_starts_seconds": starts}
            ).status_code
            == 422
        )
    assert (
        client.post(url + "/generate", json={"expected_revision": 2}).json()["error"]["code"]
        == "plan_exists"
    )
    assert (
        client.post(
            url + "/generate",
            json={"expected_revision": 2, "replace": True, "output_duration_seconds": 4},
        ).status_code
        == 422
    )
    assert (
        client.post(
            recipe_url,
            json={"expected_revision": 1, "selected": {"brightness": 0.1}, "strength": 0.5},
        ).status_code
        == 200
    )
    assert client.get(url).json() == edited
    assert analyses == [
        client.get(f"/api/projects/{pid}/{route}").json()
        for route in ["style-blueprint", "footage-color", "pacing"]
    ]
    with projects.database() as connection:
        connection.execute("PRAGMA user_version=7")
    projects.initialize()
    assert client.get(url).json() == edited
    seed_pacing(pid, (1, 2))
    stale = client.get(url).json()
    assert stale["status"] == "stale" and stale["plan"] == edited["plan"]
    assert (
        client.post(
            url, json={"expected_revision": 2, "source_starts_seconds": [0, 2, 3]}
        ).status_code
        == 409
    )
    regenerated = client.post(
        url + "/generate", json={"expected_revision": 2, "replace": True}
    ).json()
    assert regenerated["status"] == "ready" and len(regenerated["plan"]["segments"]) == 2
    with projects.database() as connection:
        connection.execute("UPDATE clips SET sha256=? WHERE project_id=?", ("0" * 64, pid))
    assert client.get(url).json()["status"] == "stale"
    assert client.delete(f"/api/projects/{pid}").status_code == 204
    assert client.get(url).status_code == 404
    with projects.database() as connection:
        assert connection.execute("SELECT count(*) FROM edit_plans").fetchone()[0] == 0
