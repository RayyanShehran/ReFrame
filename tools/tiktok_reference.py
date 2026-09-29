"""Developer-only TikTok media feasibility probe; never expose this as an API."""

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urljoin, urlsplit

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
MAX_BYTES = 50 * 1024 * 1024
MAX_SECONDS = 90
CANONICAL_HOSTS = {"www.tiktok.com", "tiktok.com", "m.tiktok.com"}
SHORT_HOSTS = {"vm.tiktok.com", "vt.tiktok.com"}
VIDEO_PATH = re.compile(r"/@[A-Za-z0-9._-]+/video/[0-9]+/?\Z")
SHORT_PATH = re.compile(r"/[A-Za-z0-9]+/?\Z")
SHORT_WEB_PATH = re.compile(r"/t/[A-Za-z0-9]+/?\Z")


def validate_url(raw: str) -> tuple[str, bool]:
    if raw != raw.strip() or any(ord(char) < 32 for char in raw):
        raise ValueError("URL contains whitespace or control characters")
    try:
        parsed = urlsplit(raw)
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Malformed URL") from exc
    if parsed.scheme != "https" or host not in CANONICAL_HOSTS | SHORT_HOSTS:
        raise ValueError("Expected an HTTPS TikTok video or short link")
    if parsed.username is not None or parsed.password is not None or port is not None:
        raise ValueError("Credentials and custom ports are not allowed")
    short = host in SHORT_HOSTS or (host in {"www.tiktok.com", "tiktok.com"} and SHORT_WEB_PATH.fullmatch(parsed.path) is not None)
    pattern = SHORT_PATH if host in SHORT_HOSTS else SHORT_WEB_PATH if short else VIDEO_PATH
    if not pattern.fullmatch(parsed.path):
        raise ValueError("Only a single TikTok video is supported")
    return f"https://{host}{parsed.path}", short


class TikTokRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        target = urljoin(request.full_url, newurl)
        validate_url(target)
        return super().redirect_request(request, fp, code, msg, headers, target)


def resolve_short(url: str) -> str:
    opener = urllib.request.build_opener(TikTokRedirects)
    request = urllib.request.Request(url, method="HEAD")
    with opener.open(request, timeout=10) as response:
        canonical, short = validate_url(response.url)
    if short:
        raise ValueError("Short link did not resolve to a video")
    return canonical


def ytdlp_base() -> list[str]:
    return [
        sys.executable, "-m", "yt_dlp", "--no-config", "--no-plugin-dirs",
        "--no-remote-components", "--no-playlist",
        "--max-filesize", "50M", "--socket-timeout", "10", "--retries", "1",
        "--fragment-retries", "1", "--extractor-retries", "1",
        "--file-access-retries", "1", "--no-cache-dir", "--no-geo-bypass",
        "--quiet", "--no-warnings",
    ]


def invoke(args: list[str], timeout: int = MAX_SECONDS) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)


def failure_detail(stderr: str) -> str:
    match = re.search(r"HTTP Error (403|404|429|5[0-9][0-9])", stderr)
    if match:
        return f"HTTP {match.group(1)}"
    if re.search(r"timed out|could not resolve|name or service not known|unable to connect", stderr, re.I):
        return "network or timeout error"
    return "extractor returned an error; raw output withheld"


