"""Explicit local reference OCR; no assets/network access during recognition."""

import argparse
import base64
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import shutil
import unicodedata
import urllib.request
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

import caption_preview
import captions
import color_analysis as color
import font_match as match
import frame_preview as preview
import projects
import reference_engine as engine
from references import ReferenceError

MANIFEST = json.loads(Path(__file__).with_name("ocr_assets.json").read_text(encoding="utf-8"))
METHOD = "tesseract-fast-block-v1"
LANGUAGES = {"english": "eng", "arabic": "ara", "combined": "eng+ara"}
DATA = Path(__file__).resolve().parent.parent / ".tools" / "ocr-data"
SECRET = secrets.token_bytes(32)  # Transient proposals expire on backend restart.
MAX_TEXT = 1024


class Request(caption_preview.ReferenceRequest):
    expected_source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_capability_token: str = Field(pattern=r"^[0-9a-f]{64}$")
    rectangle: match.Rectangle
    language: Literal["english", "arabic", "combined"]
    polarity: Literal["light", "dark"] = "light"


class Proposal(color.Schema):
    schema_version: Literal[1] = 1
    project_id: str
    source: color.Source
    reference_operation_id: str
    requested_timestamp_seconds: float = Field(ge=0, le=120.1, allow_inf_nan=False)
    timestamp_seconds: float = Field(ge=0, le=120.1, allow_inf_nan=False)
    rectangle: match.Rectangle
    language: Literal["english", "arabic", "combined"]
    polarity: Literal["light", "dark"]
    method: str
    capability_token: str = Field(pattern=r"^[0-9a-f]{64}$")
    engine_version: str = Field(max_length=256)
    assets_commit: str
    text: str = Field(min_length=1, max_length=MAX_TEXT)
    processing_width: int = Field(ge=8, le=960)
    processing_height: int = Field(ge=8, le=960)
    preprocessing_attempts: Literal[1] = 1
    token: str = Field(default="", max_length=64)


class Apply(Request):
    proposal: Proposal
    text: str = Field(min_length=1, max_length=80, strict=True)

    @model_validator(mode="after")
    def plain(self):
        captions.Cue(start=0.0, end=1.0, text=self.text)
        if sum(unicodedata.category(c)[0] in "LN" for c in self.text) < 4:
            raise ValueError("Use at least four visible letters/numbers for font matching")
        return self


def executable():
    configured = os.environ.get("REFRAME_OCR_TESSERACT")
    if configured:
        return Path(configured)
    local = DATA.parent / "ocr-engine" / "tesseract.exe"
    return local if local.is_file() else Path(shutil.which("tesseract") or "missing-tesseract")


def inspect_capability(directory, deadline):
    exe = executable()
    unavailable = {
        "available": False,
        "method": METHOD,
        "token": None,
        "engine_version": None,
        "assets_commit": MANIFEST["assets_commit"],
        "message": (
            "OCR unavailable. Install Tesseract 5.5.3 and run caption_ocr.py "
            "setup-data; manual text entry remains available."
        ),
    }
    if not exe.is_file():
        return unavailable
    try:
        digest = color.digest(exe, deadline=deadline, max_bytes=32 * 1024 * 1024)
        for name, expected in MANIFEST["files"].items():
            path = DATA / name
            if (
                path.stat().st_size != expected["size_bytes"]
                or color.digest(path, deadline=deadline, max_bytes=8 * 1024 * 1024)
                != expected["sha256"]
            ):
                return {
                    **unavailable,
                    "message": (
                        "Pinned English/Arabic OCR assets are missing or changed. Run "
                        "caption_ocr.py setup-data; manual entry remains available."
                    ),
                }
        raw = engine.run_command(
            [str(exe), "--version"],
            directory,
            "ocr-version",
            deadline,
            5,
            output_limit=8192,
            temp_budget=preview.TEMP_BUDGET,
        )
        line = raw.stdout.decode("utf-8").splitlines()[0]
        if raw.returncode or not re.fullmatch(r"tesseract v?5\.5\.3(?:\.\d+)?", line):
            return {
                **unavailable,
                "message": (
                    "OCR requires pinned Tesseract 5.5.3. Select its executable with "
                    "REFRAME_OCR_TESSERACT; manual entry remains available."
                ),
            }
    except ReferenceError as exc:
        if exc.code == "deadline":
            raise engine.ProbeTimeout() from None
        return unavailable
    except (OSError, UnicodeError, IndexError):
        return unavailable
    binding = {
        "method": METHOD,
        "engine_sha256": digest,
        "engine_version": line,
        "assets_commit": MANIFEST["assets_commit"],
        "assets": MANIFEST["files"],
    }
    return {
        "available": True,
        "method": METHOD,
        "token": match.fingerprint(binding),
        "engine_version": line,
        "assets_commit": MANIFEST["assets_commit"],
        "message": (
            "Local English/Arabic OCR ready. Review every result; no calibrated "
            "confidence is reported."
        ),
    }


