# TikTok reference-access feasibility

**Decision: proceed experimentally.** On 2026-09-29, the corrected developer-only probe obtained a real media file from one public TikTok documentation example and verified nonempty decoded video and audio bytes. This is evidence of local technical feasibility for that sample. It does not prove production-host reliability, continuing extractor support, platform permission, or reuse rights. ReFrame's reference input remains a TikTok link; uploaded user clips are a separate future input. The app does not analyze TikTok links yet.

## Sources reviewed (2026-09-29)

- TikTok [Embed Videos](https://developers.tiktok.com/docs/en/embed-videos): oEmbed accepts a video URL and returns embed HTML, title/author and thumbnail metadata. No downloadable video/audio field is documented.
- TikTok [Embed Player](https://developers.tiktok.com/docs/en/embed-player): an iframe playback surface for a post ID, not a documented media-byte access API.
- TikTok [Query Videos](https://developers.tiktok.com/docs/en/tiktok-api-v2-video-query) and [Video Object](https://developers.tiktok.com/docs/en/tiktok-api-v2-video-object): Display API requires user authorization with `video.list` and queries that user's videos. Returned fields include post metadata, cover/share/embed URLs and dimensions/duration; no media download URL is documented. This does not address arbitrary public reference links.
- [yt-dlp documentation](https://github.com/yt-dlp/yt-dlp) and [TikTok extractor source](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/tiktok.py): current extractor approach used for this local spike. Support depends on an unofficial extractor and TikTok behavior. The pinned package is `yt-dlp==2026.8.19` in `tools/pyproject.toml` and `tools/uv.lock`.
- [FFmpeg command options](https://ffmpeg.org/ffmpeg.html), [raw video and PCM formats](https://ffmpeg.org/ffmpeg-formats.html), and [scale filter](https://ffmpeg.org/ffmpeg-filters.html), reviewed 2026-09-29: `-frames:v 1`, `-t 1`, `-xerror`, `-abort_on empty_output`, raw RGB24 video and signed 16-bit little-endian PCM inform the corrected decode checks. The local Windows test used a publisher-provided FFmpeg 9.0.2 essentials build from [FFmpeg downloads](https://ffmpeg.org/download.html); it is not committed or required for ordinary app startup.

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

The command prints a small JSON summary and returns zero only when the requested video ID matches extracted metadata, retrieval passes, exactly one 64 x 64 RGB24 frame (12,288 bytes) is produced, and audio, if present, yields at least one complete 16 kHz mono PCM S16LE sample (at most 16,000 samples). `audio: absent` means the file lacks an audio stream; zero audio samples from a present stream fail. FFmpeg's `-xerror` and `-abort_on empty_output` complement the explicit byte checks. The script does not expose raw downloader output, signed media URLs or cookies. It creates a generated `data/tiktok-*` directory and removes it with its media and partial files after inspection, including ordinary failures. If process termination or deletion cannot be confirmed, the JSON reports `cleanup_failed` and the retained temporary path. Keep `data/` ignored.

Input must be an HTTPS TikTok single-video URL (`/@user/video/id`) or a documented `vm.tiktok.com`, `vt.tiktok.com`, or `www.tiktok.com/t/` short link. It rejects lookalike hosts, credentials, custom ports, profiles, playlists, live and photo paths. Short-link redirects are validated at each hop and must end at a supported video URL. Tracking query parameters are stripped before retrieval. The probe uses argument-list process calls; it disables playlists, personal config, plugins, browser cookies, remote components, cache and geo bypass. It does not handle login walls, CAPTCHA, private content or regional blocks.

Limits: one 150-second monotonic deadline for the invocation; each short-link request at most 10 seconds, metadata stage 30 seconds, download stage 90 seconds, and ffprobe and each FFmpeg decode stage 15 seconds, all capped by remaining overall time. yt-dlp has a 10-second socket timeout and one retry for downloads, fragments, extractor and file access. The accepted media file remains limited to 50 MiB (`--max-filesize 50M` plus a post-download check). A watchdog sums regular files across the generated temporary directory, including fragments, partial media and tool output, every 100 ms and stops the owned process tree above 60 MiB. Polling is **not a strict filesystem quota**: disk use can overshoot between checks, depending on write rate and termination delay. Metadata/stdout readback is capped at 1 MiB and stderr readback at 64 KiB; tool outputs are written to the watched temporary directory rather than unbounded memory pipes. On Windows the command is created suspended, assigned to a private Job Object, then resumed; the job's active-process count must reach zero before cleanup. The job has kill-on-close enabled. On POSIX the command starts a new session; `waitid(..., WNOWAIT)` leaves the leader PID reserved while the runner signals the original process group and then checks that the group is gone. The direct parent's exit alone never confirms cleanup. File removal is retried for up to two seconds after process termination. If termination or file-handle release cannot be confirmed, the probe reports `cleanup_failed` and retains the temporary directory.

This ownership model covers descendants that remain in the inherited Windows job or POSIX process group; a process deliberately created outside that ownership (for example, a POSIX child calling `setsid`) is outside this probe's guarantee. Platform behavior follows Microsoft's [Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects), [suspended-thread](https://learn.microsoft.com/en-us/windows/win32/procthread/suspending-thread-execution) guidance and the POSIX [process-group](https://man7.org/linux/man-pages/man2/getpgrp.2.html) and [killpg](https://man7.org/linux/man-pages/man3/killpg.3.html) references (reviewed 2026-09-30). The tool is for development only and is not exposed through the frontend or HTTP API.

## Live evidence

All attempts below were made **2026-09-29** on Windows x64, Python 3.12.14, yt-dlp 2026.8.19, FFmpeg/ffprobe 9.0.2. Links shown have no tracking parameters. Each invocation had a separate temporary directory; no media was retained. These are live network observations, separate from deterministic mocked CI tests. The first table preserves the original Milestone 2 observations; at that time, successful decode was inferred from FFmpeg exit status and nonempty output was not yet checked.

| Input link | Resolved identity | Metadata | Media retrieval | Video frame | Audio | Category / sanitized diagnostic |
| --- | --- | --- | --- | --- | --- | --- |
| `https://www.tiktok.com/@scout2015/video/6718335390845095173` (TikTok documentation sample) | `6718335390845095173` | Success; duration 10 s | Success; ffprobe duration 10.495011 s, 720 x 1280, HEVC + AAC | Decoded | Present; 1 s decoded | None |
| `https://www.tiktok.com/@moxypatch/video/7206382937372134662` (yt-dlp extractor test sample) | URL ID `7206382937372134662`; no extracted metadata | Failed | Not attempted | Not attempted | Not checked | `metadata_failed`; extractor returned an error, raw output withheld |
| `https://vm.tiktok.com/ZTR45GpSF/` (yt-dlp extractor test short-link sample) | None; redirected to TikTok home page | Not attempted | Not attempted | Not attempted | Not checked | `short_link_resolution_failed`; redirect did not reach a supported video |
| `https://www.tiktok.com/t/ZTRC5xgJp` and `https://vt.tiktok.com/ZSe4FqkKd` (additional yt-dlp short-link fixtures) | None; each redirected to TikTok home page | Not attempted | Not attempted | Not attempted | Not checked | `short_link_resolution_failed`; expired fixtures |

### Corrected probe rerun (2026-09-29)

The same public TikTok documentation URL `https://www.tiktok.com/@scout2015/video/6718335390845095173` was run after the correction on Windows x64 with Python 3.12.14, yt-dlp 2026.8.19 and FFmpeg/ffprobe 9.0.2. Extracted ID matched the URL ID `6718335390845095173`; metadata duration was 10 s. Media retrieval succeeded; ffprobe reported 10.495011 s, 720 x 1280, HEVC video and AAC audio. FFmpeg produced **12,288 bytes** for one 64 x 64 RGB24 frame and **16,000 complete audio samples** (32,000 bytes) from the first second, 16 kHz mono PCM S16LE. The corrected probe returned success and removed its temporary directory. This confirms nonempty decoded output for this sample; it does not validate other public links or a production host.

The TikTok documentation video's oEmbed endpoint also returned embed HTML, author and thumbnail metadata during a separate live request. That confirms metadata/embedded-playback availability only; the decoder result above came from yt-dlp plus FFmpeg.

## Production risks and next decision

- **URL and redirect security:** keep exact-host HTTPS validation and redirect checks if production ingestion is designed; treat remote metadata and media as untrusted. This probe's input restrictions are deliberately narrow.
- **Extractor reliability:** one other public canonical sample failed before download, and available short-link fixtures no longer resolve to videos. A fresh public `vm.tiktok.com`, `vt.tiktok.com` or `/t/` video short link is needed to validate live short-link success. TikTok and yt-dlp can change without notice.
- **Resource use:** the 50 MiB accepted-media limit, 60 MiB polled temporary-directory budget and 150-second deadline suit a local probe, not a production capacity or abuse policy. Fragment writes may overshoot the budget between 100 ms checks. Production isolation and storage policy need separate design.
- **Platform and rights:** official embed/Display API behavior does not authorize arbitrary video download or reuse. Terms, licensing, creator rights, music rights, regional restrictions and any required permissions need review before a production import path is chosen. Do not use login bypasses or private content.

The architect should review these observations before choosing a production ingestion approach. No reference upload fallback is proposed.