def run_reference(raw_url: str) -> dict:
    url, short = validate_url(raw_url)
    result = {
        "input": url, "canonical": None, "metadata": None,
        "retrieval": "not_attempted", "video_decode": "not_attempted",
        "audio": "not_checked", "media": None, "failure_category": None,
        "diagnostic": None,
    }
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        result.update(failure_category="missing_tools", diagnostic="FFmpeg and ffprobe must be on PATH")
        return result
    try:
        if short:
            url = resolve_short(url)
        result["canonical"] = url
    except (ValueError, OSError, urllib.error.URLError) as exc:
        result.update(failure_category="short_link_resolution_failed", diagnostic="Redirect did not reach a supported video" if isinstance(exc, ValueError) else type(exc).__name__)
        return result

    DATA_DIR.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tiktok-", dir=DATA_DIR) as temp:
        base = ytdlp_base()
        try:
            metadata = invoke(base + ["--dump-single-json", "--skip-download", url], 30)
            if metadata.returncode:
                result.update(failure_category="metadata_failed", diagnostic=failure_detail(metadata.stderr))
                return result
            info = json.loads(metadata.stdout)
            if not isinstance(info, dict) or info.get("_type") in {"playlist", "multi_video"}:
                result.update(failure_category="unsupported_input", diagnostic="Extractor did not return one video")
                return result
            video_id = str(info.get("id", ""))
            if not video_id.isdecimal():
                result.update(failure_category="invalid_metadata", diagnostic="No numeric video identity")
                return result
            result["metadata"] = {"id": video_id, "duration_seconds": info.get("duration")}
            output = str(Path(temp) / "reference.%(ext)s")
            download = invoke(base + ["--ffmpeg-location", str(Path(ffmpeg).parent), "-o", output, url])
            if download.returncode:
                result.update(retrieval="failed", failure_category="download_failed", diagnostic=failure_detail(download.stderr))
                return result
            files = [path for path in Path(temp).iterdir() if path.is_file() and path.name.startswith("reference.") and not path.name.endswith((".part", ".ytdl"))]
            if len(files) != 1 or files[0].stat().st_size > MAX_BYTES:
                result.update(retrieval="failed", failure_category="size_or_output_limit", diagnostic="Expected one media file at most 50 MiB")
                return result
            result["retrieval"] = "succeeded"
            media = files[0]
            probe = invoke([ffprobe, "-v", "error", "-show_entries", "format=duration:stream=codec_type,codec_name,width,height", "-of", "json", str(media)], 15)
            if probe.returncode:
                result.update(failure_category="probe_failed", diagnostic="ffprobe could not read the file")
                return result
            details = json.loads(probe.stdout)
            streams = details.get("streams", [])
            video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
            audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
            result["media"] = {"duration_seconds": details.get("format", {}).get("duration"), "width": video.get("width") if video else None, "height": video.get("height") if video else None, "video_codec": video.get("codec_name") if video else None, "audio_codec": audio.get("codec_name") if audio else None}
            if video is None:
                result.update(video_decode="failed", audio="present" if audio else "absent", failure_category="no_video_stream", diagnostic="No video stream; photo/slideshow or unsupported media")
                return result
            frame = invoke([ffmpeg, "-v", "error", "-nostdin", "-i", str(media), "-map", "0:v:0", "-frames:v", "1", "-f", "null", "-"], 15)
            result["video_decode"] = "passed" if frame.returncode == 0 else "failed"
            if audio is None:
                result["audio"] = "absent"
            else:
                sample = invoke([ffmpeg, "-v", "error", "-nostdin", "-i", str(media), "-map", "0:a:0", "-t", "1", "-f", "null", "-"], 15)
                result["audio"] = "decoded" if sample.returncode == 0 else "decode_failed"
            if result["video_decode"] != "passed" or result["audio"] == "decode_failed":
                result.update(failure_category="decode_failed", diagnostic="FFmpeg could not decode a required stream")
            return result
        except subprocess.TimeoutExpired:
            result.update(failure_category="timeout", diagnostic="Process exceeded its time limit")
            return result
        except (json.JSONDecodeError, OSError, ValueError):
            result.update(failure_category="invalid_tool_output", diagnostic="Tool output could not be inspected")
            return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="One public HTTPS TikTok video URL or vm/vt short link")
    args = parser.parse_args()
    try:
        result = run_reference(args.url)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))
    return 0 if result["video_decode"] == "passed" and result["audio"] in {"decoded", "absent"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
