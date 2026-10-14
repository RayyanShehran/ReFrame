"""Persisted, sequential orchestration on the existing single owned media worker."""

import asyncio
import json
import logging
import shutil
import time
import uuid
from difflib import SequenceMatcher
from typing import Literal

from pydantic import Field, ValidationError, field_validator, model_validator

import assembly
import audio_settings
import automatic_captions
import captions
import clip_library
import color_analysis as color
import framing
import grading
import pacing_analysis
import projects
import reference_engine as engine
import reference_jobs as jobs
import sequence
import transcribe_local
import video_render as render
import visual_matching
from clips import finish_thread
from references import ReferenceError

logger = logging.getLogger(__name__)
TOTAL_SECONDS = 1200
STAGES = (
    "Reading reference",
    "Choosing your clips",
    "Matching the look",
    "Recreating captions",
    "Rendering",
)
REVISION_TABLES = (
    "sequences",
    "grading_settings",
    "audio_settings",
    "caption_tracks",
    "framing_settings",
    "color_recipes",
    "project_fonts",
    "edit_plans",
)


class Request(color.Schema):
    expected_revision: int = Field(default=0, ge=0, strict=True)
    audio: Literal["reference", "original", "mix", "mute"] = "reference"
    captions: Literal["reference", "output_audio", "off"] = "reference"
    allow_reused_ranges: bool = Field(default=False, strict=True)
    replace_customized: bool = Field(default=False, strict=True)
    matching_mode: Literal["subject_aware", "measurements"] = "subject_aware"
    accept_partial_captions: bool = Field(default=False, strict=True)
    replace_verified_captions: bool = Field(default=False, strict=True)


class CaptionObservation(color.Schema):
    start: float = Field(ge=0, lt=120, allow_inf_nan=False)
    end: float = Field(gt=0, le=120, allow_inf_nan=False)
    text: str = Field(min_length=1, max_length=200)
    start_window: tuple[float, float]
    end_window: tuple[float, float]
    start_supported: bool | None = None
    end_supported: bool | None = None
    wording: Literal["unresolved", "ocr", "paired_model"]

    @field_validator("start_window", "end_window")
    @classmethod
    def window(cls, values):
        import math

        if not all(math.isfinite(v) for v in values) or not 0 <= values[0] <= values[1] <= 120:
            raise ValueError("Boundary observations must be finite and ordered")
        return values

    @model_validator(mode="after")
    def supported(self):
        if (
            self.start >= self.end
            or not self.start_window[0] <= self.start <= self.start_window[1]
            or not self.end_window[0] <= self.end <= self.end_window[1]
        ):
            raise ValueError("Cue boundaries must lie inside their observed brackets")
        return self


class CaptionReview(color.Schema):
    cues: list[captions.Cue] = Field(max_length=200)
    notices: list[str] = Field(max_length=30)
    evidence: captions.VisibleBinding
    unreadable_samples: int = Field(ge=0, le=3600)
    verified_cues: list[captions.Cue] = Field(default_factory=list, max_length=200)
    corrected_cues: list[captions.Cue] = Field(default_factory=list, max_length=200)
    observations: list[CaptionObservation] = Field(default_factory=list, max_length=200)


class ReviewRequest(color.Schema):
    expected_revision: int = Field(ge=1, strict=True)
    cues: list[captions.Cue] = Field(max_length=200)
    verified_indices: list[int] = Field(max_length=200)
    corrected_indices: list[int] = Field(default_factory=list, max_length=200)

    @field_validator("verified_indices", "corrected_indices")
    @classmethod
    def indices(cls, values):
        if any(type(i) is not int or not 0 <= i < 200 for i in values) or len(set(values)) != len(
            values
        ):
            raise ValueError("Choose valid, distinct cue indices")
        return values


class Operation(color.Schema):
    project_id: str | None = None
    schema_version: Literal[1] = 1
    revision: int = 0
    operation_id: str | None = None
    status: Literal["idle", "running", "ready", "failed"] = "idle"
    stage: str | None = None
    options: Request | None = None
    completed: list[str] = Field(default_factory=list, max_length=8)
    notices: list[str] = Field(default_factory=list, max_length=30)
    failure_code: str | None = None
    message: str | None = None
    output_id: str | None = None
    caption_review: CaptionReview | None = None

    @field_validator("notices", mode="before")
    @classmethod
    def bounded_notices(cls, value):
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            return list(dict.fromkeys(value))[-30:]
        return value


