"""Explicit model setup and local-only, subprocess-owned CPU transcription."""

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from importlib.metadata import version
from pathlib import Path

import captions
import reference_engine as engine

REPOSITORY = "Systran/faster-whisper-base"
REVISION = "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66"
MODEL = Path(__file__).resolve().parents[1] / "data" / "transcription-models" / REVISION
FILES = ["config.json", "model.bin", "tokenizer.json", "vocabulary.txt"]
THREADS = 2
PCM_LIMIT = 120 * 16000 * 2
JSON_LIMIT = 512 * 1024
TEMP_BUDGET = 8 * 1024 * 1024
ALGORITHM = "base-word-cues-v1"


@contextmanager
def owned_scope(stop):
    previous = getattr(engine._control, "stop", None)
    engine._control.stop = stop
    try:
        yield
    except engine.ProcessCleanupError:
        raise engine.RetrievalFailure(
            "cleanup_failure",
            "Owned transcription could not be confirmed stopped; staging retained.",
            False,
        ) from None
    except engine.ProbeInterrupted:
        raise engine.RetrievalFailure("interrupted", "Transcription was interrupted.") from None
    except engine.SizeLimit:
        raise engine.RetrievalFailure(
            "temporary_size_limit", "Transcription exceeded its 8 MiB polled staging budget."
        ) from None
    except engine.ToolOutputError:
        raise engine.RetrievalFailure(
            "invalid_tool_output", "Transcription tool output exceeded its supported bound."
        ) from None
    finally:
        engine._control.stop = previous


def invalid(message="Invalid model timestamps or text. Regenerate or author captions manually."):
    raise engine.RetrievalFailure("transcription_invalid", message)


def cues_from_words(data, duration):
    """Validate first; normalize <=20 ms overlaps, then split at 80 chars/5 s/punctuation."""
    import math

    if not isinstance(data, dict) or not isinstance(data.get("segments"), list):
        invalid()
    cues, previous, segment_end, count = [], 0.0, 0.0, 0
    for segment in data["segments"]:
        if not isinstance(segment, dict) or not isinstance(segment.get("words"), list):
            invalid()
        start, end, text = segment.get("start"), segment.get("end"), segment.get("text")
        if (
            not all(type(v) in {int, float} and math.isfinite(v) for v in [start, end])
            or not 0 <= start < end <= duration
            or not isinstance(text, str)
            or not text.strip()
        ):
            invalid()
        if start < segment_end - 0.0200001 or end <= segment_end:
            invalid("Speech segments overlap or are out of order.")
        segment_end = end
        pending, cue_start, cue_end = "", None, None
        if not segment["words"]:
            invalid("Speech text has no word timestamps; cannot safely create timed cues.")
        for word in segment["words"]:
            count += 1
            if count > 10000 or not isinstance(word, dict):
                invalid("Transcription exceeds the supported word limit.")
            a, b, token = word.get("start"), word.get("end"), word.get("word")
            if (
                not all(type(v) in {int, float} and math.isfinite(v) for v in [a, b])
                or not start <= a < b <= end
                or not isinstance(token, str)
                or not token.strip()
                or len(token.strip()) > 200
                or "\n" in token
            ):
                invalid()
            if a < previous:
                if previous - a > 0.0200001 or b <= previous:
                    invalid("Word timestamps overlap by more than 20 ms or are out of order.")
                a = previous
            previous = b
            separator = " " if pending and not token[0].isspace() else ""
            joined = (pending + separator + token).strip()
            if pending and (len(joined) > 80 or b - cue_start > 5):
                cues.append(captions.Cue(start=cue_start, end=cue_end, text=pending))
                pending, cue_start = "", None
            cue_start = a if cue_start is None else cue_start
            pending = (pending + (separator if pending else "") + token).strip()
            cue_end = b
            if pending.rstrip().endswith((".", "!", "?", "؟", "؛")):
                cues.append(captions.Cue(start=cue_start, end=b, text=pending.strip()))
                pending, cue_start = "", None
        if pending:
            cues.append(captions.Cue(start=cue_start, end=cue_end, text=pending.strip()))
        if len(cues) > 200:
            invalid(
                "Transcription exceeds 200 cues; shorten the output or author captions manually."
            )
    try:
        captions.Choices(cues=cues)
        captions.validate_duration(cues, duration)
    except ValueError:
        invalid()
    return cues


