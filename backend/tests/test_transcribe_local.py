import json
import math
import shutil
import subprocess
import threading
import time
import uuid
from array import array
from types import SimpleNamespace

import pytest

import reference_engine as engine
import transcribe_local as adapter
import transcription


def speech(words):
    return {"segments": [{"start": 0.0, "end": 10.0, "text": "spoken", "words": words}]}


def test_adapter_uses_owned_runner_and_restores_cancellation(monkeypatch, tmp_path):
    monkeypatch.setattr(adapter, "model_ready", lambda: True)
    stop = threading.Event()
    calls = []

    def tool(args, directory, name, *a, **kw):
        assert engine._control.stop is stop
        calls.append(name)
        if name == "transcription-timeline":
            return subprocess.CompletedProcess(args, 0, b'{"streams":[{"start_time":"0"}]}', b"")
        if name == "transcription-audio":
            (directory / "audio.pcm").write_bytes(b"\0\0" * 16000)
            return subprocess.CompletedProcess(args, 0, b"", b"")
        data = {"segments": [], "language": "en", "versions": {"fixture": "mock"}}
        return subprocess.CompletedProcess(args, 0, json.dumps(data).encode(), b"")

    monkeypatch.setattr(engine, "run_command", tool)
    previous = getattr(engine._control, "stop", None)
    assert (
        adapter.run(tmp_path / "source.mp4", 1, "en", tmp_path, stop, time.monotonic() + 10)[0]
        == []
    )
    assert calls == ["transcription-timeline", "transcription-audio", "transcription-inference"]
    assert engine._control.stop is previous


@pytest.mark.parametrize(
    "failure,code,safe",
    [
        (engine.ProcessCleanupError, "cleanup_failure", False),
        (engine.ProbeInterrupted, "interrupted", True),
        (engine.SizeLimit, "temporary_size_limit", True),
    ],
)
def test_adapter_preserves_containment_failures(monkeypatch, tmp_path, failure, code, safe):
    monkeypatch.setattr(adapter, "model_ready", lambda: True)
    monkeypatch.setattr(engine, "run_command", lambda *a, **k: (_ for _ in ()).throw(failure()))
    previous = getattr(engine._control, "stop", None)
    with pytest.raises(engine.RetrievalFailure) as caught:
        adapter.run(
            tmp_path / "source.mp4", 1, "en", tmp_path, threading.Event(), time.monotonic() + 10
        )
    assert caught.value.code == code and caught.value.cleanup_safe is safe
    assert engine._control.stop is previous


def test_readable_word_cues_punctuation_overlap_and_empty():
    result = adapter.cues_from_words(
        speech(
            [
                {"start": 0.1, "end": 0.5, "word": "Hello"},
                {"start": 0.49, "end": 1, "word": " world."},
                {"start": 1.2, "end": 2, "word": " مرحبا"},
            ]
        ),
        10,
    )
    assert [(c.start, c.end, c.text) for c in result] == [
        (0.1, 1, "Hello world."),
        (1.2, 2, "مرحبا"),
    ]
    assert adapter.cues_from_words({"segments": []}, 10) == []
    words = [{"start": i, "end": i + 0.5, "word": " " + "word" * 8} for i in range(9)]
    split = adapter.cues_from_words(speech(words), 10)
    assert len(split) > 1 and all(len(c.text) <= 80 for c in split)


@pytest.mark.parametrize(
    "word",
    [
        {"start": math.nan, "end": 1, "word": "hello"},
        {"start": 1, "end": 1, "word": "hello"},
        {"start": 0, "end": 11, "word": "hello"},
        {"start": True, "end": 1, "word": "hello"},
        {"start": 0, "end": 1, "word": " "},
        {"start": 0, "end": 1, "word": "x" * 201},
    ],
)
def test_reject_invalid_words(word):
    with pytest.raises(engine.RetrievalFailure):
        adapter.cues_from_words(speech([word]), 10)


def test_limit_and_unusable_segments():
    with pytest.raises(engine.RetrievalFailure):
        adapter.cues_from_words(
            speech([{"start": 0, "end": 1, "word": "a"}, {"start": 0.9, "end": 2, "word": "b"}]), 10
        )
    with pytest.raises(engine.RetrievalFailure):
        adapter.cues_from_words(speech([]), 10)
    data = {
        "segments": [
            {
                "start": i / 2,
                "end": (i + 1) / 2,
                "text": "a.",
                "words": [{"start": i / 2, "end": (i + 1) / 2, "word": "a."}],
            }
            for i in range(201)
        ]
    }
    with pytest.raises(engine.RetrievalFailure, match="200 cues"):
        adapter.cues_from_words(data, 120)


def test_missing_model_never_starts_a_process(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter, "MODEL", tmp_path / "missing")
    monkeypatch.setattr(engine, "run_command", lambda *a, **k: pytest.fail("No automatic download"))
    with pytest.raises(engine.RetrievalFailure, match="setup"):
        adapter.run(tmp_path / "input", 1, "en", tmp_path, threading.Event(), time.monotonic() + 2)