def record(project_id):
    projects.get_project(project_id)
    with projects.database() as db:
        return db.execute(
            "SELECT * FROM generation_operations WHERE project_id=?", (project_id,)
        ).fetchone()


def get_operation(project_id, require_active=False):
    row = record(project_id)
    if not row:
        return Operation(project_id=project_id)
    operation = Operation.model_validate_json(row["operation"])
    if operation.failure_code == "caption_partial" and operation.caption_review:
        operation.message = (
            f"Caption reading is incomplete. {len(operation.caption_review.cues)} detected cues "
            "are saved; retry reading or continue without captions."
        )
    return operation


def review_captions(project_id, request):
    row = record(project_id)
    operation = get_operation(project_id)
    if operation.revision != request.expected_revision:
        raise ReferenceError(
            409, "revision_conflict", "Caption reading changed. Reload before correcting."
        )
    if operation.status == "running" or not row or not row["cleanup_safe"]:
        raise ReferenceError(409, "reference_busy", "Wait for caption reading to stop safely.")
    review = operation.caption_review
    source = color.source(project_id)
    if (
        not review
        or review.evidence.source != source["identity"]
        or json.loads(row["snapshot"]) != snapshot(project_id)
    ):
        raise ReferenceError(
            409, "caption_review_stale", "Reference or output timeline changed. Retry reading."
        )
    try:
        captions.Choices(cues=request.cues)
    except ValidationError:
        raise ReferenceError(
            422, "invalid_caption", "Keep cues ordered, without overlaps."
        ) from None
    duration = sequence.read(project_id).sequence.output_frames / 30
    if any(c.end > duration + 0.000001 for c in request.cues) or any(
        i >= len(request.cues) for i in [*request.verified_indices, *request.corrected_indices]
    ):
        raise ReferenceError(
            422, "invalid_caption", "Keep every cue inside the output and verify an existing cue."
        )
    review.cues = request.cues
    review.verified_cues = [request.cues[i] for i in request.verified_indices]
    review.corrected_cues = [request.cues[i] for i in request.corrected_indices]
    operation.revision += 1
    with projects.database() as db:
        changed = db.execute(
            "UPDATE generation_operations SET revision=?,operation=? "
            "WHERE project_id=? AND revision=?",
            (
                operation.revision,
                operation.model_dump_json(),
                project_id,
                request.expected_revision,
            ),
        ).rowcount
        if changed != 1:
            raise ReferenceError(
                409, "revision_conflict", "Caption reading changed. Reload before correcting."
            )
    return operation


