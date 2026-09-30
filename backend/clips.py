"""Temporary inspection of one user-owned MP4 or MOV clip."""

import asyncio
import json
import logging
import math
import re
import uuid
from contextlib import asynccontextmanager
from fractions import Fraction
from pathlib import Path
from tempfile import SpooledTemporaryFile
from typing import Literal

from fastapi import Request
from pydantic import BaseModel
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from references import ReferenceError

logger = logging.getLogger(__name__)
DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "clip-inspection"
MAX_FILE = 100 * 1024 * 1024
MAX_REQUEST = 101 * 1024 * 1024
MAX_PROBE_OUTPUT = 64 * 1024
REQUEST_SECONDS = 30
PROBE_SECONDS = 15
inspection_lock = asyncio.Lock()


class ClipMultipartParser(MultiPartParser):
    """Keep Starlette spools scoped and detect incomplete multipart bodies."""

    complete = False

    def on_headers_finished(self) -> None:
        super().on_headers_finished()
        if self._current_part.file is not None:
            spool = SpooledTemporaryFile(max_size=self.spool_max_size, dir=DATA_DIR)
            self._current_part.file.file.close()
            self._current_part.file.file = spool
            self._files_to_close_on_error[-1] = spool

    def on_end(self) -> None:
        self.complete = True


class ClipDetails(BaseModel):
    filename: str
    size_bytes: int
    duration_seconds: float
    width: int
    height: int
    video_codec: str
    has_audio: bool
    audio_codec: str | None
    frame_rate: float | None
    validation_status: Literal["accepted"] = "accepted"
    storage_status: Literal["not_retained"] = "not_retained"


def clip_error(status: int, code: str, message: str) -> ReferenceError:
    return ReferenceError(status, code, message)


def display_name(raw: str | None) -> tuple[str, str]:
    basename = (raw or "").replace("\\", "/").split("/")[-1]
    extension = Path(basename).suffix.lower()
    if extension not in {".mp4", ".mov"}:
        raise clip_error(415, "unsupported_media", "Choose an MP4 or MOV video file.")
    safe = re.sub(r"[^\w. -]", "_", basename, flags=re.UNICODE)[-120:].strip(" .")
    if not safe or safe in {"mp4", "mov"}:
        raise clip_error(415, "unsupported_media", "Choose an MP4 or MOV video file.")
    return safe, extension


def validate_type(file: UploadFile) -> tuple[str, str]:
    filename, extension = display_name(file.filename)
    expected = "video/mp4" if extension == ".mp4" else "video/quicktime"
    if file.content_type not in {expected, "application/octet-stream"}:
        raise clip_error(
            415, "unsupported_media", "The file type does not match an MP4 or MOV video."
        )
    return filename, extension


def _positive_number(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise clip_error(422, "invalid_metadata", "The video metadata is invalid.") from None
    if not math.isfinite(number) or number <= 0:
        raise clip_error(422, "invalid_metadata", "The video metadata is invalid.")
    return number


def parse_probe(raw: bytes, extension: str, filename: str, size: int) -> ClipDetails:
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise clip_error(422, "invalid_metadata", "The video metadata is invalid.") from None
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("format"), dict)
        or not isinstance(data.get("streams"), list)
    ):
        raise clip_error(422, "invalid_metadata", "The video metadata is invalid.")
    container = data["format"]
    if "mov" not in str(container.get("format_name", "")).split(","):
        raise clip_error(415, "unsupported_media", "The file is not an MP4 or MOV video.")
    brand = (
        container.get("tags", {}).get("major_brand")
        if isinstance(container.get("tags"), dict)
        else None
    )
    mp4_brands = {"isom", "iso2", "iso4", "iso5", "iso6", "mp41", "mp42", "avc1", "M4V "}
    if (
        not isinstance(brand, str)
        or (extension == ".mov" and brand != "qt  ")
        or (extension == ".mp4" and brand not in mp4_brands)
    ):
        raise clip_error(
            415, "unsupported_media", "The file container does not match its extension."
        )
    duration = _positive_number(container.get("duration"))
    if duration > 120:
        raise clip_error(422, "duration_limit", "The clip must be 120 seconds or shorter.")
    streams = [stream for stream in data["streams"] if isinstance(stream, dict)]
    videos = [
        stream
        for stream in streams
        if stream.get("codec_type") == "video"
        and isinstance(stream.get("disposition", {}), dict)
        and not stream.get("disposition", {}).get("attached_pic")
    ]
    if not videos:
        raise clip_error(415, "unsupported_media", "The file must contain a video stream.")
    for stream in videos:
        width, height = stream.get("width"), stream.get("height")
        if (
            type(width) is not int
            or type(height) is not int
            or width <= 0
            or height <= 0
            or width > 4096
            or height > 4096
        ):
            raise clip_error(
                422, "dimension_limit", "Video dimensions must be between 1 and 4096 pixels."
            )
    video = videos[0]
    width, height = video["width"], video["height"]
    codec = video.get("codec_name")
    if not isinstance(codec, str) or not re.fullmatch(r"[a-zA-Z0-9_]{1,40}", codec):
        raise clip_error(422, "invalid_metadata", "The video metadata is invalid.")
    audios = [stream for stream in streams if stream.get("codec_type") == "audio"]
    audio_codec = audios[0].get("codec_name") if audios else None
    if audios and (
        not isinstance(audio_codec, str) or not re.fullmatch(r"[a-zA-Z0-9_]{1,40}", audio_codec)
    ):
        raise clip_error(422, "invalid_metadata", "The video metadata is invalid.")
    rate = video.get("avg_frame_rate")
    frame_rate = None
    if isinstance(rate, str) and len(rate) < 30:
        try:
            fraction = Fraction(rate)
            if fraction > 0 and math.isfinite(float(fraction)):
                frame_rate = float(fraction)
        except (ValueError, ZeroDivisionError, OverflowError):
            pass
    return ClipDetails(
        filename=filename,
        size_bytes=size,
        duration_seconds=duration,
        width=width,
        height=height,
        video_codec=codec,
        has_audio=bool(audios),
        audio_codec=audio_codec,
        frame_rate=frame_rate,
    )


