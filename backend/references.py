"""Validate TikTok references and inspect official oEmbed metadata."""

import asyncio
import json
import re
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel

SHORT_LINK_MESSAGE = "Open this link in your browser and paste the full TikTok video URL."
INVALID_LINK_MESSAGE = "Enter a full TikTok video URL."
UPSTREAM_MESSAGE = "Reference details could not be loaded. Please try again."
VIDEO_PATH = re.compile(r"/@([A-Za-z0-9._]{1,24})/video/([0-9]+)")
MAX_BODY = 256 * 1024


class ReferenceError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        self.status_code = status_code
        self.code = code
        self.message = message


class ReferenceRequest(BaseModel):
    url: str


class ReferenceDetails(BaseModel):
    provider: Literal["tiktok"] = "tiktok"
    video_id: str
    canonical_url: str
    title: str | None
    author_name: str | None
    metadata_status: Literal["available"] = "available"
    analysis_status: Literal["not_started"] = "not_started"


def canonicalize_url(raw: str) -> tuple[str, str]:
    value = raw.strip()
    if not value or len(value) > 2048 or any(ord(char) < 33 or char.isspace() for char in value):
        raise ReferenceError(422, "invalid_url", INVALID_LINK_MESSAGE)
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        raise ReferenceError(422, "invalid_url", INVALID_LINK_MESSAGE) from None
    if parsed.scheme != "https" or not host or "@" in parsed.netloc or port is not None:
        raise ReferenceError(422, "invalid_url", INVALID_LINK_MESSAGE)
    if host in {"vm.tiktok.com", "vt.tiktok.com"} or (
        host in {"tiktok.com", "www.tiktok.com", "m.tiktok.com"} and parsed.path.startswith("/t/")
    ):
        raise ReferenceError(422, "short_link_unsupported", SHORT_LINK_MESSAGE)
    if host not in {"tiktok.com", "www.tiktok.com", "m.tiktok.com"}:
        raise ReferenceError(422, "invalid_url", INVALID_LINK_MESSAGE)
    match = VIDEO_PATH.fullmatch(parsed.path)
    if not match:
        raise ReferenceError(422, "invalid_url", INVALID_LINK_MESSAGE)
    username, video_id = match.groups()
    return f"https://www.tiktok.com/@{username}/video/{video_id}", video_id


def _optional_text(data: dict, key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 500:
        raise ReferenceError(502, "upstream_invalid", UPSTREAM_MESSAGE)
    return value or None


async def inspect_reference(raw_url: str) -> ReferenceDetails:
    canonical_url, video_id = canonicalize_url(raw_url)
    timeout = httpx.Timeout(5.0, connect=3.0, read=5.0, write=3.0, pool=3.0)
    try:
        async with asyncio.timeout(10):
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                async with client.stream(
                    "GET", "https://www.tiktok.com/oembed", params={"url": canonical_url}
                ) as response:
                    if response.status_code in {404, 410}:
                        raise ReferenceError(
                            404, "reference_unavailable", "This reference is unavailable."
                        )
                    if response.status_code == 429:
                        raise ReferenceError(
                            503,
                            "upstream_rate_limited",
                            "TikTok is limiting requests. Please try again later.",
                        )
                    if response.status_code != 200:
                        raise ReferenceError(502, "upstream_failure", UPSTREAM_MESSAGE)
                    body = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        body.extend(chunk)
                        if len(body) > MAX_BODY:
                            raise ReferenceError(502, "upstream_oversized", UPSTREAM_MESSAGE)
    except TimeoutError:
        raise ReferenceError(
            504, "upstream_timeout", "TikTok took too long to respond. Please try again."
        ) from None
    except httpx.TimeoutException:
        raise ReferenceError(
            504, "upstream_timeout", "TikTok took too long to respond. Please try again."
        ) from None
    except httpx.RequestError:
        raise ReferenceError(502, "upstream_failure", UPSTREAM_MESSAGE) from None

    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise ReferenceError(502, "upstream_invalid", UPSTREAM_MESSAGE) from None
    if (
        not isinstance(data, dict)
        or data.get("version") != "1.0"
        or data.get("type") != "video"
        or data.get("provider_name") != "TikTok"
        or data.get("provider_url") != "https://www.tiktok.com"
    ):
        raise ReferenceError(502, "upstream_invalid", UPSTREAM_MESSAGE)
    return ReferenceDetails(
        video_id=video_id,
        canonical_url=canonical_url,
        title=_optional_text(data, "title"),
        author_name=_optional_text(data, "author_name"),
    )