def retain_reviewed(
    cues,
    review,
    source,
    replace_verified=False,
    incomplete=False,
    *,
    observed_window=None,
    excluded_intervals=(),
    observations=None,
    observed_absence=(),
):
    if (
        not review
        or review.evidence.source != source["identity"]
        or replace_verified
        or (
            source.get("reference_operation_id") is not None
            and str(review.evidence.reference_operation_id) != str(source["reference_operation_id"])
        )
    ):
        return cues, []
    observations = observations if observations is not None else []

    def key(c):
        return c.start, c.end, c.text

    old_facts = {key(c): c for c in review.observations}
    new_facts = {key(c): c for c in observations}
    selected_facts = dict(old_facts)
    ranks = {"unresolved": 0, "ocr": 1, "paired_model": 2}
    reconciled = []
    for fresh in cues:
        candidates = [
            old
            for old in review.cues
            if old.start < fresh.end
            and fresh.start < old.end
            and key(old) in old_facts
            and key(fresh) in new_facts
            and SequenceMatcher(
                None,
                automatic_captions.word_key(old.text),
                automatic_captions.word_key(fresh.text),
            ).ratio()
            >= 0.8
        ]
        fact = new_facts.get(key(fresh))
        if len(candidates) == 1:
            old = candidates[0]
            # A split/added neighboring cue needs its own boundaries, not the old whole interval.
            if sum(c.start < old.end and old.start < c.end for c in cues) == 1:
                before = old_facts[key(old)]
                text = old.text if ranks[before.wording] > ranks[fact.wording] else fresh.text
                values = {"text": text}
                windows = {}
                for edge in ("start", "end"):
                    previous, current = (
                        getattr(before, edge + "_window"),
                        getattr(fact, edge + "_window"),
                    )
                    old_supported = getattr(before, edge + "_supported")
                    new_supported = getattr(fact, edge + "_supported")
                    stronger = (old_supported is True and new_supported is False) or (
                        previous[1] - previous[0] < current[1] - current[0] - 0.000001
                    )
                    values[edge] = getattr(old if stronger else fresh, edge)
                    windows[edge + "_window"] = previous if stronger else current
                    windows[edge + "_supported"] = old_supported if stronger else new_supported
                if values["start"] < values["end"] and not any(
                    other is not fresh
                    and values["start"] < other.end
                    and other.start < values["end"]
                    for other in cues
                ):
                    fresh = fresh.model_copy(update=values)
                    fact = fact.model_copy(
                        update={
                            **values,
                            **windows,
                            "wording": before.wording if text == old.text else fact.wording,
                        }
                    )
        reconciled.append(fresh)
        if fact:
            selected_facts[key(fresh)] = fact
    cues = reconciled
    verified = review.verified_cues
    protected = list(
        {(c.start, c.end, c.text): c for c in [*review.corrected_cues, *verified]}.values()
    )
    result = [
        c for c in cues if not any(c.start < v.end and v.start < c.end for v in protected)
    ] + protected
    if incomplete:
        pinned = [
            old
            for old in review.cues
            if key(old) in old_facts
            and old not in protected
            and not any(old.start < c.end and c.start < old.end for c in protected)
            and old_facts[key(old)].wording != "unresolved"
            and any(
                old.start < fresh.end
                and fresh.start < old.end
                and fresh.text.count("\n") < old.text.count("\n")
                and all(
                    automatic_captions.word_key(line)
                    in [automatic_captions.word_key(v) for v in old.text.split("\n")]
                    for line in fresh.text.split("\n")
                )
                and key(fresh) in new_facts
                and all(
                    getattr(new_facts[key(fresh)], edge + "_window")[1]
                    - getattr(new_facts[key(fresh)], edge + "_window")[0]
                    >= getattr(old_facts[key(old)], edge + "_window")[1]
                    - getattr(old_facts[key(old)], edge + "_window")[0]
                    for edge in ("start", "end")
                )
                for fresh in cues
            )
        ]
        for old in pinned:
            pieces = []
            for fresh in result:
                if fresh in protected or fresh.end <= old.start or fresh.start >= old.end:
                    pieces.append(fresh)
                else:
                    if fresh.start < old.start:
                        pieces.append(fresh.model_copy(update={"end": old.start}))
                    if fresh.end > old.end:
                        pieces.append(fresh.model_copy(update={"start": old.end}))
            result = pieces + [old]

    if incomplete:
        prior = [
            c
            for c in review.cues
            if c in protected
            or (
                not any(
                    left <= c.start + 0.000001 and right >= c.end - 0.000001
                    for left, right in observed_absence
                )
                and not any(
                    c.start < e["end"]
                    and e["start"] < c.end
                    and automatic_captions.word_key(c.text)
                    == automatic_captions.word_key(e["text"])
                    for e in excluded_intervals
                )
            )
        ]

        def superseded_tail(old):
            words = automatic_captions.word_key(old.text)
            if len(words) < 4 or not any(
                left <= (old.start + old.end) / 2 <= right for left, right in observed_absence
            ):
                return False
            for fresh in result:
                fact = selected_facts.get(key(fresh))
                full = automatic_captions.word_key(fresh.text)
                if (
                    fact
                    and fact.end_supported is True
                    and abs(fresh.end - old.start) <= 1 / 30 + 0.000001
                    and any(
                        SequenceMatcher(None, words, full[i : i + len(words)]).ratio() >= 0.9
                        for i in range(max(0, len(full) - len(words) + 1))
                    )
                ):
                    return True
            return False

        prior = [c for c in prior if c in protected or not superseded_tail(c)]
        if observed_window is not None:
            left, right = observed_window
            pieces = []
            for old in prior:
                if old.start < left:
                    pieces.append(old.model_copy(update={"end": min(old.end, left)}))
                if old.end > right:
                    pieces.append(old.model_copy(update={"start": max(old.start, right)}))
                start, end = max(old.start, left), min(old.end, right)
                if start < end:
                    pieces.append(old.model_copy(update={"start": start, "end": end}))
            prior = pieces
        result += [c for c in prior if not any(c.start < n.end and n.start < c.end for n in result)]
    if len(result) > 200:
        raise ReferenceError(
            413, "caption_cue_limit", "More than 200 retained cues need correction."
        )
    observations[:] = [selected_facts[key(c)] for c in result if key(c) in selected_facts]
    return sorted(result, key=lambda c: c.start), verified


