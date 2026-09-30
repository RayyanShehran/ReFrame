import json
import shutil
import subprocess
import threading

import pytest

import reference_engine as engine

URL = "https://www.tiktok.com/@scout2015/video/6718335390845095173"


@pytest.mark.parametrize(
    "case,expected",
    [
        ("valid", None),
        ("no_audio", None),
        ("identity", "identity_mismatch"),
        ("size", "size_or_output_limit"),
        ("duration", "duration_limit"),
        ("nan", "duration_limit"),
        ("dimension", "dimension_limit"),
        ("cover", "no_video_stream"),
        ("frame", "decode_failed"),
        ("audio", "decode_failed"),
        ("json", "invalid_tool_output"),
        ("restricted", "reference_restricted"),
        ("rate", "rate_limited"),
    ],
)
def test_shared_api_engine_validation(monkeypatch, tmp_path, case, expected):
    monkeypatch.setattr(engine.shutil, "which", lambda name: name)
    monkeypatch.setattr(engine, "MAX_BYTES", 4 if case == "size" else 1024)
    calls = []

    def command(args, stage, name, deadline, limit):
        calls.append(name)
        output, status, stderr = b"", 0, ""
        if name.endswith("-version"):
            output = b"ffmpeg version 9.0.2 Copyright"
        elif name == "metadata":
            if case in {"restricted", "rate"}:
                status, stderr = 1, f"HTTP Error {403 if case == 'restricted' else 429}: withheld"
            output = (
                b"{"
                if case == "json"
                else json.dumps(
                    {"id": "1" if case == "identity" else "6718335390845095173"}
                ).encode()
            )
        elif name == "download":
            (stage / "reference.mp4").write_bytes(b"media")
        elif name == "probe":
            streams = [
                {
                    "index": 0,
                    "codec_type": "video",
                    "codec_name": "hevc",
                    "width": 4097 if case == "dimension" else 720,
                    "height": 1280,
                    "disposition": {"attached_pic": int(case == "cover")},
                }
            ]
            if case != "no_audio":
                streams.append({"index": 1, "codec_type": "audio", "codec_name": "aac"})
            output = json.dumps(
                {
                    "streams": streams,
                    "format": {
                        "duration": "nan" if case == "nan" else 121 if case == "duration" else 10
                    },
                }
            ).encode()
        elif name == "frame":
            output = b"" if case == "frame" else b"f" * engine.FRAME_BYTES
        elif name == "audio":
            output = b"" if case == "audio" else b"a" * 320
        return subprocess.CompletedProcess(args, status, output, stderr)

    monkeypatch.setattr(engine, "run_command", command)
    if expected:
        with pytest.raises(engine.RetrievalFailure) as error:
            engine.retrieve_media(URL, tmp_path, threading.Event())
        assert error.value.code == expected
        if case == "identity":
            assert "download" not in calls
    else:
        result = engine.retrieve_media(URL, tmp_path, threading.Event())
        assert result["media"]["decoded_frame_bytes"] == 12288
        assert result["media"]["has_audio"] is (case != "no_audio")


def test_generated_media_through_real_probe_and_decode(monkeypatch, tmp_path):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg required")
    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x240:rate=10",
            "-t",
            "1",
            "-c:v",
            "mpeg4",
            str(source),
        ],
        check=True,
    )
    real = engine.run_command

    def command(args, stage, name, deadline, limit):
        if name == "metadata":
            return subprocess.CompletedProcess(args, 0, b'{"id":"6718335390845095173"}', "")
        if name == "download":
            shutil.copyfile(source, stage / "reference.mp4")
            return subprocess.CompletedProcess(args, 0, b"", "")
        return real(args, stage, name, deadline, limit)

    monkeypatch.setattr(engine, "run_command", command)
    result = engine.retrieve_media(URL, tmp_path, threading.Event())
    assert result["media"]["decoded_frame_bytes"] == 12288
    assert result["media"]["has_audio"] is False