def capability(project_id, _request=None):
    projects.identifier(project_id)
    projects.get_project(project_id)
    with preview.operation() as (directory, deadline):
        return inspect_capability(directory, deadline)


def require_capability(directory, deadline, token):
    current = inspect_capability(directory, deadline)
    if not current["available"]:
        raise ReferenceError(503, "ocr_unavailable", current["message"])
    if current["token"] != token:
        raise ReferenceError(
            409, "ocr_stale", "OCR engine/assets changed. Reload OCR status and retry."
        )
    return current


def source(project_id, request, deadline):
    value = color.source(project_id, deadline=deadline)
    if value["identity"].media_sha256 != request.expected_source_hash or value[
        "reference_operation_id"
    ] != str(request.expected_reference_operation_id):
        raise ReferenceError(
            409,
            "ocr_stale",
            "Reference media changed. Inspect its current frame before extracting.",
        )
    return value


def preprocess(frame, request, directory, deadline):
    width, height = frame.image.width, frame.image.height
    r = request.rectangle
    x, y = math.floor(r.x * width), math.floor(r.y * height)
    cw, ch = min(width - x, round(r.width * width)), min(height - y, round(r.height * height))
    if min(cw, ch) < 8:
        raise ReferenceError(
            422, "ocr_crop", "Select a caption region at least eight decoded pixels wide and high."
        )
    factor = min(2.0, 940 / max(cw, ch))
    ow, oh = max(8, round(cw * factor)), max(8, round(ch * factor))
    source_png = directory / "ocr-source.png"
    source_png.write_bytes(base64.b64decode(frame.image.png_base64))
    target = directory / "ocr-crop.pgm"
    # ponytail: one grayscale/polarity pass; complex effects need a reviewed crop,
    # not an exhaustive search or stronger claim of recognition.
    graph = f"crop={cw}:{ch}:{x}:{y},format=gray"
    if request.polarity == "light":
        graph += ",negate"
    graph += f",scale={ow}:{oh}:flags=bilinear,pad=iw+20:ih+20:10:10:white"
    preview.tool(
        [
            shutil.which("ffmpeg") or "ffmpeg",
            "-v",
            "error",
            "-nostdin",
            "-xerror",
            "-threads",
            "1",
            "-protocol_whitelist",
            "file",
            "-i",
            str(source_png),
            "-vf",
            graph,
            "-frames:v",
            "1",
            "-threads",
            "1",
            str(target),
        ],
        directory,
        "ocr-preprocess",
        deadline,
    )
    if not 0 < target.stat().st_size <= 1024 * 1024:
        raise ReferenceError(413, "ocr_crop_limit", "Prepared OCR crop exceeded its one-MiB limit.")
    return target, ow + 20, oh + 20


def recognize(path, request, directory, deadline):
    result = engine.run_command(
        [
            str(executable()),
            str(path),
            "stdout",
            "--tessdata-dir",
            str(DATA),
            "-l",
            LANGUAGES[request.language],
            "--oem",
            "1",
            "--psm",
            "6",
            "--dpi",
            "300",
        ],
        directory,
        "ocr-recognize",
        deadline,
        15,
        output_limit=16384,
        temp_budget=preview.TEMP_BUDGET,
    )
    if result.returncode:
        raise ReferenceError(
            409,
            "ocr_failed",
            "Local OCR failed. Adjust the crop/language and retry; raw tool logs are withheld.",
        )
    try:
        text = result.stdout.decode("utf-8").replace("\r\n", "\n").strip(" \n\f")
    except UnicodeError:
        raise ReferenceError(
            409, "ocr_output", "OCR returned invalid text. Retry or use manual entry."
        ) from None
    if len(text) > MAX_TEXT:
        raise ReferenceError(
            413,
            "ocr_text_limit",
            (
                "OCR returned more than 1,024 characters. Tighten the region; no "
                "partial text is returned."
            ),
        )
    if any(unicodedata.category(c) in {"Cc", "Cs"} and c != "\n" for c in text):
        raise ReferenceError(
            409, "ocr_output", "OCR returned unsupported control characters. Use manual entry."
        )
    if not any(unicodedata.category(c)[0] in "LN" for c in text):
        raise ReferenceError(
            422,
            "ocr_empty",
            (
                "No readable text was recognized. Tighten the caption crop, check "
                "light/dark polarity and language, or enter text manually."
            ),
        )
    return text


