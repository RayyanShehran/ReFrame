"""Deterministic, separately persisted automatic range proposals on the shared worker."""

import hashlib
import json
import statistics
import sys
import time
import uuid
from functools import partial
from typing import Literal

from pydantic import Field

import candidate_analysis as candidates
import clip_library
import color_analysis as color
import projects
import reference_engine as engine
import sequence
import visual_matching as visual
from references import ReferenceError

ALGORITHM = "visual-ranges-v2"
TABLE = "assembly_operations"
STAGING = "assembly-staging"
TOTAL_SECONDS = 120


class Settings(color.Schema):
    expected_sequence_revision: int = Field(ge=1, strict=True)
    matching_mode: Literal["measurements", "subject_aware"] = "measurements"
    allow_reused_ranges: bool = False
    locked_slot_ids: list[uuid.UUID] = Field(default_factory=list, max_length=60)


class Apply(Settings):
    proposal_id: uuid.UUID


class Choice(sequence.Assignment):
    explanation: str
    warnings: list[str] = Field(default_factory=list)
    visual_similarity: float | None = Field(default=None, ge=-1, le=1, allow_inf_nan=False)
    reference_image: str | None = Field(default=None, max_length=44000)
    footage_image: str | None = Field(default=None, max_length=44000)
    reference_sample_frames: list[int] = Field(default_factory=list, max_length=3)
    footage_sample_frames: list[int] = Field(default_factory=list, max_length=3)


class Proposal(color.Schema):
    schema_version: Literal[1] = 1
    algorithm_version: Literal["measured-ranges-v1", "visual-ranges-v2"] = ALGORITHM
    analysis_version: Literal["rgb-motion-2fps-64-v1"] = candidates.ALGORITHM
    id: uuid.UUID
    input_hash: str
    sequence: sequence.Sequence
    settings: Settings
    source_hashes: dict[str, str]
    choices: list[Choice] = Field(min_length=1, max_length=60)
    warnings: list[str]
    analyzed_at: str
    cached_sources: int
    model_repository: str | None = None
    model_revision: str | None = None
    visual_analysis_version: str | None = None


class Operation(color.Operation):
    blueprint: Proposal | None = None
    proposal: Proposal | None = None
    proposal_stale: bool = False
    completed_sources: int = 0
    total_sources: int = 0


component = sys.modules[__name__]
staging = partial(color.staging, component=component)
clean_stage = partial(color.clean_stage, component=component)
fail_operation = partial(color.fail_operation, component=component)
prepare_delete = partial(color.prepare_delete, component=component)
compensate = color.compensate


def inputs(project_id, settings, stop=None, deadline=None):
    deadline = deadline or time.monotonic() + 10
    saved = sequence.read(project_id)
    if not saved.sequence or saved.status == "stale":
        raise ReferenceError(
            409, "sequence_required", "Create and save valid reference slots first."
        )
    seq = saved.sequence
    sequence.check(sequence.record(project_id), settings.expected_sequence_revision)
    locked = set(settings.locked_slot_ids)
    if len(locked) != len(settings.locked_slot_ids) or not locked <= {s.id for s in seq.slots}:
        raise ReferenceError(422, "invalid_locks", "Choose unique saved slot IDs to lock.")
    if any(s.id in locked and not s.clip_id for s in seq.slots):
        raise ReferenceError(422, "invalid_locks", "Assign and save a range before locking it.")
    sources = {}
    for clip in sorted(clip_library.read(project_id).clips, key=lambda c: str(c.id)):
        if clip.available:
            sources[str(clip.id)] = clip_library.source(
                project_id, clip.id, stop=stop, deadline=deadline
            )
    if not sources:
        raise ReferenceError(
            409, "footage_required", "Upload footage before suggesting an assembly."
        )
    ref = color.source(project_id, stop, deadline)
    hashes = {id: clip.sha256 for id, (_, clip) in sources.items()}
    settings = settings.model_copy(update={"locked_slot_ids": sorted(locked, key=str)})
    raw = {
        "sequence": seq.model_dump(mode="json"),
        "settings": settings.model_dump(mode="json"),
        "sources": hashes,
        "reference": ref["identity"].model_dump(mode="json"),
        "algorithm": ALGORITHM,
        "analysis": candidates.ALGORITHM,
        "visual": [visual.REPOSITORY, visual.REVISION, visual.ALGORITHM]
        if settings.matching_mode == "subject_aware"
        else None,
    }
    signature = hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()
    return {
        "project_id": project_id,
        "sequence": seq,
        "settings": settings,
        "sources": sources,
        "reference": ref,
        "hashes": hashes,
        "signature": signature,
    }