def model_ready():
    try:
        manifest = json.loads((MODEL / "reframe-model.json").read_text(encoding="utf-8"))
        return manifest["revision"] == REVISION and all(
            (MODEL / name).is_file()
            and (MODEL / name).stat().st_size == manifest["files"][name]["size"]
            for name in FILES
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False


def setup():
    from huggingface_hub import snapshot_download

    snapshot_download(REPOSITORY, revision=REVISION, local_dir=MODEL, allow_patterns=FILES)
    files = {}
    for name in FILES:
        path = MODEL / name
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        files[name] = {"size": path.stat().st_size, "sha256": digest}
    (MODEL / "reframe-model.json").write_text(
        json.dumps({"repository": REPOSITORY, "revision": REVISION, "files": files}),
        encoding="utf-8",
    )
    print("Pinned multilingual base model is ready in the ignored local cache.")


def infer(pcm, language):
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", OMP_NUM_THREADS=str(THREADS))
    if not model_ready():
        raise RuntimeError("Run explicit model setup first")
    import numpy as np
    from faster_whisper import WhisperModel

    raw = Path(pcm).read_bytes()
    if not raw or len(raw) > PCM_LIMIT or len(raw) % 2:
        raise ValueError("Invalid bounded PCM")
    model = WhisperModel(
        str(MODEL),
        device="cpu",
        compute_type="int8",
        cpu_threads=THREADS,
        num_workers=1,
        local_files_only=True,
    )
    segments, info = model.transcribe(
        np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768,
        task="transcribe",
        language=None if language == "auto" else language,
        word_timestamps=True,
        vad_filter=True,
        condition_on_previous_text=False,
        temperature=0,
        beam_size=5,
    )
    result = {
        "language": info.language,
        "versions": {n: version(n) for n in ["faster-whisper", "ctranslate2", "onnxruntime", "av"]},
        "segments": [],
    }
    for s in segments:
        result["segments"].append(
            {
                "start": s.start,
                "end": s.end,
                "text": s.text,
                "words": [{"start": w.start, "end": w.end, "word": w.word} for w in s.words or []],
            }
        )
        if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > JSON_LIMIT:
            raise ValueError("Model JSON exceeds its bound")
    print(json.dumps(result, ensure_ascii=True, allow_nan=False))


def run(path, duration, language, directory, stop, deadline):
    if not model_ready():
        raise engine.RetrievalFailure(
            "model_unavailable",
            "Set up local captions: in backend run "
            "uv sync --locked --extra transcription, then "
            "uv run --locked --extra transcription python transcribe_local.py setup.",
        )
    with owned_scope(stop):
        pcm = directory / "audio.pcm"
        result = engine.run_command(
            [
                shutil.which("ffmpeg") or "ffmpeg",
                "-v",
                "error",
                "-xerror",
                "-nostdin",
                "-protocol_whitelist",
                "file",
                "-i",
                str(path),
                "-map",
                "0:a:0",
                "-t",
                str(duration),
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                "-f",
                "s16le",
                str(pcm),
            ],
            directory,
            "transcription-audio",
            deadline,
            30,
            output_limit=8192,
            temp_budget=TEMP_BUDGET,
        )
        if result.returncode or not pcm.is_file() or not 0 < pcm.stat().st_size <= PCM_LIMIT:
            raise engine.RetrievalFailure(
                "decode_failed", "Output audio could not be decoded safely."
            )
        result = engine.run_command(
            [sys.executable, str(Path(__file__).resolve()), "infer", str(pcm), language],
            directory,
            "transcription-inference",
            deadline,
            300,
            output_limit=JSON_LIMIT,
            temp_budget=TEMP_BUDGET,
        )
        if result.returncode:
            raise engine.RetrievalFailure(
                "transcription_failed",
                "Local inference failed. Verify the "
                "optional transcription dependencies and pinned model setup.",
            )
        try:
            data = json.loads(result.stdout)
            cues = cues_from_words(data, duration)
            if not isinstance(data["language"], str) or not 0 < len(data["language"]) <= 10:
                invalid()
            if (
                not isinstance(data["versions"], dict)
                or not 0 < len(data["versions"]) <= 20
                or any(
                    not isinstance(k, str)
                    or not 0 < len(k) <= 64
                    or not isinstance(v, str)
                    or not 0 < len(v) <= 100
                    for k, v in data["versions"].items()
                )
            ):
                invalid()
        except (ValueError, KeyError, TypeError):
            invalid()
        return cues, data["language"], data["versions"]


def smoke(path, language):
    """Explicit real-inference check; ordinary CI never calls this command."""
    import math

    directory = Path(tempfile.mkdtemp(prefix="reframe-transcription-smoke-"))
    started = time.monotonic()
    cleanup_safe = True
    try:
        deadline = started + 300
        with owned_scope(threading.Event()):
            result = engine.run_command(
                [
                    shutil.which("ffprobe") or "ffprobe",
                    "-v",
                    "error",
                    "-protocol_whitelist",
                    "file",
                    "-show_streams",
                    "-show_format",
                    "-of",
                    "json",
                    str(Path(path).resolve()),
                ],
                directory,
                "transcription-smoke-probe",
                deadline,
                10,
                output_limit=65536,
                temp_budget=TEMP_BUDGET,
            )
            if result.returncode:
                invalid("Could not inspect the local smoke input.")
            data = json.loads(result.stdout)
            duration = float(data["format"]["duration"])
            if not math.isfinite(duration) or not 0 < duration <= 120:
                invalid("Smoke input must be at most 120 seconds.")
            if not any(s.get("codec_type") == "audio" for s in data["streams"]):
                invalid("Smoke input requires an audio stream.")
            cues, detected, versions = run(
                path, duration, language, directory, threading.Event(), deadline
            )
        print(
            json.dumps(
                {
                    "cues": [c.model_dump() for c in cues],
                    "language": detected,
                    "versions": versions,
                    "model_revision": REVISION,
                    "elapsed_seconds": time.monotonic() - started,
                },
                ensure_ascii=True,
            )
        )
    except engine.ProcessCleanupError:
        cleanup_safe = False
        raise
    except engine.RetrievalFailure as failure:
        cleanup_safe = failure.cleanup_safe
        raise
    finally:
        if cleanup_safe:
            try:
                shutil.rmtree(directory)
            except OSError as cause:
                raise engine.RetrievalFailure(
                    "cleanup_failed", f"Smoke staging retained: {directory}", cleanup_safe=False
                ) from cause
        else:
            print(f"Containment not confirmed; staging retained: {directory}", file=sys.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["setup", "infer", "smoke"])
    parser.add_argument("pcm", nargs="?")
    parser.add_argument("language", nargs="?", choices=["auto", "en", "ar"], default="auto")
    args = parser.parse_args()
    if args.command == "setup":
        setup()
    elif args.command == "infer":
        infer(args.pcm, args.language)
    else:
        if not args.pcm:
            parser.error("smoke requires a local media path")
        smoke(args.pcm, args.language)
