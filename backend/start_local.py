"""Start the existing local servers; no installation or optional asset downloads."""

import argparse
import importlib.util
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from contextlib import ExitStack
from pathlib import Path

import reference_engine as engine

ROOT = Path(__file__).resolve().parents[1]


def resolve_media_tools(root=ROOT):
    tools = ("ffmpeg", "ffprobe")
    if not all(shutil.which(tool) for tool in tools):
        local = root / ".tools/ffmpeg-bin"
        for directory in [local, *sorted(local.glob("*/bin"), reverse=True)]:
            suffix = ".exe" if os.name == "nt" else ""
            if all((directory / f"{tool}{suffix}").is_file() for tool in tools):
                os.environ["PATH"] = str(directory) + os.pathsep + os.environ.get("PATH", "")
                break
    for tool in tools:
        executable = shutil.which(tool)
        if not executable:
            raise RuntimeError(
                f"{tool} is missing. Add FFmpeg's bin directory to PATH, or extract a "
                "build containing both tools into .tools/ffmpeg-bin/<build>/bin."
            )
        subprocess.run([executable, "-version"], capture_output=True, timeout=5, check=True)
        print(f"{tool}: {executable}", flush=True)


def check(root=ROOT, production=False):
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError("Use Python 3.12: in backend, run uv sync --locked.")
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Install Node.js 22 LTS, including npm, and reopen PowerShell.")
    version = subprocess.run([node, "--version"], capture_output=True, timeout=5, check=True)
    if not version.stdout.startswith(b"v22."):
        raise RuntimeError("Use Node.js 22 LTS (the tested runtime).")
    resolve_media_tools(root)
    for module in ("uvicorn", "fastapi", "multipart", "fontTools", "yt_dlp"):
        if importlib.util.find_spec(module) is None:
            raise RuntimeError("Backend dependencies are missing: run uv sync --locked in backend.")
    next_cli = root / "frontend/node_modules/next/dist/bin/next"
    if not next_cli.is_file():
        raise RuntimeError("Frontend dependencies are missing: run npm ci in frontend.")
    if production and not (root / "frontend/.next/BUILD_ID").is_file():
        raise RuntimeError("Production build is missing: run npm run build in frontend.")
    for port in (3000, 8000):
        with socket.socket() as listener:
            if os.name == "nt":
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                listener.bind(("127.0.0.1", port))
            except OSError as exc:
                raise RuntimeError(
                    f"Port {port} is occupied. Stop its app yourself or use the manual "
                    "custom-port instructions; this launcher never stops other apps."
                ) from exc
    return [
        (
            root / "backend",
            [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8000"],
        ),
        (
            root / "frontend",
            [
                node,
                str(next_cli),
                "start" if production else "dev",
                "--hostname",
                "127.0.0.1",
                "--port",
                "3000",
            ],
        ),
    ]


def start(directory, command, stdin, stdout, stderr):
    previous = Path.cwd()
    try:
        os.chdir(directory)
        return (
            engine._WindowsOwnedProcess(command, stdin, stdout, stderr)
            if os.name == "nt"
            else subprocess.Popen(
                command, stdin=stdin, stdout=stdout, stderr=stderr, start_new_session=True
            )
        )
    finally:
        os.chdir(previous)


def stop(processes):
    failed = False
    for process in reversed(processes):
        try:
            engine.terminate_tree(process)
        except engine.ProcessCleanupError:
            failed = True
        finally:
            if os.name == "nt":
                try:
                    process.close()
                except OSError:
                    failed = True
    if failed:
        raise RuntimeError(
            "Owned process termination could not be confirmed. "
            "Keep project data; check server output before restarting."
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check prerequisites/ports only")
    parser.add_argument("--production", action="store_true", help="Use an existing frontend build")
    args = parser.parse_args()
    processes = []
    try:
        commands = check(production=args.production)
        print(
            "Prerequisites and ports ready. No dependencies or optional assets installed.",
            flush=True,
        )
        if args.check:
            return 0
        # Fixed local launcher configuration; custom ports use the documented manual commands.
        os.environ["NEXT_PUBLIC_API_BASE_URL"] = "http://127.0.0.1:8000"
        os.environ["REFRAME_ALLOWED_ORIGINS"] = "http://127.0.0.1:3000,http://localhost:3000"
        with ExitStack() as stack:
            stdin = stack.enter_context(open(os.devnull, "rb"))
            try:
                for directory, command in commands:
                    processes.append(start(directory, command, stdin, sys.stdout, sys.stderr))
                print(
                    "Open http://127.0.0.1:3000 after Next.js reports ready. "
                    "Keep this window open; Ctrl+C stops only these owned servers.",
                    flush=True,
                )
                while True:
                    for process in processes:
                        if engine.parent_exited(process):
                            raise RuntimeError(
                                "A server exited. Review its output above; "
                                "both owned servers will stop."
                            )
                    time.sleep(0.2)
            except KeyboardInterrupt:
                print("Stopping owned servers...", flush=True)
                # Windows console Ctrl+C also reaches the owned children. POSIX uses private groups.
                if os.name != "nt":
                    for process in processes:
                        try:
                            os.killpg(process.pid, signal.SIGINT)
                        except ProcessLookupError:
                            pass
                expires = time.monotonic() + 10
                while time.monotonic() < expires and any(
                    not engine.parent_exited(p) for p in processes
                ):
                    time.sleep(0.1)
            finally:
                stop(processes)
        print("Owned servers stopped. Saved project data is retained.", flush=True)
        return 0
    except (RuntimeError, OSError, subprocess.SubprocessError, engine.ProcessCleanupError) as exc:
        message = str(exc) or "Process containment failed; check owned server cleanup."
        print(f"ReFrame could not start/stop: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