def snapshot(project_id, stop=None, deadline=None):
    current = projects.get_project(project_id)
    if current.status != "active":
        raise ReferenceError(409, "project_deleting", "Project deletion is in progress.")
    clips = {}
    for clip in clip_library.read(project_id).clips:
        _, valid = clip_library.source(project_id, clip.id, stop=stop, deadline=deadline)
        clips[str(valid.id)] = valid.sha256
    if not clips:
        raise ReferenceError(409, "footage_required", "Upload footage before generating.")
    reference = jobs.get_operation(project_id)
    binding = None
    if reference.status == "ready":
        source = color.source(project_id, stop, deadline)
        binding = {
            "operation_id": source["reference_operation_id"],
            "source": source["identity"].model_dump(mode="json"),
        }
    with projects.database() as db:
        revisions = {
            table: (
                row[0]
                if (
                    row := db.execute(
                        f"SELECT revision FROM {table} WHERE project_id=?", (project_id,)
                    ).fetchone()
                )
                else 0
            )
            for table in REVISION_TABLES
        }
    return {
        "reference_url": current.reference.canonical_url,
        "reference": binding,
        "clips": clips,
        "revisions": revisions,
    }


def persist(project_id, operation, bound, cleanup_safe=True):
    operation.notices = Operation.bounded_notices(operation.notices)
    with projects.database() as db:
        db.execute(
            "UPDATE generation_operations SET state=?,operation=?,snapshot=?,cleanup_safe=? "
            "WHERE project_id=? AND operation_id=? AND revision=?",
            (
                operation.status,
                operation.model_dump_json(),
                json.dumps(bound),
                int(cleanup_safe),
                project_id,
                operation.operation_id,
                operation.revision,
            ),
        )


def begin_operation(project_id, request):
    prior = get_operation(project_id)
    if prior.revision != request.expected_revision:
        raise ReferenceError(409, "revision_conflict", "Generation changed. Reload its status.")
    if prior.status == "running":
        return prior, None
    row = record(project_id)
    if row and not row["cleanup_safe"]:
        raise ReferenceError(
            500, "cleanup_failure", "Owned processing needs manual cleanup before retry."
        )
    bound = snapshot(project_id)
    old = json.loads(row["snapshot"]) if row else None
    customized = (
        any(bound["revisions"].values()) if not row else old["revisions"] != bound["revisions"]
    )
    if customized and not request.replace_customized:
        raise ReferenceError(
            409,
            "generation_replace_required",
            "Saved edits changed. Confirm replacement, or keep editing in Customize.",
        )
    completed = (
        prior.completed
        if row
        and old == bound
        and prior.status in {"failed", "ready"}
        and (
            prior.options.model_dump(
                exclude={
                    "expected_revision",
                    "replace_customized",
                    "matching_mode",
                    "captions",
                    "accept_partial_captions",
                    "replace_verified_captions",
                }
            )
            == request.model_dump(
                exclude={
                    "expected_revision",
                    "replace_customized",
                    "matching_mode",
                    "captions",
                    "accept_partial_captions",
                    "replace_verified_captions",
                }
            )
        )
        else []
    )
    if (
        completed
        and prior.options.matching_mode != request.matching_mode
        and "sequence" in completed
    ):
        completed = []
    if completed and (prior.options.captions != request.captions or prior.status == "ready"):
        completed = [stage for stage in completed if stage != "captions"]
    operation = Operation(
        project_id=project_id,
        revision=prior.revision + 1,
        operation_id=str(uuid.uuid4()),
        status="running",
        stage=STAGES[0],
        options=request,
        completed=completed,
        notices=prior.notices if completed else [],
        caption_review=prior.caption_review if old == bound else None,
    )
    with projects.database() as db:
        db.execute(
            "INSERT INTO generation_operations VALUES (?,?,?,'running',?,?,1) "
            "ON CONFLICT(project_id) DO UPDATE SET operation_id=excluded.operation_id,"
            "revision=excluded.revision,"
            "state='running',operation=excluded.operation,snapshot=excluded.snapshot,cleanup_safe=1",
            (
                project_id,
                operation.operation_id,
                operation.revision,
                operation.model_dump_json(),
                json.dumps(bound),
            ),
        )
    return operation, {"operation": operation, "snapshot": bound}


