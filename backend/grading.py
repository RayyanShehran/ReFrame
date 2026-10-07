"""Revisioned modes and per-source matches, using the existing single media worker."""

import hashlib
import json
import shutil
import sys
import uuid
from functools import partial
from typing import Annotated, Literal

from pydantic import Field, model_validator

import clip_library
import color_analysis as color
import color_recipe
import color_transfer as transfer
import framing
import projects
import reference_engine as engine
import sequence
from references import ReferenceError

ALGORITHM = transfer.ALGORITHM
TABLE = "grading_operations"
STAGING = "grading-staging"
TOTAL_SECONDS = 300
TEMP_BUDGET = 16 * 1024 * 1024
component = sys.modules[__name__]
staging = partial(color.staging, component=component)
clean_stage = partial(color.clean_stage, component=component)
fail_operation = partial(color.fail_operation, component=component)
prepare_delete = partial(color.prepare_delete, component=component)
compensate = color.compensate
recover = partial(color.recover, component=component)


class Settings(color.Schema):
    schema_version: Literal[1] = 1
    mode: Literal["original", "basic", "transfer"] = "basic"
    revision: int = Field(default=0, ge=0, strict=True)
    controls: dict[str, transfer.Controls] = Field(default_factory=dict, max_length=70)
    shot_slots: list[uuid.UUID] = Field(default_factory=list, max_length=60)
    updated_at: str | None = None


class Save(color.Schema):
    expected_revision: int = Field(ge=0, strict=True)
    mode: Literal["original", "basic", "transfer"]
    controls: dict[str, transfer.Controls] = Field(default_factory=dict, max_length=70)
    shot_slots: list[uuid.UUID] = Field(default_factory=list, max_length=60)


class Prepare(color.Schema):
    expected_revision: int = Field(ge=0, strict=True)
    replace: bool = Field(default=False, strict=True)
    shot_slots: list[uuid.UUID] = Field(default_factory=list, max_length=60)
    expected_sequence_revision: int | None = Field(default=None, ge=1, strict=True)


Time = Annotated[float, Field(ge=0, le=120.1, allow_inf_nan=False)]


class Match(color.Schema):
    schema_version: Literal[1] = 1
    key: str = Field(pattern=r"^(clip|slot):[0-9a-f-]{36}$")
    clip_id: uuid.UUID
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    reference: color_recipe.ReferenceBinding
    source_interval: tuple[Time, Time] | None = None
    reference_interval: tuple[Time, Time] | None = None
    slot_id: uuid.UUID | None = None
    sequence_revision: int | None = Field(default=None, ge=1, strict=True)
    model: transfer.Model
    ffmpeg_version: str
    prepared_at: str

    @model_validator(mode="after")
    def coherent(self):
        expected = f"slot:{self.slot_id}" if self.slot_id else f"clip:{self.clip_id}"
        if self.key != expected:
            raise ValueError("Invalid transform route")
        if self.slot_id:
            if (
                not self.source_interval
                or not self.reference_interval
                or not self.sequence_revision
            ):
                raise ValueError("Assigned-shot matches need ranges and a sequence revision")
            if any(start >= end for start, end in (self.source_interval, self.reference_interval)):
                raise ValueError("Match intervals must be positive")
        elif self.source_interval or self.reference_interval or self.sequence_revision:
            raise ValueError("Project-look matches use the entire clip")
        return self


class Prepared(color.Schema):
    matches: list[Match] = Field(max_length=70)


class Entry(color.Schema):
    match: Match
    valid: bool
    message: str | None = None


class Operation(color.Schema):
    operation_id: str | None = None
    status: Literal["idle", "running", "ready", "failed"] = "idle"
    settings: Settings = Field(default_factory=Settings)
    entries: list[Entry] = Field(default_factory=list)
    failure_code: str | None = None
    message: str | None = None
    completed: int = 0
    total: int = 0


def read_settings(project_id):
    projects.get_project(project_id)
    with projects.database() as db:
        row = db.execute(
            "SELECT settings FROM grading_settings WHERE project_id=?", (project_id,)
        ).fetchone()
    return Settings.model_validate_json(row[0]) if row else Settings()


