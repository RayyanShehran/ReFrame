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
MAX_SAMPLES = 512
FRAME_BYTES = 224 * 224 * 3
TEMP_BUDGET = 96 * 1024 * 1024
CACHE_BUDGET = 32 * 1024 * 1024
JSON_LIMIT = 24 * 1024 * 1024
SETUP = (
    "In backend: uv sync --locked --extra visual; "
    "uv run --locked --extra visual python visual_matching.py setup"
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
    samples: list[Sample] = color.Field(min_length=1, max_length=240)


def validate(data, digest, frames):
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
        if [s.frame for s in result.samples] != frames:
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


def analyze(project_id, items, directory, stop, deadline, progress):
    """Decode each unique source once; one owned model process handles all uncached frames."""
    verify_model()
    color.guard(stop, deadline)
    analyses, pending, total = {}, [], 0
    for digest, path, measurement in items:
        frames = [s.frame for s in measurement.samples]
        if (
            frames != list(range(0, len(frames) * 15, 15))
            or frames[-1] >= measurement.duration_frames
        ):
            raise engine.RetrievalFailure(
                "cache_invalid", "Measured sample timestamps are invalid."
            )
        total += len(frames)
        if total > MAX_SAMPLES:
            raise engine.RetrievalFailure(
                "sample_limit",
                "Subject-aware analysis supports at most 512 project samples (2 fps). "
                "Use shorter/fewer clips or measurements.",
            )
        with projects.database() as db:
            row = db.execute(
                "SELECT analysis FROM visual_cache WHERE project_id=? "
                "AND source_hash=? AND algorithm=?",
                (project_id, digest, ALGORITHM),
            ).fetchone()
        if row:
            analyses[digest] = validate(row[0], digest, frames)
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
                "setpts=PTS-STARTPTS,fps=2:start_time=0:round=near:eof_action=pass,"
                f"scale={scaled[0]}:{scaled[1]}:flags=bicubic,"
                "crop=224:224,setsar=1,format=rgb24",
                "-t",
                str(duration),
                "-f",
                "rawvideo",
                "-",
            ],
            directory,
            "visual-decode",
            deadline,
            60,
            output_limit=240 * FRAME_BYTES,
            temp_budget=TEMP_BUDGET,
        )
        if result.returncode or len(result.stdout) != len(frames) * FRAME_BYTES:
            raise engine.RetrievalFailure(
                "decode_failed", "Visual samples did not match the complete measured timeline."
            )
        raw = directory / f"{digest}.rgb"
        raw.write_bytes(result.stdout)
        pending.append({"source_hash": digest, "path": str(raw), "frames": frames})
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
                analyses[analysis.source_hash] = analysis
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
    return sum(sum(x * y for x, y in zip(sa.embedding, sb.embedding)) for sa in a for sb in b) / (
        len(a) * len(b)
    )


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
