import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request

import tiktok_reference as spike

URL = "https://www.tiktok.com/@scout2015/video/6718335390845095173"


class URLTests(unittest.TestCase):
    def test_accepts_video_and_short_links_without_tracking(self):
        self.assertEqual(spike.validate_url(URL + "?utm_source=test"), (URL, False))
        self.assertEqual(spike.validate_url("https://vm.tiktok.com/ZMabc123/?share=1"), ("https://vm.tiktok.com/ZMabc123/", True))
        self.assertEqual(spike.validate_url("https://vt.tiktok.com/ZMabc123/"), ("https://vt.tiktok.com/ZMabc123/", True))
        self.assertEqual(spike.validate_url("https://www.tiktok.com/t/ZTRC5xgJp"), ("https://www.tiktok.com/t/ZTRC5xgJp", True))

    def test_rejects_non_video_and_unsafe_urls(self):
        bad = ["http://www.tiktok.com/@u/video/123", "https://www.tiktok.com.evil.test/@u/video/123", "https://user@www.tiktok.com/@u/video/123", "https://www.tiktok.com:443/@u/video/123", "https://www.tiktok.com/@u", "https://www.tiktok.com/@u/photo/123", "https://www.tiktok.com/@u/live", "https://www.tiktok.com/playlist/123", "https://vm.tiktok.com/@u/video/123", "https://www.tiktok.com/@u/video/123%2f", "https://127.0.0.1/@u/video/123"]
        for url in bad:
            with self.subTest(url=url), self.assertRaises(ValueError):
                spike.validate_url(url)

    def test_short_redirect_rejects_outside_video_path(self):
        handler = spike.TikTokRedirects()
        request = Request("https://vm.tiktok.com/ZMabc123/")
        for target in ["https://evil.test/@u/video/123", "http://www.tiktok.com/@u/video/123", "https://www.tiktok.com/"]:
            with self.subTest(target=target), self.assertRaises(ValueError):
                handler.redirect_request(request, None, 302, "Found", {}, target)

    def test_limits_and_isolation_flags(self):
        args = spike.ytdlp_base()
        for flag, value in [("--max-filesize", "50M"), ("--socket-timeout", "10"), ("--retries", "1"), ("--fragment-retries", "1")]:
            self.assertEqual(args[args.index(flag) + 1], value)
        for flag in ["--no-config", "--no-plugin-dirs", "--no-playlist", "--no-remote-components", "--no-geo-bypass"]:
            self.assertIn(flag, args)


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        patcher = patch.object(spike, "DATA_DIR", self.data)
        patcher.start()
        self.addCleanup(patcher.stop)

    @patch.object(spike.shutil, "which", side_effect=lambda name: name)
    def test_partial_download_removed_on_failure(self, _which):
        def fake(args, **kwargs):
            if "--dump-single-json" in args:
                return subprocess.CompletedProcess(args, 0, json.dumps({"id": "6718335390845095173", "duration": 15}), "")
            output = Path(args[args.index("-o") + 1]).parent / "reference.mp4.part"
            output.write_bytes(b"partial")
            return subprocess.CompletedProcess(args, 1, "", "HTTP Error 403: signed URL withheld")

        with patch.object(spike.subprocess, "run", side_effect=fake) as runner:
            result = spike.run_reference(URL)
        self.assertEqual(result["failure_category"], "download_failed")
        self.assertEqual(result["diagnostic"], "HTTP 403")
        self.assertEqual(list(self.data.iterdir()), [])
        self.assertTrue(all(call.kwargs["timeout"] <= 90 for call in runner.call_args_list))

    @patch.object(spike.shutil, "which", side_effect=lambda name: name)
    def test_success_decodes_and_cleans_up(self, _which):
        def fake(args, **kwargs):
            if "--dump-single-json" in args:
                return subprocess.CompletedProcess(args, 0, json.dumps({"id": "6718335390845095173", "duration": 15}), "")
            if "-o" in args:
                output = Path(args[args.index("-o") + 1]).parent / "reference.mp4"
                output.write_bytes(b"media")
                return subprocess.CompletedProcess(args, 0, "", "")
            if args[0] == "ffprobe":
                probe = {"format": {"duration": "15.0"}, "streams": [{"codec_type": "video", "codec_name": "h264", "width": 720, "height": 1280}, {"codec_type": "audio", "codec_name": "aac"}]}
                return subprocess.CompletedProcess(args, 0, json.dumps(probe), "")
            return subprocess.CompletedProcess(args, 0, "", "")

        with patch.object(spike.subprocess, "run", side_effect=fake):
            result = spike.run_reference(URL)
        self.assertEqual((result["retrieval"], result["video_decode"], result["audio"]), ("succeeded", "passed", "decoded"))
        self.assertEqual(list(self.data.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
