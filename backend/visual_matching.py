"""Pinned offline CLIP image inference; only `setup` can access the network."""

import argparse
import base64
import hashlib
import io
import json
import math
import os
import sys
import threading
import time
import urllib.request
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import color_analysis as color
import framing
import projects
import reference_engine as engine

REPOSITORY = "Xenova/clip-vit-base-patch32"
REVISION = "d15189d7028b43f1d3e65039190477f6af591c2a"
SHA256 = "583fd1110a514667812fee7d684952aaf82a99b959760c8d7dca7e0ab9839299"
MODEL_BYTES = 89117001
MODEL = Path(__file__).resolve().parents[1] / "data" / "visual-models" / REVISION / "vision.onnx"
ALGORITHM = "clip-q8-2fps-center224-v1"
SAMPLING = "shot-balanced-adaptive-v1"
MAX_SAMPLES = 512
REFINEMENT_SAMPLES = 112
FRAME_BYTES = 224 * 224 * 3
TEMP_BUDGET = 96 * 1024 * 1024
CACHE_BUDGET = 32 * 1024 * 1024
JSON_LIMIT = 24 * 1024 * 1024
SETUP = (
    "In backend: uv sync --locked --extra visual --extra transcription; "
    "uv run --locked --extra visual --extra transcription python visual_matching.py setup"
)


def readiness():
    try:
        ready = MODEL.is_file() and MODEL.stat().st_size == MODEL_BYTES
        ready = ready and all(
            version(name) == expected
            for name, expected in {
                "onnxruntime": "1.30.0",
                "numpy": "2.5.3",
                "Pillow": "12.3.0",
                "psutil": "7.2.2",
            }.items()
        )
    except (OSError, PackageNotFoundError):
        ready = False
    return {
        "ready": bool(ready),
        "repository": REPOSITORY,
        "revision": REVISION,
        "algorithm": ALGORITHM,
        "setup": SETUP,
    }


def verify_model():
    if not readiness()["ready"]:
        raise engine.RetrievalFailure("model_unavailable", SETUP)
    with MODEL.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != SHA256:
            raise engine.RetrievalFailure("model_invalid", "Local model checksum failed. " + SETUP)


def setup():
    MODEL.parent.mkdir(parents=True, exist_ok=True)
    temporary = MODEL.with_suffix(".part")
    try:
        with (
            urllib.request.urlopen(
                f"https://huggingface.co/{REPOSITORY}/resolve/{REVISION}/onnx/vision_model_quantized.onnx",
                timeout=30,
            ) as response,
            temporary.open("wb") as target,
        ):
            size = 0
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > MODEL_BYTES:
                    raise ValueError("Model download exceeded its exact size")
                target.write(chunk)
        with temporary.open("rb") as stream:
            if size != MODEL_BYTES or hashlib.file_digest(stream, "sha256").hexdigest() != SHA256:
                raise ValueError("Model checksum failed")
        temporary.replace(MODEL)
    finally:
        temporary.unlink(missing_ok=True)
    print(json.dumps(readiness()))


class Sample(color.Schema):
    frame: int = color.Field(ge=0, le=3599, strict=True)
    embedding: list[float] = color.Field(min_length=512, max_length=512)
    image: str = color.Field(max_length=44000)


class Analysis(color.Schema):
    source_hash: str = color.Field(pattern=r"^[0-9a-f]{64}$")
    revision: str
    algorithm: str
    samples: list[Sample] = color.Field(min_length=1, max_length=512)


def validate(data, digest, frames=None):
    try:
        result = (
            Analysis.model_validate_json(data)
            if isinstance(data, str)
            else Analysis.model_validate(data)
        )
        if (
            result.source_hash != digest
            or result.revision != REVISION
            or result.algorithm != ALGORITHM
        ):
            raise ValueError
        actual = [s.frame for s in result.samples]
        if actual != sorted(set(actual)) or (frames is not None and actual != frames):
            raise ValueError
        for sample in result.samples:
            if not all(math.isfinite(v) for v in sample.embedding):
                raise ValueError
            if not 0.999 < math.sqrt(sum(v * v for v in sample.embedding)) < 1.001:
                raise ValueError
            image = base64.b64decode(sample.image, validate=True)
            if (
                not image.startswith(b"\xff\xd8")
                or not image.endswith(b"\xff\xd9")
                or len(image) > 32768
            ):
                raise ValueError
        return result
    except (ValueError, TypeError):
        raise engine.RetrievalFailure(
            "cache_invalid", "Visual analysis data failed validation."
        ) from None


