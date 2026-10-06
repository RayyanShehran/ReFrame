import os
import socket
import sys
import time

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