def guard(project_id, operation_id, bound, stop=None, deadline=None):
    color.guard(stop, deadline)
    current = get_operation(project_id)
    if current.operation_id != operation_id or current.status != "running":
        raise ReferenceError(409, "generation_stale", "Generation is no longer current.")
    if snapshot(project_id, stop, deadline) != bound:
        raise ReferenceError(
            409,
            "generation_stale",
            "Footage, reference or saved edits changed. Retry with current inputs.",
        )


def stage_failure(project_id, module, operation):
    table = (
        "reference_operations"
        if module is jobs
        else "render_operations"
        if module is render
        else module.TABLE
    )
    with projects.database() as db:
        row = db.execute(
            f"SELECT cleanup_safe FROM {table} WHERE project_id=?", (project_id,)
        ).fetchone()
    return engine.RetrievalFailure(
        operation.failure_code or "generation_stage_failed",
        operation.message or "This stage failed. Retry explicitly.",
        cleanup_safe=bool(row and row[0]),
    )


async def owned(function, stop, *args):
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        stop.set()
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not task.cancelled():
            try:
                task.result()
            except engine.ProcessCleanupError:
                raise
            except engine.RetrievalFailure as error:
                if not error.cleanup_safe:
                    raise
            except Exception:
                pass
        raise


async def worker(project_id, operation_id, data, stop):
    operation, bound = data["operation"], data["snapshot"]
    deadline = time.monotonic() + TOTAL_SECONDS

    async def locked(function, *args, advance=None):
        nonlocal bound
        async with projects.operation_lock:
            await owned(guard, stop, project_id, operation_id, bound, stop, deadline)
            result = await owned(function, stop, *args)
            bound = await owned(snapshot, stop, project_id, stop, deadline)
            if advance and advance not in operation.completed:
                operation.completed.append(advance)
            await finish_thread(persist, project_id, operation, bound)
            return result

    async def stage(label):
        operation.stage = label
        async with projects.operation_lock:
            await owned(guard, stop, project_id, operation_id, bound, stop, deadline)
            await finish_thread(persist, project_id, operation, bound)

    async def job(module, analysis, *args):
        async with projects.operation_lock:
            await owned(guard, stop, project_id, operation_id, bound, stop, deadline)
            sub, source = await owned(module.begin_operation, stop, project_id, *args)
        if source:
            await jobs.worker(
                project_id,
                sub.operation_id,
                source,
                stop,
                analysis,
                overall_deadline=deadline,
                before_commit=lambda: guard(project_id, operation_id, bound, stop, deadline),
            )
        current = await projects.storage_call(module.get_operation, project_id)
        if current.status != "ready":
            raise stage_failure(project_id, module, current)
        return current

    failure = None
    safe = True
    try:
        # Setup is checked before retrieval or expensive analysis; no downloads here.
        if (
            operation.options.matching_mode == "subject_aware"
            and not visual_matching.readiness()["ready"]
        ):
            raise ReferenceError(
                503, "visual_setup", "Set up local visual matching before generating. See Details."
            )
        if operation.options.captions != "off":
            await locked(capability, project_id, operation.options.captions)
        await job(jobs, False)
        # Retaining the reference legitimately extends the owned source snapshot.
        async with projects.operation_lock:
            bound = await owned(snapshot, stop, project_id, stop, deadline)
            await finish_thread(persist, project_id, operation, bound)
        await job(color, True)
        pacing = await job(pacing_analysis, "pacing")
        operation.notices.extend(pacing.blueprint.interpretation_limits[:2])
        await stage(STAGES[1])
        if "sequence" not in operation.completed:
            current = sequence.read(project_id)
            if "slots" not in operation.completed:
                generated = await locked(
                    sequence.generate,
                    project_id,
                    sequence.Generate(
                        expected_revision=current.sequence.revision if current.sequence else 0,
                        replace=bool(current.sequence),
                    ),
                    advance="slots",
                )
            else:
                generated = current
            revision = generated.sequence.revision
            settings = assembly.Settings(
                expected_sequence_revision=revision,
                matching_mode=operation.options.matching_mode,
                allow_reused_ranges=operation.options.allow_reused_ranges,
            )
            selected = await job(assembly, "assembly", settings)
            if any(choice.clip_id is None for choice in selected.proposal.choices):
                missing = (
                    sum(
                        choice.duration_frames
                        for choice in selected.proposal.choices
                        if choice.clip_id is None
                    )
                    / 30
                )
                raise ReferenceError(
                    409,
                    "footage_shortage",
                    f"Need footage for {missing:.2f} more seconds. "
                    "Upload more, or explicitly allow reused footage.",
                )
            assignment = await locked(
                assembly.apply,
                project_id,
                assembly.Apply(**settings.model_dump(), proposal_id=selected.proposal.id),
            )
            await locked(
                sequence.save,
                project_id,
                sequence.Save.model_validate(assignment),
                advance="sequence",
            )
            operation.notices.extend(selected.proposal.warnings[:5])
        if operation.options.matching_mode == "measurements":
            notice = (
                "Continued using motion and scene measurements; "
                "subject-aware matching was not used."
            )
            if notice not in operation.notices:
                operation.notices.append(notice)
        saved_sequence = sequence.read(project_id).sequence
        await stage(STAGES[2])
        if "grading" not in operation.completed:
            revision = grading.read_settings(project_id).revision
            await job(
                grading,
                "grading",
                grading.Prepare(
                    expected_revision=revision,
                    shot_slots=[slot.id for slot in saved_sequence.slots],
                    expected_sequence_revision=saved_sequence.revision,
                ),
            )
            await locked(
                grading.save,
                project_id,
                grading.Save(
                    expected_revision=revision,
                    mode="transfer",
                    shot_slots=[slot.id for slot in saved_sequence.slots],
                    controls={
                        f"slot:{slot.id}": grading.transfer.Controls(strength=0.7)
                        for slot in saved_sequence.slots
                    },
                ),
                advance="grading",
            )
        if "audio" not in operation.completed:
            previous = audio_settings.read(project_id).settings
            await locked(
                audio_settings.save,
                project_id,
                audio_settings.SaveRequest(
                    expected_revision=previous.revision,
                    mode=operation.options.audio,
                    original_volume=70.0 if operation.options.audio == "mix" else 100.0,
                    reference_volume=30.0 if operation.options.audio == "mix" else 100.0,
                    refresh_sources=True,
                ),
                advance="audio",
            )
        await stage(STAGES[3])
        if "captions" not in operation.completed:
            await locked(
                prepare_captions,
                project_id,
                operation.options.captions,
                stop,
                min(deadline, time.monotonic() + automatic_captions.TOTAL_SECONDS),
                operation,
                advance="captions",
            )
        await stage(STAGES[4])
        current = captions.read(project_id).track
        await job(
            render,
            "render",
            0,
            None,
            audio_settings.read(project_id).settings.revision,
            current.revision,
            framing.read(project_id).settings.revision,
            saved_sequence.revision,
            grading.read_settings(project_id).revision,
        )
        output = render.get_operation(project_id).output
        operation.output_id = str(output.output_id)
        operation.status = "ready"
        operation.notices = list(dict.fromkeys(operation.notices))[:30]
        async with projects.operation_lock:
            await owned(guard, stop, project_id, operation_id, bound, stop, deadline)
            await finish_thread(persist, project_id, operation, bound)
    except engine.ProcessCleanupError:
        safe = False
        failure = (
            "cleanup_failure",
            "Owned processing could not be confirmed stopped; files retained.",
        )
    except engine.RetrievalFailure as error:
        safe = error.cleanup_safe
        failure = (
            error.code,
            "Generation stopped. Retry explicitly to resume safe saved work."
            if error.code == "interrupted"
            else error.message,
        )
    except ReferenceError as error:
        safe = error.code != "cleanup_failure"
        failure = (error.code, error.message)
    except (asyncio.CancelledError, engine.ProbeTimeout):
        stop.set()
        failure = ("interrupted", "Generation stopped. Retry explicitly to resume safe saved work.")
    except Exception:
        logger.exception("Generation failed; internal details withheld from UI")
        failure = ("generation_failed", "Generation failed. Retry or open Details.")
    finally:
        if failure:
            operation.status = "failed"
            operation.failure_code, operation.message = failure
            await finish_thread(persist, project_id, operation, bound, safe)