def get_operation(project_id, require_active=False):
    projects.get_project(project_id)
    with projects.database() as db:
        row = db.execute(
            "SELECT * FROM assembly_operations WHERE project_id=?", (project_id,)
        ).fetchone()
        prior = db.execute(
            "SELECT proposal FROM assembly_proposals WHERE project_id=?", (project_id,)
        ).fetchone()
    proposal = Proposal.model_validate_json(prior[0]) if prior else None
    stale = False
    if proposal:
        try:
            stale = inputs(project_id, proposal.settings)["signature"] != proposal.input_hash
        except (ReferenceError, engine.ProbeTimeout):
            stale = True
    return Operation(
        operation_id=row["operation_id"] if row else None,
        status=row["state"] if row else "idle",
        started_at=row["started_at"] if row else None,
        finished_at=row["finished_at"] if row else None,
        failure_code=row["failure_code"] if row else None,
        message=row["message"] if row else None,
        proposal=proposal,
        proposal_stale=stale,
        blueprint=proposal if row and row["state"] == "ready" and not stale else None,
        completed_sources=row["completed"] if row else 0,
        total_sources=row["total"] if row else 0,
    )


def reusable(project_id, request):
    current = get_operation(project_id, True)
    current_input = inputs(project_id, request)
    with projects.database() as db:
        row = db.execute(
            "SELECT source_hash FROM assembly_operations WHERE project_id=?", (project_id,)
        ).fetchone()
    return (
        current
        if row
        and row[0] == current_input["signature"]
        and current.status in {"running", "ready"}
        and not current.proposal_stale
        else None
    )


def begin_operation(project_id, request):
    data = inputs(project_id, request)
    if request.matching_mode == "subject_aware" and not visual.readiness()["ready"]:
        raise ReferenceError(409, "model_unavailable", visual.SETUP)
    old = get_operation(project_id, True)
    if old.operation_id:
        prepare_delete(project_id)
    id = str(uuid.uuid4())
    hashes = [*data["hashes"].values(), data["reference"]["identity"].media_sha256]
    with projects.database() as db:
        db.execute(
            "DELETE FROM candidate_cache WHERE project_id=? AND (algorithm!=? OR "
            "source_hash NOT IN (" + ",".join("?" for _ in hashes) + "))",
            (project_id, candidates.ALGORITHM, *hashes),
        )
        db.execute(
            "DELETE FROM visual_cache WHERE project_id=? AND (algorithm!=? OR "
            "source_hash NOT IN (" + ",".join("?" for _ in hashes) + "))",
            (project_id, visual.ALGORITHM, *hashes),
        )
        db.execute("DELETE FROM assembly_operations WHERE project_id=?", (project_id,))
        db.execute(
            "INSERT INTO assembly_operations(project_id,operation_id,state,started_at,"
            "source_hash,algorithm_version,total) VALUES(?,?,'running',?,?,?,?)",
            (project_id, id, projects.now(), data["signature"], ALGORITHM, len(hashes)),
        )
    data["operation_id"] = id
    return get_operation(project_id), data


def stats(analysis, start, end):
    values = [s for s in analysis.samples if start <= s.frame < end]
    if not values:
        values = [min(analysis.samples, key=lambda s: abs(s.frame - start))]
    return tuple(
        statistics.fmean(getattr(s, name) for s in values)
        for name in ("motion", "brightness", "sharpness")
    )


def overlaps(start, end, ranges):
    return any(start < b and a < end for a, b in ranges)