async def probe_file(path: Path, extension: str, filename: str, size: int) -> ClipDetails:
    args = [
        "ffprobe",
        "-v",
        "error",
        "-protocol_whitelist",
        "file",
        "-print_format",
        "json",
        "-show_entries",
        "format=format_name,duration:format_tags=major_brand:stream=codec_type,codec_name,width,height,avg_frame_rate:stream_disposition=attached_pic",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        process = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
    except OSError:
        raise clip_error(
            503, "inspector_unavailable", "Video inspection is unavailable on this server."
        ) from None
    try:
        async with asyncio.timeout(PROBE_SECONDS):
            output = bytearray()
            while chunk := await process.stdout.read(4096):
                output.extend(chunk)
                if len(output) > MAX_PROBE_OUTPUT:
                    raise clip_error(422, "invalid_metadata", "The video metadata is too large.")
            if await process.wait() != 0:
                raise clip_error(
                    415, "unsupported_media", "The file is not a readable MP4 or MOV video."
                )
    except TimeoutError:
        raise clip_error(
            504, "inspection_timeout", "Video inspection took too long. Please retry."
        ) from None
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()
    return parse_probe(bytes(output), extension, filename, size)


@asynccontextmanager
async def limited_form(request: Request):
    received = 0
    original_receive = request._receive

    async def receive():
        nonlocal received
        message = await original_receive()
        if message["type"] == "http.request":
            received += len(message.get("body", b""))
            if received > MAX_REQUEST:
                raise clip_error(413, "request_too_large", "The upload request exceeds 101 MiB.")
        return message

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    parser = ClipMultipartParser(
        request.headers, request.stream(), max_files=1, max_fields=0, max_part_size=1024
    )
    request._receive = receive
    try:
        try:
            form = await parser.parse()
            if not parser.complete:
                raise MultiPartException("Incomplete multipart body")
        except MultiPartException:
            raise clip_error(
                422, "invalid_multipart", "Send exactly one video file in the file field."
            ) from None
        yield form
    finally:
        # Starlette keeps every opened spool here, including unfinished parts.
        cleanup_error = None
        for spool in parser._files_to_close_on_error:
            try:
                spool.close()
            except OSError as exc:
                cleanup_error = exc
                logger.exception("Clip multipart spool cleanup failed")
        request._receive = original_receive
        if cleanup_error is not None:
            raise clip_error(
                500, "cleanup_failure", "The temporary upload could not be deleted."
            ) from cleanup_error


async def inspect_clip(request: Request) -> ClipDetails:
    if inspection_lock.locked():
        raise clip_error(
            503, "inspection_busy", "A clip is being inspected. Please try again shortly."
        )
    await inspection_lock.acquire()
    path: Path | None = None
    try:
        async with asyncio.timeout(REQUEST_SECONDS):
            length = request.headers.get("content-length")
            if length and length.isdecimal() and int(length) > MAX_REQUEST:
                raise clip_error(413, "request_too_large", "The upload request exceeds 101 MiB.")
            if (
                not request.headers.get("content-type", "")
                .lower()
                .startswith("multipart/form-data;")
            ):
                raise clip_error(
                    422, "invalid_multipart", "Send exactly one video file in the file field."
                )
            async with limited_form(request) as form:
                if len(form) != 1 or not isinstance(form.get("file"), UploadFile):
                    raise clip_error(
                        422, "invalid_multipart", "Send exactly one video file in the file field."
                    )
                file = form["file"]
                filename, extension = validate_type(file)
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                path = DATA_DIR / f"clip-{uuid.uuid4().hex}{extension}"
                size = 0
                with path.open("xb") as target:
                    while chunk := await file.read(1024 * 1024):
                        size += len(chunk)
                        if size > MAX_FILE:
                            raise clip_error(
                                413, "file_too_large", "The clip must be 100 MiB or smaller."
                            )
                        await asyncio.to_thread(target.write, chunk)
                if size == 0:
                    raise clip_error(415, "unsupported_media", "The file is empty.")
                return await probe_file(path, extension, filename, size)
    except TimeoutError:
        raise clip_error(
            504, "inspection_timeout", "Upload or inspection took too long. Please retry."
        ) from None
    except OSError:
        logger.exception("Clip inspection storage failure")
        raise clip_error(500, "storage_failure", "The clip could not be handled safely.") from None
    finally:
        cleanup_error = None
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                cleanup_error = exc
                logger.exception("Clip inspection cleanup failed for %s", path)
        inspection_lock.release()
        if cleanup_error is not None:
            raise clip_error(
                500, "cleanup_failure", "The temporary clip could not be deleted."
            ) from cleanup_error


def cleanup_stale_files() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for path in DATA_DIR.glob("clip-*"):
        if path.is_file():
            try:
                path.unlink()
            except OSError:
                logger.exception("Could not remove stale clip inspection file %s", path)
