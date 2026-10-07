# Manual multi-clip sequence — Milestone 28

## Workflow and bounds

Upload and rename sources in Footage; create slots explicitly from saved estimated reference pacing in Style & cuts. Assign a source, select a start (end follows the required duration), or edit the slot duration. Add/remove/reorder slots and Save. Unassigned slots can be saved; every slot must be assigned before rendering. Regeneration explicitly replaces saved/draft slots after confirmation. Choose Whole clip, Saved cut plan, or Multi-clip sequence in Export. Saved drafts/results restore after refresh; unsaved edits survive section/slot switches only.

Per project: **10 clips, 500 MiB logical retained/staged source footage**, **100 MiB per file**, 120 seconds and 4096 pixels per source. Multipart parsing caps file data before completed spooling; globally serialized staging reserves the remaining source budget, including concurrent uploads. Spool and staged copy can temporarily duplicate the current upload's bytes; the 500 MiB source budget is not a filesystem quota for all runtime data. Reference remains independently limited to 50 MiB. One process/loopback user only.

Sequence output: **1–60 slots, 1–3600 total frames at 30 fps**. Source ranges snap to that grid. Cumulative reference boundaries use the existing nearest-frame rounding/subframe merging, total duration rounds down; edits retain integer frame lengths. Too-short sources fail explicitly. Reuse/overlap/out-of-order source selections are intentional; no looping/freezing/stretching/speed change/automatic shortening. Project color settings are uniform, not camera matching or per-clip analysis. Native source playback depends on browser codec support; MP4 exports use the existing H.264/AAC format.

## Storage/API

SQLite schema **18**, sequence schema **1**, method **reference-slots-30fps-v1**, renderer **sdr-eq-mp4-v9**. Original `clips`/old plans/specs remain compatible; older renders default to no sequence. One sequence per project.

- GET/POST `/api/projects/{id}/clips`: list/stream-upload. First library upload can retain the original; subsequent sources are additional clips.
- POST `/api/projects/{id}/clips/{clip_id}`: validated display name. DELETE: explicit removal, blocked while a media job runs or saved sequence assigns it.
- GET suffix `/video`: generated storage path, source hash/size validation, no-store, framework Range support and lock through serving. No arbitrary user paths or public-directory media.
- GET `/api/projects/{id}/sequence`: empty/incomplete/ready/stale plus saved settings.
- POST suffix `/generate`: `expected_revision`, explicit `replace` for existing slots. No random/AI assignments.
- POST `/sequence`: `expected_revision`, ordered slots `{id, duration_frames, clip_id|null, source_start_frame}`. Hashes/end/output bounds are derived on the server. Duplicate IDs, invalid/too-short ranges and >120-second totals fail; revision conflict retains the client's draft.
- POST `/render`: `expected_sequence_revision` selects sequence, mutually exclusive with `expected_plan_revision`; existing recipe/audio/caption/framing revisions still apply.
- Caption Save uses `mode: sequence` and `expected_sequence_revision`. Source/timing changes preserve text and require explicit rebind; automatic proposals require current output audio/timeline. Changing color/framing does not change manual cue time.

A used clip cannot be removed until its saved assignments are replaced/unassigned/removed. Removing the original explicitly invalidates its measurements, recipe and legacy plan; other sources are not silently promoted. Upload a new original and explicitly reanalyze/regenerate before rendering again.

## Renderer and remaining limits

Each selected source interval is independently autorotated, SAR-corrected, resampled to 30 fps, graded once and framed before concatenation. Original mode chooses the original source's existing canvas and fits other sources into it; fixed modes apply saved Fit/Fill positions per clip. PCM stereo 48 kHz intermediates keep cut audio sample boundaries exact; silent sources provide silence. Saved original gains, continuous reference offset/gains, mix limiter and mute use the existing final audio pipeline. Final caption/libass rendering follows assembly/color/framing. Saved animation phase uses output time.

Existing **300-second overall deadline**, **100 MiB final output**, **120 MiB aggregate staging watchdog (100 ms polling; possible overshoot)** and bounded tool output remain. Intermediate/final copies share that staging budget; complex/high-bitrate sequences can fail the resource bound even when input quotas pass. Staging uses sequential intermediates and a final encode, so more slots cost time and incur an additional video encode. No second worker/framework. Every assigned hash and saved sequence is revalidated before publication; stale work leaves previous output intact. Unconfirmed process cleanup remains quarantined.

Saved sequence caption still/motion previews now share interval assembly with export, preserving output-time animation phase. See [automatic proposal and preview method](AUTOMATIC_ASSEMBLY.md). No live crop/waveform/professional timeline, automatic selection, per-clip grading, AI/model downloads or image mode. Physical phones were not tested.

## Actual evidence — 2026-10-07

Windows Python 3.12.14, Node 22.16.0 and FFmpeg/FFprobe 9.0.2. Generated A: 320 × 180, 4 seconds, red/green with 440 Hz audio; B: 180 × 320, 3 seconds, blue and silent. Slots: A 2–3 s → B 1–2 s → A 0–1 s. Export: **320 × 180, 3.000 seconds, 90 decoded frames**, ordered green/blue/red (portrait source padded). Middle caption 1.1–1.9 s appears only in its interval. Mono decoded PCM RMS at 0.2–0.8 / 1.2–1.8 / 2.2–2.8 s: **2046.3 / 0.0 / 2043.7**. Missing/changed hashes, stale render revisions/publication and assignment deletion are rejected. Earlier lifecycle suites cover unchanged containment/output replacement; final CI runs them all offline.

One isolated browser session used seeded offline reference analyses (not live TikTok): uploaded B and another A, created slots, selected/reused original A, trimmed, switched sections without losing the draft, saved revision 2, exported/played/downloaded the 3-second output and refreshed. A phone draft at 1.5 s survived slot switches; native selected-range preview stopped paused at 2.5 s. Refresh restored saved 2–3 s and the same completed output ID. Phone 390 × 844 had one editor column, 351.2 px, document scroll width 375 < viewport 390; desktop 1280 × 900 restored export settings/output. Browser export used saved recipe strength 50% and captions disabled; generated-media regression separately verifies sequence caption burning. Runtime media, databases, downloads and screenshots are not committed.


Saved color mode is now explicit: Original bypasses creative adjustments, Basic retains the uniform recipe, and Reference color transfer uses each clip or selected assigned-shot transform. Missing/stale used matches block export. [Routing, bounds and evidence](COLOR_TRANSFER.md).
