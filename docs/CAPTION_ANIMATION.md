# Caption animation and real motion previews

## Saved model and rendering

Caption-track schema 1 gains an independently versioned `animation` object (schema 1). Missing animation defaults to None; existing projects, revisions and render snapshots remain readable without a database migration. Saving uses the existing caption revision conflict check. Words, cue times, style, timeline and transcription provenance remain unchanged. Static appearance suggestions modify style only.

| Mode | Behavior |
| --- | --- |
| None | Existing static ASS rendering, unchanged |
| Fade | Linear entrance/exit opacity |
| Pop | Entrance scale from the selected percentage to saved size |
| Slide up | Entrance from below to saved final position |

Entrance and exit durations: 0–1 second, default 0.25. Pop initial scale: 50–100%, default 70%. Slide displacement: 0–25% of final canvas height, default 8%. Only relevant controls are shown. Pop/Slide have no exit effect. A zero entrance uses final geometry immediately.

Each effect is capped at half the visible cue interval. Cues with fewer than three visible 30 fps frames remain static. Existing half-open cue/frame timing and ASS centisecond encoding are retained; effects never extend the event. Full exports use `sdr-eq-mp4-v8` when animation is selected; None retains the previous version selection. Caption revision changes make older exports outdated but retain them until replacement succeeds.

The shared subtitle renderer generates numeric `fad`, `fscx/fscy`, `t` or `move` directives; Slide replaces the mutually exclusive `pos` directive. User text still passes through literal ASS escaping. Font, fill, outline, shadow and final alignment/position use the existing renderer after framing. Audio and output duration are untouched. Tag behavior follows the [Aegisub ASS documentation](https://aegisub.org/docs/latest/ass_tags/).

## Explicit motion preview

POST `/api/projects/{id}/caption-motion-preview` accepts the existing saved-cue request with expected recipe/framing/caption and optional plan revisions. It uses the existing shared media slot, project lock, source hashing, contained processes, cancellation join and staging cleanup. It does not publish or replace the full export.

The window includes up to six frames (0.2 seconds) before/after the selected saved cue, capped at 120 frames/four seconds. Long cues show the entrance; their exit may not appear. Whole/cut video graphs, saved color/framing, actual font and subtitle data come from existing export helpers. Frame trim retains full-output timestamps until subtitles are rendered; only then does `setpts` rebase the preview to zero. Thus crossing cuts retains the correct footage selections and cue animation phase. See [FFmpeg trim/setpts documentation](https://www.ffmpeg.org/ffmpeg-filters.html).

Limits: 640-pixel maximum edge, silent H.264 MP4, 4 MiB accepted file, existing 16 MiB aggregate staging watchdog, 30-second overall processing deadline and bounded 64 KiB tool output. The watchdog polls at 100 ms and may overshoot; it is not a filesystem quota. Output is probed and actually decoded to verify the exact frame count and absence of audio. Source/spec bindings are checked again before publication. Unconfirmed cleanup follows existing quarantine/error handling.

The bounded MP4 is returned as base64 and held in the mounted browser workspace; preview files are removed before the response. Previous successful previews remain playable during replacement/failure and are marked outdated on binding/draft changes. Late responses cannot replace current state. Switching sections retains the preview; refresh requires explicitly generating it again. Saved settings and full exports restore. No automatic requests or playback; native video controls provide explicit playback, including with reduced-motion preferences.

## Focused evidence — 2026-10-06

Windows, Python 3.12.14, FFmpeg/FFprobe 9.0.2. One reusable generated four-second 480 × 270 / 30 fps H.264/AAC fixture has green then blue imagery, a baked lower caption and a 440 Hz tone. No TikTok requests, OCR/transcription runs or asset downloads.

Actual None/Fade/Pop/Slide exports preserve four seconds and audio. Decoded frame checks cover cue absence before/after, changing Fade intensity, final geometry and a two-frame static cue. Pop starts with white-glyph bounds `(229,118,256,124)` and settles to static `(207,113,272,128)`. Slide starts 22 pixels lower and settles to the same bounds. Existing static/literal/provenance checks are retained.

A real saved-cut preview spans output 0.4–1.7 seconds: 39 decoded frames, 1.3 seconds, 480 × 270, 6,256 bytes. Source pixel changes from RGB `(37,69,37)` to `(37,38,133)` across the cut while Pop progresses from small entrance geometry to its saved size. Anton and uploaded DejaVu previews work; the uploaded-font case also verifies saved Square framing produces a 640 × 640 preview. Bound, timeout and stale-publication failures leave captions/export state intact and staging empty.

One production-build browser workflow used the same media and explicitly seeded stored color measurements: Fade → Save revision 2 → silent preview/play → Slide up → outdated retained preview → Save revision 3 → update → full render/play → refresh. The preview is 1.9 seconds / 480 × 270. Amiri Regular renders `Motion مرحبا`, with imported-SRT provenance and 0.3–1.8 second cue preserved. Full export: H.264/AAC, 480 × 270, four seconds, 120 video frames, 70,709 bytes, SHA-256 `56576730693c6c2fad83fce7df95e7a1bc333365a0083b0c4d286e5c46bfa82b`. Playback reached readyState 4 and paused=false; refresh restored revision 3, Slide up, text/font and the same export. Its restored download completed successfully. Switching sections retained the preview before refresh. Phone 390 × 740 and tablet 768 × 900 had no horizontal overflow (client/scroll widths 375 and 753 pixels).

Affected local checks passed: caption/model/render regressions, motion cut/custom-font/bounds failures, 16 frontend tests across four affected files (the existing save-body assertion was updated and rerun), targeted ESLint, frontend typecheck/build and Ruff lint/format. Final exact-commit CI runs the full foundation suites.

## Limits

Animations are manually selected; no reference-motion detection or karaoke. Preview encoding/downscaling differs from full-export quality, and long cues may omit their exit. Long/high-resolution inputs can reach the explicit deadline; retry retains the prior preview. Responsive evidence uses browser viewports and keyboard/native controls, not physical phone/tablet touch hardware. Existing custom-font format/glyph limitations remain; no separate new OTF benchmark was added.
