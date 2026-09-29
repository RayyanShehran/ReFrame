"""Developer-only TikTok media feasibility probe; never expose this as an API."""

import argparse
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urljoin, urlsplit

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
MAX_BYTES = 50 * 1024 * 1024
TEMP_BUDGET = 60 * 1024 * 1024
TOTAL_SECONDS = 150
POLL_SECONDS = 0.1
MAX_CAPTURE = 1024 * 1024
FRAME_BYTES = 64 * 64 * 3
MAX_AUDIO_BYTES = 16000 * 2
CANONICAL_HOSTS = {"www.tiktok.com", "tiktok.com", "m.tiktok.com"}
SHORT_HOSTS = {"vm.tiktok.com", "vt.tiktok.com"}
VIDEO_PATH = re.compile(r"/@[A-Za-z0-9._-]+/video/[0-9]+/?\Z")
SHORT_PATH = re.compile(r"/[A-Za-z0-9]+/?\Z")
SHORT_WEB_PATH = re.compile(r"/t/[A-Za-z0-9]+/?\Z")


class ProbeTimeout(Exception):
    pass


class SizeLimit(Exception):
    pass


class ProcessCleanupError(Exception):
    pass


class ToolOutputError(Exception):
    pass


if os.name == "nt":
    import _winapi
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class _JobLimits(ctypes.Structure):
        class _Basic(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD),
            ]

        class _Io(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
            )]

        _fields_ = [
            ("BasicLimitInformation", _Basic), ("IoInfo", _Io),
            ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class _JobAccounting(ctypes.Structure):
        _fields_ = [(name, ctypes.c_longlong) for name in (
            "TotalUserTime", "TotalKernelTime", "ThisPeriodTotalUserTime", "ThisPeriodTotalKernelTime",
        )] + [(name, wintypes.DWORD) for name in (
            "TotalPageFaultCount", "TotalProcesses", "ActiveProcesses", "TotalTerminatedProcesses",
        )]

    _kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    for _name, _args, _result in [
        ("CreateJobObjectW", [ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
        ("SetInformationJobObject", [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
        ("AssignProcessToJobObject", [wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
        ("QueryInformationJobObject", [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p], wintypes.BOOL),
        ("TerminateJobObject", [wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
        ("ResumeThread", [wintypes.HANDLE], wintypes.DWORD),
    ]:
        _call = getattr(_kernel, _name)
        _call.argtypes, _call.restype = _args, _result

    class _WindowsOwnedProcess:
        def __init__(self, args: list[str], stdin, stdout, stderr):
            self.job = _kernel.CreateJobObjectW(None, None)
            if not self.job:
                raise ctypes.WinError(ctypes.get_last_error())
            self.handle = None
            self.returncode = None
            thread = None
            handles = []
            try:
                limits = _JobLimits()
                limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                if not _kernel.SetInformationJobObject(self.job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                    raise ctypes.WinError(ctypes.get_last_error())
                for stream in (stdin, stdout, stderr):
                    handles.append(_winapi.DuplicateHandle(
                        _winapi.GetCurrentProcess(), msvcrt.get_osfhandle(stream.fileno()),
                        _winapi.GetCurrentProcess(), 0, True, _winapi.DUPLICATE_SAME_ACCESS,
                    ))
                startup = subprocess.STARTUPINFO()
                startup.dwFlags |= _winapi.STARTF_USESTDHANDLES
                startup.hStdInput, startup.hStdOutput, startup.hStdError = handles
                startup.lpAttributeList = {"handle_list": handles}
                self.handle, thread, self.pid, _ = _winapi.CreateProcess(
                    None, subprocess.list2cmdline(args), None, None, True,
                    0x4, None, None, startup,  # CREATE_SUSPENDED: no child can spawn before assignment.
                )
                if not _kernel.AssignProcessToJobObject(self.job, self.handle):
                    raise ctypes.WinError(ctypes.get_last_error())
                if _kernel.ResumeThread(thread) == 0xFFFFFFFF:
                    raise ctypes.WinError(ctypes.get_last_error())
            except BaseException as original:
                stopped = True
                if self.handle is not None:
                    try:
                        _winapi.TerminateProcess(self.handle, 1)
                        stopped = _winapi.WaitForSingleObject(self.handle, 10000) == _winapi.WAIT_OBJECT_0
                    except OSError:
                        stopped = False
                try:
                    self.close()
                except OSError:
                    stopped = False
                if not stopped:
                    raise ProcessCleanupError from original
                raise
            finally:
                if thread is not None:
                    _winapi.CloseHandle(thread)
                for handle in handles:
                    _winapi.CloseHandle(handle)

        def poll(self):
            if self.returncode is None and _winapi.WaitForSingleObject(self.handle, 0) == _winapi.WAIT_OBJECT_0:
                self.returncode = _winapi.GetExitCodeProcess(self.handle)
            return self.returncode

        def wait(self, timeout=10):
            if _winapi.WaitForSingleObject(self.handle, int(timeout * 1000)) != _winapi.WAIT_OBJECT_0:
                raise ProcessCleanupError
            return self.poll()

        def active(self):
            state = _JobAccounting()
            if not _kernel.QueryInformationJobObject(self.job, 1, ctypes.byref(state), ctypes.sizeof(state), None):
                raise ctypes.WinError(ctypes.get_last_error())
            return state.ActiveProcesses

        def terminate(self):
            if self.active() and not _kernel.TerminateJobObject(self.job, 1):
                raise ctypes.WinError(ctypes.get_last_error())

        def close(self):
            if self.handle is not None:
                _winapi.CloseHandle(self.handle)
                self.handle = None
            if self.job is not None:
                _winapi.CloseHandle(self.job)
                self.job = None


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


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def remaining(deadline: float, stage_limit: float) -> float:
    left = min(stage_limit, deadline - time.monotonic())
    if left <= 0:
        raise ProbeTimeout
    return left


def resolve_short(url: str, deadline: float) -> str:
    opener = urllib.request.build_opener(NoRedirects)
    for _ in range(6):
        try:
            with opener.open(urllib.request.Request(url, method="HEAD"), timeout=remaining(deadline, 10)) as response:
                canonical, short = validate_url(response.url)
                if short:
                    raise ValueError("Short link did not resolve to a video")
                return canonical
        except urllib.error.HTTPError as exc:
            if exc.code not in {301, 302, 303, 307, 308} or not exc.headers.get("Location"):
                raise
            url, short = validate_url(urljoin(url, exc.headers["Location"]))
            if not short:
                return url
    raise ValueError("Too many short-link redirects")


def ytdlp_base() -> list[str]:
    return [
        sys.executable, "-m", "yt_dlp", "--no-config", "--no-plugin-dirs",
        "--no-remote-components", "--no-playlist",
        "--max-filesize", "50M", "--socket-timeout", "10", "--retries", "1",
        "--fragment-retries", "1", "--extractor-retries", "1",
        "--file-access-retries", "1", "--no-cache-dir", "--no-geo-bypass",
        "--quiet", "--no-warnings",
    ]


def directory_size(root: Path) -> int:
    total = 0
    for folder, _, files in os.walk(root, followlinks=False):
        for name in files:
            try:
                entry = os.stat(Path(folder) / name, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if stat.S_ISREG(entry.st_mode):
                total += entry.st_size
    return total


def terminate_tree(process: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            process.terminate()
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass  # The group may contain only its exited leader.
        process.wait(timeout=10)
        expires = time.monotonic() + 10
        while True:
            if os.name == "nt":
                alive = process.active() != 0
            else:
                try:
                    os.killpg(process.pid, 0)
                    alive = True
                except ProcessLookupError:
                    alive = False
            if not alive:
                return
            if time.monotonic() >= expires:
                raise ProcessCleanupError
            time.sleep(POLL_SECONDS)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProcessCleanupError from exc


def parent_exited(process) -> bool:
    if os.name == "nt":
        return process.poll() is not None
    # WNOWAIT leaves the leader's PID reserved until its group is terminated.
    return os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None


def run_command(args: list[str], temp: Path, name: str, deadline: float, stage_limit: float) -> subprocess.CompletedProcess[bytes]:
    expires = time.monotonic() + remaining(deadline, stage_limit)
    stdout_path, stderr_path = temp / f"{name}.out", temp / f"{name}.err"
    with open(os.devnull, "rb") as stdin, stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        process = (_WindowsOwnedProcess(args, stdin, stdout, stderr) if os.name == "nt" else
                   subprocess.Popen(args, stdin=stdin, stdout=stdout, stderr=stderr, start_new_session=True))
        try:
            while not parent_exited(process):
                if directory_size(temp) > TEMP_BUDGET:
                    raise SizeLimit
                if time.monotonic() >= expires:
                    raise ProbeTimeout
                time.sleep(POLL_SECONDS)
            if directory_size(temp) > TEMP_BUDGET:
                raise SizeLimit
        except BaseException:
            try:
                terminate_tree(process)
            except ProcessCleanupError as exc:
                raise ProcessCleanupError from exc
            raise
        else:
            terminate_tree(process)
        finally:
            if os.name == "nt":
                try:
                    process.close()
                except OSError as exc:
                    raise ProcessCleanupError from exc
    if stdout_path.stat().st_size > MAX_CAPTURE:
        raise ToolOutputError
    output = stdout_path.read_bytes()
    with stderr_path.open("rb") as stderr:
        diagnostic = stderr.read(65536).decode("utf-8", errors="replace")
    for path in (stdout_path, stderr_path):
        expires = time.monotonic() + 2
        while True:
            try:
                path.unlink()
                break
            except PermissionError as exc:
                if time.monotonic() >= expires:
                    raise ProcessCleanupError from exc
                time.sleep(POLL_SECONDS)
    return subprocess.CompletedProcess(args, process.returncode, output, diagnostic)


def failure_detail(stderr: str) -> str:
    match = re.search(r"HTTP Error (403|404|429|5[0-9][0-9])", stderr)
    if match:
        return f"HTTP {match.group(1)}"
    if re.search(r"timed out|could not resolve|name or service not known|unable to connect", stderr, re.I):
        return "network or timeout error"
    return "extractor returned an error; raw output withheld"


def inspect_reference(url: str, temp: Path, deadline: float, result: dict, ffmpeg: str, ffprobe: str) -> None:
    base = ytdlp_base()
    metadata = run_command(base + ["--dump-single-json", "--skip-download", url], temp, "metadata", deadline, 30)
    if metadata.returncode:
        result.update(failure_category="metadata_failed", diagnostic=failure_detail(metadata.stderr))
        return
    info = json.loads(metadata.stdout)
    if not isinstance(info, dict) or info.get("_type") in {"playlist", "multi_video"}:
        result.update(failure_category="unsupported_input", diagnostic="Extractor did not return one video")
        return
    video_id = str(info.get("id", ""))
    if not video_id.isdecimal():
        result.update(failure_category="invalid_metadata", diagnostic="No numeric video identity")
        return
    result["metadata"] = {"id": video_id, "duration_seconds": info.get("duration")}
    if video_id != urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]:
        result.update(failure_category="identity_mismatch", diagnostic="Extracted video ID differs from requested URL")
        return
    output = str(temp / "reference.%(ext)s")
    download = run_command(base + ["--ffmpeg-location", str(Path(ffmpeg).parent), "-o", output, url], temp, "download", deadline, 90)
    if download.returncode:
        result.update(retrieval="failed", failure_category="download_failed", diagnostic=failure_detail(download.stderr))
        return
    files = [path for path in temp.iterdir() if path.is_file() and path.name.startswith("reference.") and not path.name.endswith((".part", ".ytdl"))]
    if len(files) != 1 or files[0].stat().st_size > MAX_BYTES:
        result.update(retrieval="failed", failure_category="size_or_output_limit", diagnostic="Expected one media file at most 50 MiB")
        return
    result["retrieval"] = "succeeded"
    media = files[0]
    probe = run_command([ffprobe, "-v", "error", "-show_entries", "format=duration:stream=codec_type,codec_name,width,height", "-of", "json", str(media)], temp, "probe", deadline, 15)
    if probe.returncode:
        result.update(failure_category="probe_failed", diagnostic="ffprobe could not read the file")
        return
    details = json.loads(probe.stdout)
    if not isinstance(details, dict) or not isinstance(details.get("format", {}), dict):
        raise ToolOutputError
    streams = details["streams"]
    if not isinstance(streams, list) or any(not isinstance(stream, dict) for stream in streams):
        raise ToolOutputError
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    result["media"] = {"duration_seconds": details.get("format", {}).get("duration"), "width": video.get("width") if video else None, "height": video.get("height") if video else None, "video_codec": video.get("codec_name") if video else None, "audio_codec": audio.get("codec_name") if audio else None, "decoded_frame_bytes": 0, "decoded_audio_samples": 0}
    if video is None:
        result.update(video_decode="failed", audio="present" if audio else "absent", failure_category="no_video_stream", diagnostic="No video stream; photo/slideshow or unsupported media")
        return
    common = [ffmpeg, "-hide_banner", "-v", "error", "-xerror", "-abort_on", "empty_output", "-nostdin", "-i", str(media)]
    frame = run_command(common + ["-map", "0:v:0", "-an", "-vf", "scale=64:64", "-pix_fmt", "rgb24", "-frames:v", "1", "-f", "rawvideo", "-"], temp, "frame", deadline, 15)
    if frame.returncode == 0 and len(frame.stdout) == FRAME_BYTES:
        result["video_decode"] = "passed"
        result["media"]["decoded_frame_bytes"] = len(frame.stdout)
    else:
        result["video_decode"] = "failed"
    if audio is None:
        result["audio"] = "absent"
    else:
        sample = run_command(common + ["-map", "0:a:0", "-vn", "-t", "1", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-f", "s16le", "-"], temp, "audio", deadline, 15)
        if sample.returncode == 0 and 0 < len(sample.stdout) <= MAX_AUDIO_BYTES and len(sample.stdout) % 2 == 0:
            result["audio"] = "decoded"
            result["media"]["decoded_audio_samples"] = len(sample.stdout) // 2
        else:
            result["audio"] = "decode_failed"
    if result["video_decode"] != "passed" or result["audio"] == "decode_failed":
        result.update(failure_category="decode_failed", diagnostic="FFmpeg produced no complete required output or reported an error")


def run_reference(raw_url: str) -> dict:
    url, short = validate_url(raw_url)
    deadline = time.monotonic() + TOTAL_SECONDS
    result = {"input": url, "canonical": None, "metadata": None, "retrieval": "not_attempted", "video_decode": "not_attempted", "audio": "not_checked", "media": None, "failure_category": None, "diagnostic": None}
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        result.update(failure_category="missing_tools", diagnostic="FFmpeg and ffprobe must be on PATH")
        return result
    try:
        if short:
            url = resolve_short(url, deadline)
        result["canonical"] = url
    except (ValueError, OSError, urllib.error.URLError) as exc:
        result.update(failure_category="short_link_resolution_failed", diagnostic="Redirect did not reach a supported video" if isinstance(exc, ValueError) else type(exc).__name__)
        return result
    except ProbeTimeout:
        result.update(failure_category="timeout", diagnostic="Overall time limit exceeded during redirect")
        return result
    DATA_DIR.mkdir(exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix="tiktok-", dir=DATA_DIR))
    process_stopped = True
    try:
        inspect_reference(url, temp, deadline, result, ffmpeg, ffprobe)
    except ProbeTimeout:
        result.update(failure_category="timeout", diagnostic="Overall or stage time limit exceeded")
    except SizeLimit:
        result.update(retrieval="failed", failure_category="temporary_size_limit", diagnostic="Temporary files exceeded 60 MiB")
    except ProcessCleanupError:
        process_stopped = False
        result.update(failure_category="cleanup_failed", diagnostic="Process tree could not be confirmed stopped; temporary directory retained")
    except KeyboardInterrupt:
        result.update(failure_category="interrupted", diagnostic="Probe interrupted; owned process tree stopped")
    except (json.JSONDecodeError, UnicodeError, OSError, ValueError, TypeError, KeyError, ToolOutputError):
        result.update(failure_category="invalid_tool_output", diagnostic="Tool output could not be inspected")
    finally:
        if process_stopped:
            expires = time.monotonic() + 2
            while True:
                try:
                    shutil.rmtree(temp)
                    break
                except OSError:
                    if time.monotonic() >= expires:
                        result.update(failure_category="cleanup_failed", diagnostic="Temporary directory could not be removed", temporary_directory=str(temp))
                        break
                    time.sleep(POLL_SECONDS)
        else:
            result["temporary_directory"] = str(temp)
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
    return 0 if result["video_decode"] == "passed" and result["audio"] in {"decoded", "absent"} and result["failure_category"] is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