def reference_binding(project_id, stop=None, deadline=None):
    result = color.get_operation(project_id, True, stop=stop, deadline=deadline)
    if result.status != "ready":
        raise ReferenceError(
            409, "reference_colors_required", "Retain and analyze valid reference colors first."
        )
    blueprint = result.blueprint
    return color_recipe.ReferenceBinding(
        source=blueprint.source,
        operation_id=result.operation_id,
        schema_version=blueprint.schema_version,
        algorithm_version=blueprint.algorithm_version,
        analyzed_at=blueprint.analyzed_at,
    )


def cached(cache, key, function):
    if key not in cache:
        try:
            cache[key] = function()
        except ReferenceError as exc:
            cache[key] = exc
    if isinstance(cache[key], ReferenceError):
        raise cache[key]
    return cache[key]


def validate(project_id, match, stop=None, deadline=None, *, cache=None):
    cache = {} if cache is None else cache
    if match.model.algorithm != ALGORITHM or match.reference != cached(
        cache, "reference", lambda: reference_binding(project_id, stop, deadline)
    ):
        raise ReferenceError(
            409, "match_stale", "Reference or analysis changed. Match explicitly again."
        )
    path, clip = cached(
        cache,
        str(match.clip_id),
        lambda: clip_library.source(project_id, match.clip_id, stop=stop, deadline=deadline),
    )
    if clip.sha256 != match.source_hash:
        raise ReferenceError(409, "match_stale", "Footage changed. Match this clip again.")
    if match.slot_id:
        saved = cached(cache, "sequence", lambda: sequence.read(project_id))
        slot = (
            next((s for s in saved.sequence.slots if s.id == match.slot_id), None)
            if saved.sequence
            else None
        )
        if (
            saved.status != "ready"
            or not slot
            or slot.clip_id != match.clip_id
            or slot.reference_start_frame is None
            or (slot.source_start_frame / 30, slot.source_end_frame / 30) != match.source_interval
            or (slot.reference_start_frame / 30, slot.reference_end_frame / 30)
            != match.reference_interval
        ):
            raise ReferenceError(
                409,
                "match_stale",
                "Assigned source/reference range changed. Match this slot again.",
            )
    return path, clip


def get_operation(project_id, require_active=False):
    settings = read_settings(project_id)
    with projects.database() as db:
        row = db.execute(
            "SELECT * FROM grading_operations WHERE project_id=?", (project_id,)
        ).fetchone()
        matches = db.execute(
            "SELECT match FROM color_matches WHERE project_id=? ORDER BY key", (project_id,)
        ).fetchall()
    entries, cache = [], {}
    for record in matches:
        match = Match.model_validate_json(record[0])
        try:
            validate(project_id, match, cache=cache)
            entries.append(Entry(match=match, valid=True))
        except ReferenceError as exc:
            entries.append(Entry(match=match, valid=False, message=exc.message))
    return Operation(
        operation_id=row["operation_id"] if row else None,
        status=row["state"] if row else "idle",
        settings=settings,
        entries=entries,
        failure_code=row["failure_code"] if row else None,
        message=row["message"] if row else None,
        completed=row["completed"] if row else 0,
        total=row["total"] if row else 0,
    )


def check_revision(project_id, expected):
    if read_settings(project_id).revision != expected:
        raise ReferenceError(
            409, "revision_conflict", "Saved color mode changed. Reload; your draft is retained."
        )