def staging(operation_id):
    return projects.DATA_DIR / "generation-staging" / projects.identifier(operation_id)


def capability(project_id, mode):
    import workflow_capabilities

    result = workflow_capabilities.inspect(project_id)
    if not result.captions.available or not result.ocr.available:
        raise ReferenceError(
            503,
            "caption_setup",
            "Set up local caption reading/rendering, or choose captions Off. See Details.",
        )
    if mode == "output_audio" and not result.transcription.available:
        raise ReferenceError(
            503,
            "transcription_setup",
            "Set up local speech captions, or choose another caption mode. See Details.",
        )


def prepare_captions(project_id, mode, stop, deadline, operation):
    directory = staging(operation.operation_id)
    directory.parent.mkdir(exist_ok=True)
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir()
    safe = True
    engine._control.stop = stop
    try:
        previous = captions.read(project_id).track
        cues, notices, evidence = [], [], None
        if mode != "off":
            source = color.source(project_id, stop, deadline)
            review = operation.caption_review
            if mode == "reference" and operation.options.accept_partial_captions:
                if (
                    not review
                    or (
                        review.evidence.method != automatic_captions.METHOD
                        and not review.verified_cues
                    )
                    or not review.cues
                    or review.evidence.source != source["identity"]
                    or str(review.evidence.reference_operation_id)
                    != str(source["reference_operation_id"])
                ):
                    raise ReferenceError(
                        409, "caption_review_required", "Retry caption reading first."
                    )
                cues = automatic_captions.fit_review_width(
                    project_id,
                    review.cues,
                    directory,
                    stop,
                    min(deadline, time.monotonic() + automatic_captions.TOTAL_SECONDS),
                )
                notices = list(review.notices)
                evidence = review.evidence.model_dump(mode="json")
                notices.append(
                    "Incomplete captions exported by explicit confirmation; some words are missing."
                )
            else:
                cues, notices, evidence = automatic_captions.detect(
                    project_id, source, directory, stop, deadline
                )
                incomplete = evidence.pop("unreadable_samples", 0)
                observed_window = evidence.pop("observed_window", None)
                excluded_intervals = evidence.pop("excluded_intervals", [])
                observations = [
                    CaptionObservation.model_validate(c) for c in evidence.pop("observations", [])
                ]
                observed_absence = evidence.pop("observed_absence", [])
                color.guard(stop, deadline)
                if mode == "reference":
                    cues, verified = retain_reviewed(
                        cues,
                        review,
                        source,
                        operation.options.replace_verified_captions,
                        bool(incomplete),
                        observed_window=observed_window,
                        excluded_intervals=excluded_intervals,
                        observations=observations,
                        observed_absence=observed_absence,
                    )
                if mode == "reference" and incomplete:
                    operation.caption_review = CaptionReview(
                        cues=cues,
                        notices=notices[:30],
                        evidence=captions.VisibleBinding.model_validate(evidence),
                        unreadable_samples=incomplete,
                        observations=observations,
                        verified_cues=verified,
                        corrected_cues=[
                            c
                            for c in (
                                review.corrected_cues
                                if review and not operation.options.replace_verified_captions
                                else []
                            )
                            if c in cues
                        ],
                    )
                    raise ReferenceError(
                        422,
                        "caption_partial",
                        f"Caption reading is incomplete. {len(cues)} detected cues are saved; "
                        "retry reading or continue without captions.",
                    )
                operation.caption_review = (
                    CaptionReview(
                        cues=cues,
                        notices=notices[:30],
                        evidence=captions.VisibleBinding.model_validate(evidence),
                        unreadable_samples=0,
                        observations=observations,
                        verified_cues=verified,
                        corrected_cues=[
                            c
                            for c in (
                                review.corrected_cues
                                if review and not operation.options.replace_verified_captions
                                else []
                            )
                            if c in cues
                        ],
                    )
                    if mode == "reference"
                    and (
                        verified
                        or (
                            review
                            and review.corrected_cues
                            and not operation.options.replace_verified_captions
                        )
                    )
                    else None
                )
        selected_sequence = sequence.read(project_id).sequence
        values = dict(
            expected_revision=previous.revision,
            mode="sequence",
            expected_sequence_revision=selected_sequence.revision,
            confirm_rebind=True,
            enabled=bool(cues),
            cues=cues,
        )
        provenance = None
        if mode == "reference":
            values["provenance"] = "automatic_reference"
            provenance = captions.VisibleBinding.model_validate(evidence)
        audio_binding = None
        if mode == "output_audio":
            # Transcribe the actual assembled audio without publishing this scratch export.
            disabled = previous.model_copy(update={"enabled": False})
            spec = render.specification(
                project_id,
                0,
                None,
                audio_settings.read(project_id).settings.revision,
                previous.revision,
                framing.read(project_id).settings.revision,
                selected_sequence.revision,
                grading.read_settings(project_id).revision,
                caption_override=disabled,
            )
            spec = spec.model_copy(update={"captions": disabled})
            source = render.footage.source(project_id, stop, deadline)
            scratch = directory / "assembled-audio-source" / str(uuid.uuid4())
            path, output = render.pipeline(
                {"project_id": project_id, "path": source["path"], "spec": spec},
                scratch,
                stop,
                deadline,
            )
            if not output.has_audio:
                raise ReferenceError(
                    409,
                    "output_audio_unavailable",
                    "This output is silent. Choose reference captions or Off.",
                )
            speech = directory / "speech"
            speech.mkdir()
            recognized = transcribe_local.run(
                path, output.duration_seconds, "auto", speech, stop, deadline
            )
            if not cues:
                notices.append(
                    "No reference caption appearance was readable; "
                    "speech captions use the default supported style."
                )
            style = cues[0].appearance if cues else None
            cues = [
                captions.Cue.model_validate(cue).model_copy(update={"appearance": style})
                for cue in recognized[0]
            ]
            values.update(cues=cues, enabled=bool(cues), provenance="generated_output_audio")
            audio_binding = (
                captions.AutomaticBinding(
                    timeline=captions.timeline(project_id, "sequence"),
                    audio=audio_settings.read(project_id).settings,
                ),
                output.sha256,
            )
        if mode == "off" and operation.caption_review:
            operation.notices.append(
                "Captions explicitly disabled for this generation; "
                "unreadable text was not treated as absent."
            )
        result = captions.save(
            project_id,
            captions.SaveRequest(**values),
            visible_binding=provenance,
            generated_audio=audio_binding,
        )
        operation.notices.extend(notices)
        return result
    except engine.ProcessCleanupError:
        safe = False
        raise
    except engine.RetrievalFailure as error:
        safe = error.cleanup_safe
        raise
    except ReferenceError as error:
        safe = error.code != "cleanup_failure"
        raise
    finally:
        if safe:
            try:
                shutil.rmtree(directory)
            except OSError:
                raise ReferenceError(
                    500, "cleanup_failure", "Caption staging needs cleanup before retry."
                ) from None


def prepare_delete(project_id):
    row = record(project_id)
    if row:
        if not row["cleanup_safe"]:
            raise ReferenceError(
                500, "cleanup_failure", "Owned generation files retained for manual review."
            )
        directory = staging(row["operation_id"])
        if directory.exists():
            shutil.rmtree(directory)


def recover():
    with projects.database() as db:
        rows = db.execute("SELECT * FROM generation_operations").fetchall()
    for row in rows:
        operation = Operation.model_validate_json(row["operation"])
        if operation.status == "running":
            operation.status = "failed"
            operation.failure_code = "interrupted"
            operation.message = "Backend restarted. Retry explicitly to resume valid saved work."
            persist(
                row["project_id"], operation, json.loads(row["snapshot"]), bool(row["cleanup_safe"])
            )
        if row["cleanup_safe"]:
            prepare_delete(row["project_id"])
