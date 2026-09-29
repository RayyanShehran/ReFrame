# TikTok reference-access feasibility

**Decision: proceed experimentally.** On 2026-09-29, a developer-only probe obtained a real media file from one public TikTok documentation example, and FFmpeg decoded a video frame and one second of audio. This is evidence of local technical feasibility for that sample. It does not prove production-host reliability, continuing extractor support, platform permission, or reuse rights. Reframe's reference input remains a TikTok link; uploaded user clips are a separate future input. The app does not analyze TikTok links yet.

## Sources reviewed (2026-09-29)

- TikTok [Embed Videos](https://developers.tiktok.com/docs/en/embed-videos): oEmbed accepts a video URL and returns embed HTML, title/author and thumbnail metadata. No downloadable video/audio field is documented.
- TikTok [Embed Player](https://developers.tiktok.com/docs/en/embed-player): an iframe playback surface for a post ID, not a documented media-byte access API.
- TikTok [Query Videos](https://developers.tiktok.com/docs/en/tiktok-api-v2-video-query) and [Video Object](https://developers.tiktok.com/docs/en/tiktok-api-v2-video-object): Display API requires user authorization with `video.list` and queries that user's videos. Returned fields include post metadata, cover/share/embed URLs and dimensions/duration; no media download URL is documented. This does not address arbitrary public reference links.
- [yt-dlp documentation](https://github.com/yt-dlp/yt-dlp) and [TikTok extractor source](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/tiktok.py): current extractor approach used for this local spike. Support depends on an unofficial extractor and TikTok behavior. The pinned package is `yt-dlp==2026.8.19` in `tools/pyproject.toml` and `tools/uv.lock`.
- [FFmpeg downloads](https://ffmpeg.org/download.html): FFmpeg/ffprobe provide stream inspection and decode checks. The local Windows test used a publisher-provided FFmpeg 9.0.2 essentials build; it is not committed or required for ordinary app startup.

URL recognition, oEmbed metadata, and embedded playback are distinct from obtaining decodable media bytes. The official surfaces above do not establish that arbitrary public TikTok references can be downloaded for frame/audio analysis. The probe tests the latter through yt-dlp. Public availability and successful retrieval do not grant permission to reuse a video or its audio.

## Reproduce the developer probe

Use Python 3.12, uv 0.12.20, and FFmpeg/ffprobe 9.0.2 on `PATH`. From PowerShell:

```powershell
cd C:\Projects\Reframe\tools
uv sync --locked
ffmpeg -version
ffprobe -version
uv run --locked python tiktok_reference.py 'https://www.tiktok.com/@scout2015/video/6718335390845095173'
uv run --locked python -m unittest discover -s tests -p 'test_tiktok_reference.py'
```

The command prints a small JSON summary and returns zero only when retrieval and video decoding pass and audio, if present, decodes. `audio: absent` means the file lacks an audio stream; it is different from retrieval failure. The script does not expose raw downloader output, signed media URLs or cookies. It creates a generated `data/tiktok-*` directory and removes it with its media and partial files after inspection, including ordinary failures. Keep `data/` ignored.

Input must be an HTTPS TikTok single-video URL (`/@user/video/id`) or a documented `vm.tiktok.com`, `vt.tiktok.com`, or `www.tiktok.com/t/` short link. It rejects lookalike hosts, credentials, custom ports, profiles, playlists, live and photo paths. Short-link redirects are validated at each hop and must end at a supported video URL. Tracking query parameters are stripped before retrieval. The probe uses argument-list process calls; it disables playlists, personal config, plugins, browser cookies, remote components, cache and geo bypass. It does not handle login walls, CAPTCHA, private content or regional blocks.

Limits: short-link request timeout 10 seconds; yt-dlp metadata stage 30 seconds and download stage 90 seconds; socket timeout 10 seconds; one retry for downloads, fragments, extractor and file access; yt-dlp `--max-filesize 50M` and post-download 50 MiB check; ffprobe and each FFmpeg decode capped at 15 seconds. The file-size option and final check bound the accepted output, but fragment/temporary disk use during a download is not a strict 50 MiB ceiling. The process timeout bounds each command, not the whole run as one deadline. The tool is for development only and is not exposed through the frontend or HTTP API.

## Live evidence

All attempts below were made **2026-09-29** on Windows x64, Python 3.12.14, yt-dlp 2026.8.19, FFmpeg/ffprobe 9.0.2. Links shown have no tracking parameters. Each invocation had a separate temporary directory; no media was retained. These are live network observations, separate from deterministic mocked CI tests.

| Input link | Resolved identity | Metadata | Media retrieval | Video frame | Audio | Category / sanitized diagnostic |
| --- | --- | --- | --- | --- | --- | --- |
| `https://www.tiktok.com/@scout2015/video/6718335390845095173` (TikTok documentation sample) | `6718335390845095173` | Success; duration 10 s | Success; ffprobe duration 10.495011 s, 720×1280, HEVC + AAC | Decoded | Present; 1 s decoded | None |
| `https://www.tiktok.com/@moxypatch/video/7206382937372134662` (yt-dlp extractor test sample) | URL ID `7206382937372134662`; no extracted metadata | Failed | Not attempted | Not attempted | Not checked | `metadata_failed`; extractor returned an error, raw output withheld |
| `https://vm.tiktok.com/ZTR45GpSF/` (yt-dlp extractor test short-link sample) | None; redirected to TikTok home page | Not attempted | Not attempted | Not attempted | Not checked | `short_link_resolution_failed`; redirect did not reach a supported video |
| `https://www.tiktok.com/t/ZTRC5xgJp` and `https://vt.tiktok.com/ZSe4FqkKd` (additional yt-dlp short-link fixtures) | None; each redirected to TikTok home page | Not attempted | Not attempted | Not attempted | Not checked | `short_link_resolution_failed`; expired fixtures |

The TikTok documentation video's oEmbed endpoint also returned embed HTML, author and thumbnail metadata during a separate live request. That confirms metadata/embedded-playback availability only; the decoder result above came from yt-dlp plus FFmpeg.

## Production risks and next decision

- **URL and redirect security:** keep exact-host HTTPS validation and redirect checks if production ingestion is designed; treat remote metadata and media as untrusted. This probe's input restrictions are deliberately narrow.
- **Extractor reliability:** one other public canonical sample failed before download, and available short-link fixtures no longer resolve to videos. A fresh public `vm.tiktok.com`, `vt.tiktok.com` or `/t/` video short link is needed to validate live short-link success. TikTok and yt-dlp can change without notice.
- **Resource use:** the 50 MiB output and stage time limits suit a bounded local probe, not a production capacity or abuse policy. Temporary fragments may exceed the accepted final size. Production isolation, total deadline and storage policy need separate design.
- **Platform and rights:** official embed/Display API behavior does not authorize arbitrary video download or reuse. Terms, licensing, creator rights, music rights, regional restrictions and any required permissions need review before a production import path is chosen. Do not use login bypasses or private content.

The architect should review these observations before choosing a production ingestion approach. No reference upload fallback is proposed.