@pytest.mark.parametrize(
    "video_start,audio_start,gap",
    [(0, 0, 0), (0, 0.936, 0), (0.5, 0, 0), (2, 2.936, 0), (0, 0, 0.5), (0, 0, 0.05)],
)
def test_real_pcm_keeps_video_relative_audio_timing(
    tmp_path, monkeypatch, video_start, audio_start, gap
):
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("FFmpeg/FFprobe required for local timing fixture")
    path = tmp_path / "timed.mp4"
    args = [
        ffmpeg,
        "-v",
        "error",
        "-copyts",
        "-itsoffset",
        str(video_start),
        "-f",
        "lavfi",
        "-i",
        "color=c=gray:s=64x64:r=30:d=3",
        "-itsoffset",
        str(audio_start),
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=880:sample_rate=48000:duration=2.1",
        "-map",
        "0:v",
        "-map",
        "1:a",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-bf",
        "0",
        "-c:a",
        "aac",
        "-avoid_negative_ts",
        "disabled",
    ]
    if gap:
        args += ["-af", f"aselect='not(between(t,0.8,{0.8 + gap}))'"]
    subprocess.run(args + [str(path)], check=True, capture_output=True, timeout=10)
    metadata = json.loads(
        subprocess.run(
            [ffprobe, "-v", "error", "-show_streams", "-of", "json", str(path)],
            check=True,
            capture_output=True,
            timeout=10,
        ).stdout
    )
    video, audio = metadata["streams"]
    assert float(video["start_time"]) == pytest.approx(video_start, abs=0.001)
    stage = tmp_path / "stage"
    monkeypatch.setattr(adapter, "model_ready", lambda: True)
    real_tool = engine.run_command
    expected = max(0, audio_start - video_start)

    def tool(args, directory, name, *a, **kw):
        if name != "transcription-inference":
            return real_tool(args, directory, name, *a, **kw)
        raw = (directory / "audio.pcm").read_bytes()
        samples = array("h")
        samples.frombytes(raw)
        assert 0 < len(raw) <= adapter.PCM_LIMIT and len(raw) % 2 == 0

        def rms(start, end):
            values = samples[round(start * 16000) : round(end * 16000)]
            assert values
            return math.sqrt(sum(v * v for v in values) / len(values))

        windows = [rms(i / 100, (i + 1) / 100) for i in range(len(samples) // 160)]
        onset = next(i / 100 for i, value in enumerate(windows) if value > 100)
        # Two 48 kHz AAC packets plus one 10 ms measurement window.
        tolerance = 2 * 1024 / 48000 + 0.01
        print(
            f"video={video_start:.3f} audio_stream={float(audio['start_time']):.3f} "
            f"expected_tone={expected:.3f} measured_onset={onset:.3f} "
            f"pcm_seconds={len(samples) / 16000:.3f}"
        )
        assert onset == pytest.approx(expected, abs=tolerance)
        if expected > tolerance:
            assert rms(0, expected - tolerance) < 1
        assert rms(expected + 0.1, expected + 0.3) > 1000
        if video_start > audio_start:
            assert len(samples) / 16000 == pytest.approx(
                2.1 - video_start + audio_start, abs=tolerance
            )
        if gap:
            middle = 0.8 + gap / 2
            silence = rms(middle - 0.005, middle + 0.005)
            assert silence < 1 and rms(0.8 + gap + 0.2, 0.8 + gap + 0.3) > 1000
            print(f"timestamp_gap={gap:.3f} seconds; interior PCM RMS={silence:.3f}")
        # Mock only inference: model seconds already include PCM's padded initial silence.
        data = {
            "language": "en",
            "versions": {"model": "mock"},
            "segments": [
                {
                    "start": expected + 0.1,
                    "end": expected + 0.5,
                    "text": "Aligned.",
                    "words": [{"start": expected + 0.1, "end": expected + 0.5, "word": "Aligned."}],
                }
            ],
        }
        return subprocess.CompletedProcess(args, 0, json.dumps(data).encode(), b"")

    monkeypatch.setattr(engine, "run_command", tool)
    binding = transcription.captions.AutomaticBinding(
        timeline=transcription.captions.Timeline(
            mode="whole",
            duration_seconds=3,
            footage={
                "project_id": str(uuid.uuid4()),
                "clip_id": "clip-" + "a" * 32 + ".mp4",
                "media_sha256": "a" * 64,
            },
        ),
        audio=transcription.audio_settings.Settings(),
    )
    value = {
        "path": path,
        "language": "en",
        "binding": binding,
        "output": SimpleNamespace(
            duration_seconds=3,
            output_id=uuid.uuid4(),
            sha256="b" * 64,
            ffmpeg_version="real fixture",
        ),
    }
    _, proposal = transcription.pipeline(value, stage, threading.Event(), time.monotonic() + 20)
    assert proposal.binding == binding
    assert proposal.cues[0].start == pytest.approx(expected + 0.1) and proposal.cues[
        0
    ].end == pytest.approx(expected + 0.5)


@pytest.mark.parametrize(
    "data",
    [
        {"streams": []},
        {"streams": [{}]},
        {"streams": [{"start_time": "NaN"}]},
        {"streams": [{"start_time": "invalid"}]},
    ],
)
def test_invalid_video_timeline_never_starts_inference(tmp_path, monkeypatch, data):
    monkeypatch.setattr(adapter, "model_ready", lambda: True)

    def tool(args, directory, name, *a, **kw):
        assert name == "transcription-timeline"
        return subprocess.CompletedProcess(args, 0, json.dumps(data).encode(), b"")

    monkeypatch.setattr(engine, "run_command", tool)
    with pytest.raises(engine.RetrievalFailure, match="video's timeline"):
        adapter.run(
            tmp_path / "source.mp4", 3, "en", tmp_path, threading.Event(), time.monotonic() + 10
        )