def spread(values, count):
    if count >= len(values):
        return list(values)
    if count == 1:
        return [values[len(values) // 2]]
    return [values[round(i * (len(values) - 1) / (count - 1))] for i in range(count)]


def sample_requests(items, reference_hash=None, intervals=(), budget=MAX_SAMPLES):
    """Reserve every reference shot and every source before sharing the remaining budget."""
    unique = {digest: measurement for digest, _, measurement in items}
    mandatory = {digest: set() for digest in unique}
    if reference_hash in unique:
        for start, end in intervals:
            if not 0 <= start < end <= unique[reference_hash].duration_frames:
                raise engine.RetrievalFailure(
                    "cache_invalid", "Reference sample interval is invalid."
                )
            mandatory[reference_hash].update({start, end - 1})
    desired = {
        digest: sorted({s.frame for s in measurement.samples} | mandatory[digest])
        for digest, measurement in unique.items()
    }
    counts = {
        digest: max(len(mandatory[digest]), min(2, len(values)))
        for digest, values in desired.items()
    }
    if sum(counts.values()) > budget:
        raise engine.RetrievalFailure(
            "sample_limit", "Visual coverage exceeds the supported budget."
        )
    # Each clip receives another sample before any clip receives a second extra sample.
    while sum(counts.values()) < budget:
        available = [d for d in sorted(desired) if counts[d] < len(desired[d])]
        if not available:
            break
        for digest in available[: budget - sum(counts.values())]:
            counts[digest] += 1
    requests = {}
    for digest, values in desired.items():
        count = counts[digest]
        required = mandatory[digest]
        if required:
            extra = (
                spread([v for v in values if v not in required], count - len(required))
                if count > len(required)
                else []
            )
            requests[digest] = sorted(required | set(extra))
        elif count < len(values):
            # Distributed pairs give short candidate ranges usable visual evidence.
            anchors = spread(values, max(1, count // 2))
            pairs = {
                f for a in anchors for f in (a, min(a + 1, unique[digest].duration_frames - 1))
            }
            extras = (
                spread([v for v in values if v not in pairs], count - len(pairs))
                if count > len(pairs)
                else []
            )
            requests[digest] = sorted(pairs | set(extras))
        else:
            requests[digest] = values
    return requests


def refine_requests(requests, ranges):
    remaining = MAX_SAMPLES - sum(map(len, requests.values()))
    extra = {digest: set() for digest in requests}
    for digest, start, end in ranges:
        if digest in extra and start < end:
            extra[digest].update({start, (start + end - 1) // 2, end - 1})
    pending = {d: sorted(values - set(requests[d])) for d, values in extra.items()}
    refined = {d: set(values) for d, values in requests.items()}
    while remaining and any(pending.values()):
        for digest in sorted(pending):
            if pending[digest] and remaining:
                refined[digest].add(pending[digest].pop(0))
                remaining -= 1
    return {d: sorted(values) for d, values in refined.items()}


def select_expression(frames):
    # FFmpeg's expression parser has a nesting limit; balance large selections.
    if len(frames) == 1:
        return f"eq(n,{frames[0]})"
    middle = len(frames) // 2
    return f"({select_expression(frames[:middle])}+{select_expression(frames[middle:])})"


def analyze(project_id, items, directory, stop, deadline, progress, frame_requests=None):
    """Decode only requested uncached frames; deduplicate sources and overlap across passes."""
    verify_model()
    color.guard(stop, deadline)
    items = list(
        {digest: (digest, path, measurement) for digest, path, measurement in items}.values()
    )
    requests = frame_requests if frame_requests is not None else sample_requests(items)
    if set(requests) != {d for d, _, _ in items} or sum(map(len, requests.values())) > MAX_SAMPLES:
        raise engine.RetrievalFailure(
            "sample_limit", "Visual coverage exceeds the supported budget."
        )
    analyses, pending, previous = {}, [], {}
    for digest, path, measurement in items:
        frames = requests[digest]
        if (
            not frames
            or frames != sorted(set(frames))
            or frames[0] < 0
            or frames[-1] >= measurement.duration_frames
        ):
            raise engine.RetrievalFailure(
                "cache_invalid", "Requested sample timestamps are invalid."
            )
        with projects.database() as db:
            row = db.execute(
                "SELECT analysis FROM visual_cache WHERE project_id=? "
                "AND source_hash=? AND algorithm=?",
                (project_id, digest, ALGORITHM),
            ).fetchone()
        cached = validate(row[0], digest) if row else None
        previous[digest] = {s.frame: s for s in cached.samples} if cached else {}
        missing = [f for f in frames if f not in previous[digest]]
        if not missing:
            analyses[digest] = Analysis(
                source_hash=digest,
                revision=REVISION,
                algorithm=ALGORITHM,
                samples=[previous[digest][f] for f in frames],
            )
            progress(len(analyses))
            continue
        ffmpeg, _, video, duration, _ = color.inspect_video(
            path, directory, deadline, temp_budget=TEMP_BUDGET
        )
        width, height = framing.display_dimensions(video)
        ratio = 224 / min(width, height)
        scaled = (math.ceil(width * ratio), math.ceil(height * ratio))
        if max(scaled) > 8192:
            raise engine.RetrievalFailure("dimension_limit", "Visual resize exceeds 8,192 pixels.")
        result = engine.run_command(
            [
                ffmpeg,
                "-v",
                "error",
                "-nostdin",
                "-xerror",
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
                "setpts=PTS-STARTPTS,fps=30:start_time=0:round=near:eof_action=round,"
                + "select='"
                + select_expression(missing)
                + "',"
                f"scale={scaled[0]}:{scaled[1]}:flags=bicubic,"
                "crop=224:224,setsar=1,format=rgb24",
                "-frames:v",
                str(len(missing)),
                "-fps_mode",
                "passthrough",
                "-f",
                "rawvideo",
                "-",
            ],
            directory,
            "visual-decode",
            deadline,
            60,
            output_limit=len(missing) * FRAME_BYTES,
            temp_budget=TEMP_BUDGET,
        )
        if result.returncode or len(result.stdout) != len(missing) * FRAME_BYTES:
            raise engine.RetrievalFailure(
                "decode_failed", "Requested visual frames could not be completely decoded."
            )
        raw = directory / f"{digest}.rgb"
        raw.write_bytes(result.stdout)
        pending.append({"source_hash": digest, "path": str(raw), "frames": missing})
    if pending:
        request = directory / "visual-request.json"
        request.write_text(json.dumps(pending), encoding="utf-8")
        result = engine.run_command(
            [sys.executable, str(Path(__file__).resolve()), "infer", str(request)],
            directory,
            "visual-inference",
            deadline,
            120,
            output_limit=JSON_LIMIT,
            temp_budget=TEMP_BUDGET,
        )
        if result.returncode:
            raise engine.RetrievalFailure(
                "inference_failed",
                "Local visual inference failed or exceeded its 1 GiB memory budget. "
                "Previous proposal retained; measurements remain available.",
            )
        try:
            output = json.loads(result.stdout)
            if len(output["analyses"]) != len(pending):
                raise ValueError
            for request_item, record in zip(pending, output["analyses"]):
                analysis = validate(record, request_item["source_hash"], request_item["frames"])
                digest = analysis.source_hash
                combined = previous[digest] | {s.frame: s for s in analysis.samples}
                frames = requests[digest]
                analyses[digest] = analysis.model_copy(
                    update={"samples": [combined[f] for f in frames]}
                )
                retained = set(frames) | set(
                    spread(
                        sorted(set(combined) - set(frames)),
                        min(512 - len(frames), len(set(combined) - set(frames))),
                    )
                )
                analysis = analysis.model_copy(
                    update={"samples": [combined[f] for f in sorted(retained)]}
                )
                color.guard(stop, deadline)
                serialized = analysis.model_dump_json()
                with projects.database() as db:
                    projects.row_project(db, project_id)
                    used = db.execute(
                        "SELECT COALESCE(SUM(length(CAST(analysis AS BLOB))),0) "
                        "FROM visual_cache WHERE project_id=? AND source_hash!=?",
                        (project_id, analysis.source_hash),
                    ).fetchone()[0]
                    if used + len(serialized.encode()) > CACHE_BUDGET:
                        raise engine.RetrievalFailure(
                            "cache_limit", "Visual cache exceeded 32 MiB."
                        )
                    db.execute(
                        "INSERT OR REPLACE INTO visual_cache VALUES(?,?,?,?)",
                        (project_id, analysis.source_hash, ALGORITHM, serialized),
                    )
                progress(len(analyses))
        except (ValueError, TypeError, KeyError):
            raise engine.RetrievalFailure(
                "inference_invalid", "Local visual inference returned invalid data."
            ) from None
        finally:
            for item in pending:
                Path(item["path"]).unlink(missing_ok=True)
            request.unlink(missing_ok=True)
    return analyses


def range_samples(analysis, start, end):
    values = [s for s in analysis.samples if start <= s.frame < end]
    # ponytail: three representative grid samples; finer temporal matching needs a new version.
    return (
        [values[i] for i in sorted({0, len(values) // 2, len(values) - 1})]
        if len(values) >= 2
        else []
    )


def similarity(a, b):
    pairs = []
    for sa in a:
        for sb in b:
            norm = math.sqrt(sum(v * v for v in sa.embedding) * sum(v * v for v in sb.embedding))
            pairs.append(sum(x * y for x, y in zip(sa.embedding, sb.embedding)) / norm)
    # Floating-point unit vectors can otherwise produce 1.0000000000000004.
    return max(-1.0, min(1.0, sum(pairs) / len(pairs)))


def infer(request):
    import psutil

    peak = [0]

    def monitor():
        while True:
            peak[0] = max(peak[0], psutil.Process().memory_info().rss)
            if peak[0] > 1024**3:
                os._exit(3)
            time.sleep(0.1)

    threading.Thread(target=monitor, daemon=True).start()
    os.environ.update(OMP_NUM_THREADS="2", OPENBLAS_NUM_THREADS="1")
    import numpy as np
    import onnxruntime as ort
    from PIL import Image

    verify_model()
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    options.log_severity_level = 3
    model = ort.InferenceSession(
        str(MODEL), sess_options=options, providers=["CPUExecutionProvider"]
    )
    tasks = json.loads(Path(request).read_text(encoding="utf-8"))
    if sum(len(t["frames"]) for t in tasks) > MAX_SAMPLES:
        raise ValueError("Sample limit")
    output = []
    mean = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
    std = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)
    for task in tasks:
        raw = np.memmap(task["path"], mode="r", dtype=np.uint8)
        if raw.size != len(task["frames"]) * FRAME_BYTES:
            raise ValueError("Incomplete frames")
        samples = []
        for index, frame in enumerate(task["frames"]):
            rgb = raw[index * FRAME_BYTES : (index + 1) * FRAME_BYTES].reshape(224, 224, 3)
            pixels = ((rgb.astype(np.float32) / 255 - mean) / std).transpose(2, 0, 1)[None]
            vector = model.run(["image_embeds"], {"pixel_values": pixels})[0][0].astype(np.float64)
            vector /= np.linalg.norm(vector)
            image = Image.fromarray(rgb).resize((128, 128), Image.Resampling.BICUBIC)
            encoded = io.BytesIO()
            image.save(encoded, format="JPEG", quality=65)
            samples.append(
                {
                    "frame": frame,
                    "embedding": vector.tolist(),
                    "image": base64.b64encode(encoded.getvalue()).decode("ascii"),
                }
            )
        output.append(
            {
                "source_hash": task["source_hash"],
                "revision": REVISION,
                "algorithm": ALGORITHM,
                "samples": samples,
            }
        )
        del raw
    print(
        json.dumps(
            {"analyses": output, "peak_rss": peak[0], "runtime": version("onnxruntime")},
            allow_nan=False,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["setup", "ready", "infer"])
    parser.add_argument("request", nargs="?")
    args = parser.parse_args()
    if args.command == "setup":
        setup()
    elif args.command == "ready":
        print(json.dumps(readiness()))
    else:
        infer(args.request)