def inputs(project_id, request, stop=None, deadline=None):
    check_revision(project_id, request.expected_revision)
    ref = color.source(project_id, stop, deadline)
    binding = reference_binding(project_id, stop, deadline)
    targets = []
    for clip in clip_library.read(project_id).clips:
        path, saved = clip_library.source(project_id, clip.id, stop=stop, deadline=deadline)
        targets.append(
            dict(
                key=f"clip:{clip.id}",
                clip=saved,
                path=path,
                source_interval=None,
                reference_interval=None,
                slot_id=None,
            )
        )
    if not targets:
        raise ReferenceError(409, "footage_required", "Upload footage first.")
    if len(set(request.shot_slots)) != len(request.shot_slots):
        raise ReferenceError(422, "invalid_slots", "Choose unique slot IDs.")
    if request.shot_slots:
        seq = sequence.read(project_id)
        if seq.status != "ready" or seq.sequence.revision != request.expected_sequence_revision:
            raise ReferenceError(
                409, "sequence_stale", "Save a valid sequence and include its revision."
            )
        for id in request.shot_slots:
            slot = next((s for s in seq.sequence.slots if s.id == id), None)
            if not slot or slot.reference_start_frame is None:
                raise ReferenceError(
                    422,
                    "unassociated_slot",
                    "This slot has no assigned reference shot. Use the project look.",
                )
            path, clip = clip_library.source(project_id, slot.clip_id, stop=stop, deadline=deadline)
            targets.append(
                dict(
                    key=f"slot:{id}",
                    clip=clip,
                    path=path,
                    slot_id=id,
                    source_interval=(slot.source_start_frame / 30, slot.source_end_frame / 30),
                    reference_interval=(
                        slot.reference_start_frame / 30,
                        slot.reference_end_frame / 30,
                    ),
                )
            )
    signature = hashlib.sha256(
        json.dumps(
            dict(
                reference=binding.model_dump(mode="json"),
                targets=[
                    dict(
                        key=t["key"],
                        hash=t["clip"].sha256,
                        source=t["source_interval"],
                        reference=t["reference_interval"],
                    )
                    for t in targets
                ],
                revision=request.expected_revision,
            ),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return dict(
        project_id=project_id,
        request=request,
        reference=ref,
        binding=binding,
        targets=targets,
        signature=signature,
    )


def reusable(project_id, request):
    data = inputs(project_id, request)
    current = get_operation(project_id)
    with projects.database() as db:
        row = db.execute(
            "SELECT source_hash FROM grading_operations WHERE project_id=?", (project_id,)
        ).fetchone()
    return current if row and row[0] == data["signature"] and current.status == "running" else None


def begin_operation(project_id, request):
    data = inputs(project_id, request)
    current = get_operation(project_id)
    # Never replace an existing transform (and its controls) without an explicit action.
    prior = {e.match.key: e for e in current.entries}
    data["targets"] = [
        t
        for t in data["targets"]
        if request.replace or t["key"] not in prior or not prior[t["key"]].valid
    ]
    if not data["targets"]:
        return current, None
    if current.operation_id:
        prepare_delete(project_id)
    id = str(uuid.uuid4())
    with projects.database() as db:
        db.execute("DELETE FROM grading_operations WHERE project_id=?", (project_id,))
        db.execute(
            "INSERT INTO grading_operations(project_id,operation_id,state,started_at,"
            "source_hash,algorithm_version,total) VALUES(?,?,'running',?,?,?,?)",
            (project_id, id, projects.now(), data["signature"], ALGORITHM, len(data["targets"])),
        )
    return get_operation(project_id), data


def sample(path, interval, directory, deadline):
    import video_render as render

    video, _, duration = render.probe(path, directory, deadline, temp_budget=TEMP_BUDGET)
    metadata = color.inspect_colors(video)
    framing.display_dimensions(video)  # Validate rotation/SAR; autorotation handles the decode.
    start, end = interval or (0, duration)
    if not 0 <= start < end <= duration + 1 / 30:
        raise ReferenceError(422, "invalid_range", "Color sampling range exceeds source duration.")
    length = end - start
    filters = (
        f"setpts=PTS-STARTPTS,trim=start={start:.12f}:end={end:.12f},"
        f"setpts=PTS-{start + length / 24:.12f}/TB,"
        f"fps={12 / length:.12f}:start_time=0:round=near:eof_action=pass,"
        "scale=64:64:flags=area:in_color_matrix=bt709:out_color_matrix=bt709:"
        f"in_range={'full' if metadata.color_range == 'pc' else 'limited'}:out_range=full,"
        "format=rgb24"
    )
    raw = render.tool(
        [
            shutil.which("ffmpeg") or "ffmpeg",
            "-v",
            "error",
            "-nostdin",
            "-xerror",
            "-abort_on",
            "empty_output",
            "-threads",
            "1",
            "-filter_threads",
            "1",
            "-protocol_whitelist",
            "file",
            "-i",
            str(path),
            "-map",
            f"0:{video['index']}",
            "-an",
            "-sn",
            "-dn",
            "-vf",
            filters,
            "-frames:v",
            "12",
            "-fps_mode",
            "passthrough",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "-",
        ],
        directory,
        "transfer-samples",
        deadline,
        60,
        12 * 64 * 64 * 3,
        temp_budget=TEMP_BUDGET,
    )
    return transfer.pixels(raw)


def pipeline(data, directory, stop, deadline):
    import video_render as render

    directory.mkdir(parents=True, exist_ok=False)
    engine._control.stop = stop
    try:
        version = (
            render.tool(
                [shutil.which("ffmpeg") or "ffmpeg", "-version"],
                directory,
                "transfer-version",
                deadline,
                5,
                8192,
                temp_budget=TEMP_BUDGET,
            )
            .decode()
            .splitlines()[0]
        )
        reference_samples = {}
        result = []
        for i, target in enumerate(data["targets"]):
            color.guard(stop, deadline)
            interval = target["reference_interval"]
            if interval not in reference_samples:
                reference_samples[interval] = sample(
                    data["reference"]["path"], interval, directory, deadline
                )
            source, mask = sample(target["path"], target["source_interval"], directory, deadline)
            ref, refmask = reference_samples[interval]
            result.append(
                Match(
                    key=target["key"],
                    clip_id=target["clip"].id,
                    source_hash=target["clip"].sha256,
                    reference=data["binding"],
                    source_interval=target["source_interval"],
                    reference_interval=interval,
                    slot_id=target["slot_id"],
                    sequence_revision=data["request"].expected_sequence_revision
                    if target["slot_id"]
                    else None,
                    model=transfer.fit(source, ref, mask or refmask),
                    ffmpeg_version=version,
                    prepared_at=projects.now(),
                )
            )
            with projects.database() as db:
                db.execute(
                    "UPDATE grading_operations SET completed=? WHERE project_id=? "
                    "AND source_hash=? AND state='running'",
                    (i + 1, data["project_id"], data["signature"]),
                )
        return data, Prepared(matches=result)
    except engine.ProcessCleanupError:
        raise engine.RetrievalFailure(
            "cleanup_failure",
            "Color processes could not be confirmed stopped; staging retained.",
            False,
        ) from None
    except engine.ProbeInterrupted:
        raise engine.RetrievalFailure("interrupted", "Color matching was interrupted.") from None
    except (engine.SizeLimit, engine.ToolOutputError):
        raise engine.RetrievalFailure(
            "transfer_limit", "Color matching exceeded its bounded output/staging budget."
        ) from None
    except (ValueError, KeyError, TypeError):
        raise engine.RetrievalFailure(
            "transfer_invalid", "Complete SDR color samples could not be verified."
        ) from None
    finally:
        engine._control.stop = None


def commit(project_id, operation_id, data, prepared, stop, deadline):
    color.guard(stop, deadline)
    check_revision(project_id, data["request"].expected_revision)
    cache = {}
    for match in prepared.matches:
        validate(project_id, match, stop, deadline, cache=cache)
    clean_stage(operation_id)
    with projects.database() as db:
        row = db.execute(
            "SELECT * FROM grading_operations WHERE project_id=?", (project_id,)
        ).fetchone()
        if (
            projects.row_project(db, project_id)["status"] != "active"
            or not row
            or row["operation_id"] != operation_id
            or row["state"] != "running"
        ):
            raise engine.RetrievalFailure("interrupted", "Color operation is no longer current.")
        color.guard(stop, deadline)
        for match in prepared.matches:
            db.execute(
                "INSERT INTO color_matches VALUES(?,?,?) ON CONFLICT(project_id,key) "
                "DO UPDATE SET match=excluded.match",
                (project_id, match.key, match.model_dump_json()),
            )
        db.execute(
            "UPDATE grading_operations SET state='ready',finished_at=?,blueprint=? "
            "WHERE project_id=? AND operation_id=?",
            (projects.now(), prepared.model_dump_json(), project_id, operation_id),
        )
        db.execute("UPDATE projects SET updated_at=? WHERE id=?", (projects.now(), project_id))


def save(project_id, request):
    check_revision(project_id, request.expected_revision)
    current = get_operation(project_id)
    keys = {e.match.key: e for e in current.entries}
    if not set(request.controls) <= keys.keys() or len(set(request.shot_slots)) != len(
        request.shot_slots
    ):
        raise ReferenceError(
            422, "invalid_controls", "Only prepared clips/slots can have color overrides."
        )
    for id in request.shot_slots if request.mode == "transfer" else []:
        if f"slot:{id}" not in keys or not keys[f"slot:{id}"].valid:
            raise ReferenceError(
                409, "match_stale", "Prepare a valid assigned-shot match before selecting it."
            )
    if request.mode == "transfer":
        originals = [clip for clip in clip_library.read(project_id).clips if clip.primary]
        if not originals:
            raise ReferenceError(409, "match_required", "Upload and match the original clip first.")
        for clip in originals:
            if f"clip:{clip.id}" not in keys or not keys[f"clip:{clip.id}"].valid:
                raise ReferenceError(
                    409, "match_required", "Match the original clip to reference colors first."
                )
    settings = Settings(
        mode=request.mode,
        controls=request.controls,
        shot_slots=request.shot_slots,
        revision=request.expected_revision + 1,
        updated_at=projects.now(),
    )
    with projects.database() as db:
        if projects.row_project(db, project_id)["status"] != "active":
            raise ReferenceError(409, "project_deleting", "Retry project deletion first.")
        row = db.execute(
            "SELECT revision FROM grading_settings WHERE project_id=?", (project_id,)
        ).fetchone()
        if (row[0] if row else 0) != request.expected_revision:
            raise ReferenceError(
                409, "revision_conflict", "Color settings changed. Reload explicitly."
            )
        db.execute(
            "INSERT INTO grading_settings VALUES(?,?,?) ON CONFLICT(project_id) "
            "DO UPDATE SET revision=excluded.revision,settings=excluded.settings",
            (project_id, settings.revision, settings.model_dump_json()),
        )
        db.execute("UPDATE projects SET updated_at=? WHERE id=?", (projects.now(), project_id))
    return get_operation(project_id)


class Snapshot(color.Schema):
    settings: Settings
    matches: list[Match] = Field(default_factory=list, max_length=70)


def snapshot(project_id, clip_ids, slots=(), stop=None, deadline=None):
    settings = read_settings(project_id)
    if settings.mode != "transfer":
        return Snapshot(settings=settings)
    keys = {f"clip:{id}" for id in clip_ids}
    for slot in slots:
        if slot.id in settings.shot_slots:
            keys.add(f"slot:{slot.id}")
    with projects.database() as db:
        records = db.execute(
            "SELECT key,match FROM color_matches WHERE project_id=?", (project_id,)
        ).fetchall()
    models = {r["key"]: Match.model_validate_json(r["match"]) for r in records}
    result, cache = [], {}
    for key in sorted(keys):
        if key not in models:
            raise ReferenceError(
                409, "match_required", "Match every selected clip/shot before rendering."
            )
        validate(project_id, models[key], stop, deadline, cache=cache)
        result.append(models[key])
    return Snapshot(settings=settings, matches=result)


def routed(bound, clip_id, slot=None):
    key = f"slot:{slot.id}" if slot and slot.id in bound.settings.shot_slots else f"clip:{clip_id}"
    match = next((m for m in bound.matches if m.key == key), None)
    if bound.settings.mode == "transfer" and not match:
        raise ReferenceError(
            409, "match_required", "The selected clip/slot has no saved transform."
        )
    return match, bound.settings.controls.get(key, transfer.Controls())


def filter_for(bound, clip_id, directory, slot=None):
    if bound.settings.mode == "original":
        return "null"
    if bound.settings.mode == "basic":
        raise ValueError("Basic uses its existing recipe filter")
    match, controls = routed(bound, clip_id, slot)
    if controls.strength == 0:
        return "null"
    name = (
        "transfer-"
        + hashlib.sha256(
            (match.model_dump_json() + controls.model_dump_json()).encode()
        ).hexdigest()[:24]
        + ".cube"
    )
    path = directory / name
    if not path.exists():
        transfer.cube(path, match.model, controls)
    from captions import filter_path

    return (
        f"format=gbrp,lut3d=file={filter_path(path)}:interp=tetrahedral,"
        "scale=iw:ih:in_range=full:out_range=limited:out_color_matrix=bt709,"
        "format=yuv420p,setparams=range=limited:colorspace=bt709"
    )