def signature(proposal):
    return hmac.new(
        SECRET,
        json.dumps(
            proposal.model_dump(mode="json", exclude={"token"}), sort_keys=True, ensure_ascii=False
        ).encode(),
        hashlib.sha256,
    ).hexdigest()


def generate(project_id, request):
    with preview.operation() as (directory, deadline):
        available = require_capability(directory, deadline, request.expected_capability_token)
        before = source(project_id, request, deadline)
        frame = caption_preview.reference_frame(project_id, request, stage=(directory, deadline))
        path, width, height = preprocess(frame, request, directory, deadline)
        text = recognize(path, request, directory, deadline)
        if source(project_id, request, deadline) != before:
            raise ReferenceError(409, "ocr_stale", "Reference changed before OCR publication.")
        require_capability(directory, deadline, request.expected_capability_token)
        proposal = Proposal(
            project_id=project_id,
            source=frame.source,
            reference_operation_id=str(frame.reference_operation_id),
            requested_timestamp_seconds=frame.requested_timestamp_seconds,
            timestamp_seconds=frame.timestamp_seconds,
            rectangle=request.rectangle,
            language=request.language,
            polarity=request.polarity,
            method=METHOD,
            capability_token=available["token"],
            engine_version=available["engine_version"],
            assets_commit=MANIFEST["assets_commit"],
            text=text,
            processing_width=width,
            processing_height=height,
        )
        proposal.token = signature(proposal)
        color.guard(None, deadline)
        return proposal


def apply(project_id, request):
    p = request.proposal
    if not hmac.compare_digest(p.token, signature(p)) or p.project_id != project_id:
        raise ReferenceError(
            409,
            "ocr_stale",
            "OCR proposal expired or changed. Extract again; manual text is retained.",
        )
    if (
        p.requested_timestamp_seconds,
        p.rectangle,
        p.language,
        p.polarity,
        p.capability_token,
        p.method,
    ) != (
        request.timestamp_seconds,
        request.rectangle,
        request.language,
        request.polarity,
        request.expected_capability_token,
        METHOD,
    ):
        raise ReferenceError(
            409,
            "ocr_stale",
            "Region, frame, language or method changed. Extract again before applying.",
        )
    with preview.operation() as (directory, deadline):
        require_capability(directory, deadline, request.expected_capability_token)
        current = source(project_id, request, deadline)
        if (
            current["identity"] != p.source
            or current["reference_operation_id"] != p.reference_operation_id
        ):
            raise ReferenceError(
                409, "ocr_stale", "Reference changed before application. Extract again."
            )
        return {"text": request.text}


def setup_data():
    DATA.mkdir(parents=True, exist_ok=True)
    for name, expected in MANIFEST["files"].items():
        path = DATA / name
        if (
            path.exists()
            and path.stat().st_size == expected["size_bytes"]
            and hashlib.sha256(path.read_bytes()).hexdigest() == expected["sha256"]
        ):
            continue
        temporary = DATA / (name + ".tmp")
        try:
            url = f"https://raw.githubusercontent.com/{MANIFEST['assets_repository']}/{MANIFEST['assets_commit']}/{name}"
            with urllib.request.urlopen(url, timeout=30) as response:
                raw = response.read(expected["size_bytes"] + 1)
            if (
                len(raw) != expected["size_bytes"]
                or hashlib.sha256(raw).hexdigest() != expected["sha256"]
            ):
                raise RuntimeError("OCR asset checksum/size mismatch")
            temporary.write_bytes(raw)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    print("Pinned English/Arabic OCR assets ready. No download occurs during recognition.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["setup-data", "status"])
    args = parser.parse_args()
    if args.command == "setup-data":
        setup_data()
    else:
        projects.DATA_DIR.mkdir(parents=True, exist_ok=True)
        with preview.operation() as (directory, deadline):
            print(json.dumps(inspect_capability(directory, deadline), ensure_ascii=False))