def select(data, measured, reference, stop=None, deadline=None, embeddings=None):
    # ponytail: greedy slot-order packing; revisit only if manual corrections prove insufficient.
    seq, settings = data["sequence"], data["settings"]
    occupied = {id: [] for id in measured}
    locked = set(settings.locked_slot_ids)
    uses = {id: 0 for id in measured}
    for slot in seq.slots:
        if deadline is not None:
            color.guard(stop, deadline)
        if slot.id in locked:
            occupied[str(slot.clip_id)].append((slot.source_start_frame, slot.source_end_frame))
            uses[str(slot.clip_id)] += 1
    choices = []
    for slot in seq.slots:
        if deadline is not None:
            color.guard(stop, deadline)
        if slot.id in locked:
            choices.append(
                Choice(
                    **sequence.Assignment.model_validate(
                        slot.model_dump(), extra="ignore"
                    ).model_dump(),
                    explanation="Locked saved assignment; unchanged.",
                )
            )
            continue
        associated = slot.reference_start_frame is not None
        start_ref = slot.reference_start_frame if associated else 0
        end_ref = slot.reference_end_frame if associated else reference.duration_frames
        target = stats(reference, start_ref, end_ref)[0]
        reference_samples = (
            visual.range_samples(embeddings["reference"], start_ref, end_ref)
            if embeddings and associated
            else []
        )
        ranked = []
        for id, analysis in measured.items():
            if deadline is not None:
                color.guard(stop, deadline)
            length = slot.duration_frames
            if analysis.duration_frames < length:
                continue
            starts = {
                0,
                analysis.duration_frames - length,
                *range(0, analysis.duration_frames - length + 1, 15),
                *analysis.boundaries,
                *(b for _, b in occupied[id]),
            }
            for start in sorted(starts):
                if deadline is not None:
                    color.guard(stop, deadline)
                end = start + length
                if end > analysis.duration_frames:
                    continue
                reuse = overlaps(start, end, occupied[id])
                if reuse and not settings.allow_reused_ranges:
                    continue
                motion, brightness, sharpness = stats(analysis, start, end)
                cuts = sum(start < t < end for t in analysis.boundaries)
                score = (
                    -5 * cuts
                    - 2 * abs(target - motion)
                    - 0.3 * max(0, 0.08 - brightness) / 0.08
                    + 0.2 * min(1, sharpness / 0.03)
                    - 0.08 * uses[id]
                    - (1 if reuse else 0)
                )
                footage_samples = (
                    visual.range_samples(embeddings[id], start, end) if embeddings else []
                )
                similarity = (
                    visual.similarity(reference_samples, footage_samples)
                    if reference_samples and footage_samples
                    else None
                )
                if similarity is not None:
                    score += 4 * similarity
                ranked.append(
                    (
                        -round(score, 9),
                        id,
                        start,
                        motion,
                        brightness,
                        sharpness,
                        cuts,
                        reuse,
                        similarity,
                    )
                )
        if not ranked:
            choices.append(
                Choice(
                    id=slot.id,
                    duration_frames=slot.duration_frames,
                    reference_start_frame=slot.reference_start_frame,
                    reference_end_frame=slot.reference_end_frame,
                    explanation="No full permitted range remains. Shorten or upload; "
                    "unlock assignments, or explicitly allow reused ranges.",
                    warnings=["Unfilled; no footage will be shortened or stretched."],
                )
            )
            continue
        _, id, start, motion, brightness, sharpness, cuts, reuse, similarity = min(ranked)
        occupied[id].append((start, start + slot.duration_frames))
        uses[id] += 1
        warnings = []
        samples = (
            visual.range_samples(embeddings[id], start, start + slot.duration_frames)
            if embeddings
            else []
        )
        if settings.matching_mode == "subject_aware":
            if not associated:
                warnings.append(
                    "No reference interval associated: measurement-based selection using "
                    "whole-reference motion. Regenerate slots to associate reference shots."
                )
            elif similarity is None:
                warnings.append(
                    "Fewer than two 2 fps samples inside the reference or selected range: "
                    "measurement-based selection for this slot."
                )
            else:
                others = [r[-1] for r in ranked if r[1] != id and r[-1] is not None]
                if similarity < 0.65:
                    warnings.append(
                        "Weak visual similarity; the subject may be absent. Review the images."
                    )
                if others and similarity - max(others) < 0.05:
                    warnings.append(
                        "Ambiguous visual similarity across clips; review alternatives manually."
                    )
        if reuse:
            warnings.append("Overlapping source range reused by explicit permission.")
        if cuts:
            warnings.append("Sampled scene change inside range; review the cut.")
        if brightness < 0.08:
            warnings.append("Dark sampled pixels; this may be intentional.")
        choices.append(
            Choice(
                id=slot.id,
                clip_id=id,
                duration_frames=slot.duration_frames,
                source_start_frame=start,
                reference_start_frame=slot.reference_start_frame,
                reference_end_frame=slot.reference_end_frame,
                visual_similarity=similarity,
                reference_image=reference_samples[len(reference_samples) // 2].image
                if reference_samples
                else None,
                footage_image=samples[len(samples) // 2].image if samples else None,
                reference_sample_frames=[s.frame for s in reference_samples],
                footage_sample_frames=[s.frame for s in samples],
                explanation=(
                    f"Visual cosine similarity {similarity:.3f} (not a probability). "
                    if similarity is not None
                    else ""
                )
                + f"Sample motion {motion:.3f} vs reference {target:.3f}; "
                f"brightness {brightness:.3f}, sharpness {sharpness:.3f}; "
                f"{cuts} sampled scene changes.",
                warnings=warnings,
            )
        )
    return choices


def pipeline(data, directory, stop, deadline):
    directory.mkdir(parents=True, exist_ok=False)
    engine._control.stop = stop
    try:
        measured = {}
        cached = 0
        count = 0
        items = [(id, path, clip.sha256) for id, (path, clip) in data["sources"].items()]
        items.append(
            ("reference", data["reference"]["path"], data["reference"]["identity"].media_sha256)
        )
        reference = None
        for index, (id, path, digest) in enumerate(items):
            color.guard(stop, deadline)
            analysis, hit = candidates.analyze(
                data["project_id"], path, digest, directory, stop, deadline
            )
            count += len(analysis.samples)
            cached += int(hit)
            if count > candidates.MAX_SAMPLES:
                raise engine.RetrievalFailure(
                    "sample_limit", "Project exceeded 2,640 sampled frames."
                )
            if id == "reference":
                reference = analysis
            else:
                measured[id] = analysis
            with projects.database() as db:
                db.execute(
                    "UPDATE assembly_operations SET completed=? WHERE project_id=? AND "
                    "operation_id=? AND state='running'",
                    (index + 1, data["project_id"], data["operation_id"]),
                )
        embeddings = None
        if data["settings"].matching_mode == "subject_aware":

            def progress(completed):
                color.guard(stop, deadline)
                with projects.database() as db:
                    db.execute(
                        "UPDATE assembly_operations SET completed=? WHERE project_id=? "
                        "AND operation_id=? AND state='running'",
                        (completed, data["project_id"], data["operation_id"]),
                    )

            with projects.database() as db:
                db.execute(
                    "UPDATE assembly_operations SET completed=0,message=? "
                    "WHERE project_id=? AND operation_id=?",
                    (
                        "Loading local model and comparing visual samples; "
                        "120-second overall deadline.",
                        data["project_id"],
                        data["operation_id"],
                    ),
                )
            unique = {
                digest: (path, reference if id == "reference" else measured[id])
                for id, path, digest in items
            }
            analyzed = visual.analyze(
                data["project_id"],
                [(digest, path, measurement) for digest, (path, measurement) in unique.items()],
                directory,
                stop,
                deadline,
                progress,
            )
            embeddings = {id: analyzed[digest] for id, _, digest in items}
        choices = select(data, measured, reference, stop, deadline, embeddings)
        color.guard(stop, deadline)
        proposal = Proposal(
            id=uuid.UUID(data["operation_id"]),
            input_hash=data["signature"],
            sequence=data["sequence"],
            settings=data["settings"],
            source_hashes=data["hashes"],
            choices=choices,
            cached_sources=cached,
            analyzed_at=projects.now(),
            model_repository=visual.REPOSITORY if embeddings else None,
            model_revision=visual.REVISION if embeddings else None,
            visual_analysis_version=visual.ALGORITHM if embeddings else None,
            warnings=[
                "Experimental frame-based visual similarity plus motion/scene measurements."
                if embeddings
                else "Automatic selection based on motion, scene boundaries "
                "and image measurements.",
                "2 fps sampling can miss brief cuts. Dark/quiet/soft images can be intentional.",
                "Similarity is not a probability, action/story/identity recognition "
                "or artistic intent. Review images."
                if embeddings
                else "Unchanged inputs produce the same ranges. No subject recognition.",
            ],
        )
        return data, proposal
    except engine.ProcessCleanupError:
        raise engine.RetrievalFailure(
            "cleanup_failure", "Owned analysis could not be confirmed stopped.", False
        ) from None
    except engine.ProbeInterrupted:
        raise engine.RetrievalFailure("interrupted", "Assembly suggestion canceled.") from None
    except (engine.SizeLimit, engine.ToolOutputError):
        raise engine.RetrievalFailure(
            "analysis_limit", "Candidate processing exceeded its bounded output or staging."
        ) from None
    finally:
        engine._control.stop = None


def commit(project_id, operation_id, data, proposal, stop, deadline):
    color.guard(stop, deadline)
    if inputs(project_id, proposal.settings, stop, deadline)["signature"] != proposal.input_hash:
        raise engine.RetrievalFailure(
            "source_changed", "Sequence or sources changed during selection."
        )
    clean_stage(operation_id)
    with projects.database() as db:
        project = projects.row_project(db, project_id)
        row = db.execute(
            "SELECT * FROM assembly_operations WHERE project_id=?", (project_id,)
        ).fetchone()
        saved = db.execute(
            "SELECT sequence FROM sequences WHERE project_id=?", (project_id,)
        ).fetchone()
        if (
            project["status"] != "active"
            or not row
            or row["state"] != "running"
            or row["operation_id"] != operation_id
            or not saved
            or sequence.Sequence.model_validate_json(saved[0]) != proposal.sequence
        ):
            raise engine.RetrievalFailure("interrupted", "Assembly operation is no longer current.")
        color.guard(stop, deadline)
        db.execute(
            "INSERT OR REPLACE INTO assembly_proposals VALUES(?,?)",
            (project_id, proposal.model_dump_json()),
        )
        db.execute(
            "UPDATE assembly_operations SET state='ready',message=NULL,finished_at=? "
            "WHERE project_id=?",
            (projects.now(), project_id),
        )


def apply(project_id, request):
    current = get_operation(project_id)
    proposal = current.proposal
    settings = Settings.model_validate(request.model_dump(), extra="ignore")
    settings = settings.model_copy(
        update={"locked_slot_ids": sorted(settings.locked_slot_ids, key=str)}
    )
    if (
        not proposal
        or proposal.id != request.proposal_id
        or current.proposal_stale
        or settings != proposal.settings
        or inputs(project_id, settings)["signature"] != proposal.input_hash
    ):
        raise ReferenceError(
            409, "proposal_stale", "Proposal inputs/settings changed. Suggest explicitly again."
        )
    return {
        "expected_revision": proposal.sequence.revision,
        "slots": [
            sequence.Assignment.model_validate(c.model_dump(), extra="ignore").model_dump(
                mode="json"
            )
            for c in proposal.choices
        ],
    }


def recover():
    with projects.database() as db:
        rows = db.execute("SELECT * FROM assembly_operations").fetchall()
    for row in rows:
        if row["state"] == "running":
            fail_operation(
                row["project_id"],
                row["operation_id"],
                engine.RetrievalFailure(
                    "interrupted", "Backend restarted during selection. Retry explicitly."
                ),
            )
        if row["cleanup_safe"]:
            clean_stage(row["operation_id"])
