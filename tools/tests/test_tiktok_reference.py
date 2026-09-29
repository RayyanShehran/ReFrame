import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request

import tiktok_reference as spike

URL = "https://www.tiktok.com/@scout2015/video/6718335390845095173"
ID = "6718335390845095173"


class URLTests(unittest.TestCase):
    def test_accepts_video_and_short_links_without_tracking(self):
        self.assertEqual(spike.validate_url(URL + "?utm_source=test"), (URL, False))
        for url in ["https://vm.tiktok.com/ZMabc123/", "https://vt.tiktok.com/ZMabc123/", "https://www.tiktok.com/t/ZTRC5xgJp"]:
            self.assertTrue(spike.validate_url(url + "?share=1")[1])

    def test_rejects_non_video_and_unsafe_urls(self):
        bad = ["http://www.tiktok.com/@u/video/123", "https://www.tiktok.com.evil.test/@u/video/123", "https://user@www.tiktok.com/@u/video/123", "https://www.tiktok.com:443/@u/video/123", "https://www.tiktok.com/@u", "https://www.tiktok.com/@u/photo/123", "https://www.tiktok.com/@u/live", "https://www.tiktok.com/playlist/123", "https://vm.tiktok.com/@u/video/123", "https://www.tiktok.com/@u/video/123%2f", "https://127.0.0.1/@u/video/123"]
        for url in bad:
            with self.subTest(url=url), self.assertRaises(ValueError):
                spike.validate_url(url)

    def test_short_redirect_is_not_followed_blindly(self):
        handler = spike.NoRedirects()
        self.assertIsNone(handler.redirect_request(Request("https://vm.tiktok.com/ZMabc123/"), None, 302, "Found", {}, "https://evil.test/"))

    def test_short_redirect_target_is_validated(self):
        short = "https://vm.tiktok.com/ZMabc123/"
        response = HTTPError(short, 302, "Found", {"Location": "https://evil.test/@u/video/123"}, None)
        with patch.object(spike.urllib.request, "build_opener") as build:
            build.return_value.open.side_effect = response
            with self.assertRaises(ValueError):
                spike.resolve_short(short, time.monotonic() + 1)


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        patcher = patch.object(spike, "DATA_DIR", self.data)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(spike.shutil, "which", side_effect=lambda name: name)
        patcher.start()
        self.addCleanup(patcher.stop)

    def fake_commands(self, *, frame=None, audio=None, has_audio=True, video_id=ID, metadata=None, media=b"media"):
        frame = b"v" * spike.FRAME_BYTES if frame is None else frame
        audio = b"a" * 320 if audio is None else audio
        calls = []

        def fake(args, temp, name, deadline, stage_limit):
            calls.append(name)
            if name == "metadata":
                output = json.dumps({"id": video_id, "duration": 10}).encode() if metadata is None else metadata
            elif name == "download":
                (temp / "reference.mp4").write_bytes(media)
                output = b""
            elif name == "probe":
                streams = [{"codec_type": "video", "codec_name": "hevc", "width": 720, "height": 1280}]
                if has_audio:
                    streams.append({"codec_type": "audio", "codec_name": "aac"})
                output = json.dumps({"format": {"duration": "10.5"}, "streams": streams}).encode()
            elif name == "frame":
                output = frame
            elif name == "audio":
                output = audio
            else:
                self.fail(name)
            return subprocess.CompletedProcess(args, 0, output, "")

        return fake, calls

    def run_fake(self, **kwargs):
        fake, calls = self.fake_commands(**kwargs)
        with patch.object(spike, "run_command", side_effect=fake):
            result = spike.run_reference(URL)
        self.assertEqual(list(self.data.iterdir()), [])
        return result, calls

    def test_valid_output_has_observable_frame_and_samples(self):
        result, calls = self.run_fake()
        self.assertEqual(calls, ["metadata", "download", "probe", "frame", "audio"])
        self.assertEqual((result["video_decode"], result["audio"], result["failure_category"]), ("passed", "decoded", None))
        self.assertEqual(result["media"]["decoded_frame_bytes"], 12288)
        self.assertEqual(result["media"]["decoded_audio_samples"], 160)

    def test_zero_exit_empty_video_is_failure(self):
        result, _ = self.run_fake(frame=b"")
        self.assertEqual((result["video_decode"], result["failure_category"]), ("failed", "decode_failed"))

    def test_zero_exit_empty_audio_is_failure(self):
        result, _ = self.run_fake(audio=b"")
        self.assertEqual((result["audio"], result["failure_category"]), ("decode_failed", "decode_failed"))

    def test_video_without_audio_is_valid(self):
        result, calls = self.run_fake(has_audio=False)
        self.assertEqual((result["video_decode"], result["audio"], result["failure_category"]), ("passed", "absent", None))
        self.assertNotIn("audio", calls)

    def test_mismatched_identity_stops_before_download(self):
        result, calls = self.run_fake(video_id="123")
        self.assertEqual(result["failure_category"], "identity_mismatch")
        self.assertEqual(calls, ["metadata"])

    def test_malformed_metadata_is_rejected(self):
        result, calls = self.run_fake(metadata=b"{")
        self.assertEqual(result["failure_category"], "invalid_tool_output")
        self.assertEqual(calls, ["metadata"])

    def test_malformed_probe_streams_are_rejected(self):
        fake, _ = self.fake_commands()

        def malformed(args, temp, name, deadline, stage_limit):
            if name == "probe":
                return subprocess.CompletedProcess(args, 0, b'{"streams":[1]}', "")
            return fake(args, temp, name, deadline, stage_limit)

        with patch.object(spike, "run_command", side_effect=malformed):
            result = spike.run_reference(URL)
        self.assertEqual(result["failure_category"], "invalid_tool_output")
        self.assertEqual(list(self.data.iterdir()), [])

    def test_oversized_media_is_rejected(self):
        with patch.object(spike, "MAX_BYTES", 4):
            result, calls = self.run_fake(media=b"12345")
        self.assertEqual(result["failure_category"], "size_or_output_limit")
        self.assertEqual(calls, ["metadata", "download"])

    def test_partial_download_is_cleaned_on_failure(self):
        fake, _ = self.fake_commands()

        def fail_download(args, temp, name, deadline, stage_limit):
            if name == "download":
                (temp / "reference.mp4.part").write_bytes(b"partial")
                return subprocess.CompletedProcess(args, 1, b"", "HTTP Error 403: signed URL withheld")
            return fake(args, temp, name, deadline, stage_limit)

        with patch.object(spike, "run_command", side_effect=fail_download):
            result = spike.run_reference(URL)
        self.assertEqual((result["failure_category"], result["diagnostic"]), ("download_failed", "HTTP 403"))
        self.assertEqual(list(self.data.iterdir()), [])

    def test_aggregate_temp_limit_stops_process_and_cleans(self):
        real = spike.run_command
        code = "import pathlib,sys,time; (pathlib.Path(sys.argv[1])/'fragment.part').write_bytes(b'x'*4096); time.sleep(10)"

        def large(args, temp, name, deadline, stage_limit):
            return real([sys.executable, "-c", code, str(temp)], temp, name, deadline, stage_limit)

        with patch.object(spike, "TEMP_BUDGET", 1024), patch.object(spike, "run_command", side_effect=large):
            result = spike.run_reference(URL)
        self.assertEqual(result["failure_category"], "temporary_size_limit")
        self.assertEqual(list(self.data.iterdir()), [])

    def test_real_timeout_stops_process_and_cleans(self):
        real = spike.run_command

        def slow(args, temp, name, deadline, stage_limit):
            return real([sys.executable, "-c", "import time; time.sleep(10)"], temp, name, deadline, stage_limit)

        with patch.object(spike, "TOTAL_SECONDS", 0.3), patch.object(spike, "run_command", side_effect=slow):
            result = spike.run_reference(URL)
        self.assertEqual(result["failure_category"], "timeout")
        self.assertEqual(list(self.data.iterdir()), [])

    def test_captured_output_is_bounded(self):
        real = spike.run_command
        code = "import sys; sys.stdout.write('x'*4096)"

        def noisy(args, temp, name, deadline, stage_limit):
            return real([sys.executable, "-c", code], temp, name, deadline, stage_limit)

        with patch.object(spike, "MAX_CAPTURE", 1024), patch.object(spike, "run_command", side_effect=noisy):
            result = spike.run_reference(URL)
        self.assertEqual(result["failure_category"], "invalid_tool_output")
        self.assertEqual(list(self.data.iterdir()), [])

    def test_process_tree_termination_stops_child(self):
        marker = self.data / "started"
        escaped = self.data / "escaped"
        child = "import pathlib,sys,time; time.sleep(2); pathlib.Path(sys.argv[1]).write_text('escaped')"
        parent = "import pathlib,subprocess,sys,time; subprocess.Popen([sys.executable,'-c',sys.argv[2],sys.argv[3]]); pathlib.Path(sys.argv[1]).write_text('started'); time.sleep(10)"
        with self.assertRaises(spike.ProbeTimeout):
            spike.run_command([sys.executable, "-c", parent, str(marker), child, str(escaped)], self.data, "helper", time.monotonic() + 1, 1)
        self.assertTrue(marker.exists())
        time.sleep(2.1)
        self.assertFalse(escaped.exists(), "owned child survived process-tree termination")

    def test_cleanup_failure_is_reported(self):
        fake, _ = self.fake_commands()
        with patch.object(spike, "run_command", side_effect=fake), patch.object(spike.shutil, "rmtree", side_effect=OSError):
            result = spike.run_reference(URL)
        self.assertEqual(result["failure_category"], "cleanup_failed")
        self.assertTrue(Path(result["temporary_directory"]).exists())


if __name__ == "__main__":
    unittest.main()
