import os
import socket
import sys
import time
from pathlib import Path

import pytest

import start_local as launcher


def prerequisites(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher.shutil, "which", lambda name: name)
    monkeypatch.setattr(
        launcher.subprocess,
        "run",
        lambda *a, **k: launcher.subprocess.CompletedProcess(a, 0, b"v22.16.0"),
    )
    monkeypatch.setattr(launcher.importlib.util, "find_spec", lambda name: True)
    cli = tmp_path / "frontend/node_modules/next/dist/bin/next"
    cli.parent.mkdir(parents=True)
    cli.touch()


def test_missing_dependencies_and_occupied_port_are_actionable(tmp_path, monkeypatch):
    prerequisites(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError, match="Production build is missing"):
        launcher.check(tmp_path, production=True)
    monkeypatch.setattr(launcher.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="Node.js 22"):
        launcher.check(tmp_path)
    monkeypatch.setattr(launcher.shutil, "which", lambda name: name)
    with socket.socket() as listener:
        try:
            listener.bind(("127.0.0.1", 3000))
        except OSError:
            pass  # An existing listener is the same preflight condition.
        with pytest.raises(RuntimeError, match="Port 3000 is occupied"):
            launcher.check(tmp_path)


def test_owned_launch_uses_directory_and_stop_does_not_touch_other_process(tmp_path):
    marker = tmp_path / "started"
    code = (
        "from pathlib import Path; import time; "
        "Path('started').write_text(str(Path.cwd())); time.sleep(60)"
    )
    previous = os.getcwd()
    with open(os.devnull, "rb") as stdin, (tmp_path / "log").open("wb") as log:
        owned = launcher.start(tmp_path, [sys.executable, "-c", code], stdin, log, log)
        unrelated = launcher.start(
            tmp_path, [sys.executable, "-c", "import time; time.sleep(60)"], stdin, log, log
        )
        try:
            expires = time.monotonic() + 10
            while not marker.exists() and time.monotonic() < expires:
                time.sleep(0.05)
            assert marker.read_text() == str(tmp_path)
            assert os.getcwd() == previous
            launcher.stop([owned])
            owned = None
            assert not launcher.engine.parent_exited(unrelated)
        finally:
            # Stop every owned helper even after an assertion failure.
            if owned is not None:
                launcher.stop([owned])
            launcher.stop([unrelated])


def test_media_tools_fallback_is_complete_and_inherited(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "")
    suffix = ".exe" if os.name == "nt" else ""
    directory = tmp_path / ".tools/ffmpeg-bin/existing-build/bin"
    directory.mkdir(parents=True)
    for tool in ("ffmpeg", "ffprobe"):
        path = directory / (tool + suffix)
        path.touch()
        path.chmod(0o755)
    calls = []
    monkeypatch.setattr(launcher.subprocess, "run", lambda args, **kwargs: calls.append(args))
    launcher.resolve_media_tools(tmp_path)
    assert [(Path(args[0]), args[1]) for args in calls] == [
        (directory / (tool + suffix), "-version") for tool in ("ffmpeg", "ffprobe")
    ]
    assert os.environ["PATH"].split(os.pathsep)[0] == str(directory)
    # Available tools retain precedence; a second call does not prepend the path again.
    saved = os.environ["PATH"]
    launcher.resolve_media_tools(tmp_path)
    assert os.environ["PATH"] == saved
    # Never select a local build containing only one tool.
    monkeypatch.setenv("PATH", "")
    (directory / ("ffprobe" + suffix)).unlink()
    with pytest.raises(RuntimeError, match="missing"):
        launcher.resolve_media_tools(tmp_path)
    assert os.environ["PATH"] == ""
