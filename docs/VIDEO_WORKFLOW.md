# Coordinated video workflow (Milestone 26)

## Explicit preparation

After saving a project and uploading footage, **Prepare reference and footage** lists reference retrieval, reference color, footage color, and opt-in reference pacing before starting. The session-only coordinator calls each existing operation hook sequentially; hooks retain their existing status requests, bounded polling, retry and backend shared worker. Ready server-validated results are reused. No new queue, persistence schema or media algorithm is introduced.

Failure stops subsequent launches. **Retry preparation** keeps successful results and starts only missing/failed work. **Stop after current step** prevents subsequent launches; it does not cancel the running backend job. Project change/unmount/reload revokes continuation; reopening restores backend status and requires explicit **Continue preparation**. An already-running failed operation stops the adopted sequence rather than automatically retrying. Double clicks and obsolete status responses cannot launch duplicate steps.

Recipes, plans, words, fonts, appearance/motion acceptance, OCR, transcription and rendering remain separate explicit actions. Optional pacing/matching/recognition failures preserve the basic whole-clip route and manual controls. Guidance uses existing render blockers. Source-availability changes refresh audio/caption/reference metadata without replacing unsaved drafts. Framing inspection errors explain their limited role: Original/Fit remain usable; inspect current footage before positioning Fill.

## Optional readiness

**Check optional features** explicitly reads project-scoped `/optional-features`. It reuses the contained preview operation and existing libass/default-font, transcription manifest/dependency and OCR capability checks. No recognition, inference, model download or video encode is performed. Shared-worker contention, deadline and cleanup errors stay explicit. Checks run on request, not every panel update. Transcription readiness checks manifest sizes and installed versions, not model inference; selected fonts/glyphs remain separately validated by Save/render. Low-level setup/version information is collapsed.

## Reproducible workflow evidence — 2026-10-06

Windows/Python 3.12.14 and FFmpeg/FFprobe 9.0.2; one production-build browser session, isolated empty local storage. A test launcher outside the repository replaced **only upstream metadata and retrieval** with a visibly labeled generated reference. Normal project creation, upload, retention/hash validation, analysis, settings, render, serving and download paths were exercised. The fixture decoder verified 12,288 RGB bytes and 16,000 audio samples. No production fixture bypass/reference upload was added; no TikTok, OCR/transcription inference or asset download occurred.

Reference: 640 × 360/30 fps/4 seconds, generated green/blue footage with known baked Anton text/fade and 880 Hz audio. Uploaded footage: 640 × 360/30 fps/6 seconds, moving test pattern and 440 Hz audio. Pacing yielded zero cuts/one shot for the modest color change; this illustrates threshold limitations, not proof of multiple detected cuts.

1. Create **M26 complete local workflow** → upload → prepare three required steps sequentially. Review generated recipe and save selected brightness −0.20, contrast/saturation 0.80, strength 50%, revision 2.
2. With cuts/captions disabled and default original audio/framing, render/play/download **640 × 360, 6.000 seconds**, H.264/AAC. Native playback readyState 4, playing, no reported media error. Output `728742ec-0bd8-4264-8ea0-b7c8c9b8444a`.
3. Opt into pacing → Continue; all three required steps reused, only pacing launched. Explicitly generate/review/save plan revision 2 selecting footage **0.5–4.5 seconds**, a single trimmed output segment. Multiple-cut behavior is covered by existing render fixtures, not claimed for this session.
4. Manually review/save **My local caption**, 0.3–2.1 seconds on cut-plan revision 2; Anton Regular, white, 7% height, bottom-center, Fade 0.25/0.25 seconds, caption revision 1. Inspect reference at 0.8 seconds; actual saved-caption still and silent motion preview succeed. No matching suggestion is automatically accepted. Prior assisted matching/recognition evidence is retained.
5. Save Mix **70% original / 30% reference**, reference offset 0.25 seconds (audio revision 1); save Square/Fit **720 × 720** (framing revision 1). Prior basic output remains available as outdated until successful replacement.
6. Render/play/download **720 × 720, 4.000 seconds**, H.264/AAC, output `cfb20463-4aed-4426-8c40-fb2932ca90ae`. Full audio decode yields **64,000 mono 16 kHz samples**. Decoded frames at 0.1/0.9/2.3 seconds show captions absent/present/absent, drawn on the padded final canvas. This checks integration, not a new quantitative mix-level benchmark.
7. Refresh and reopen via saved-project list restores recipe 2, plan 2, audio/caption/framing 1 and the same playable output. Saved words/font/Fade restore. Transient previews/readiness require explicit requests; render-mode draft defaults to whole, so select saved cuts again to match bound captions. No automatic creative operation or preparation continuation occurs.

Phone **390 × 740** and desktop **1440 × 900** workflow checks: document widths **375/1425 px**, equal to scroll widths; no page-level horizontal overflow. Native compact navigation, playback and download were exercised; browser sizing is not physical iPhone/Android verification. Screenshots and downloaded outputs are retained outside synced project sources. Disposable servers/storage are removed after verification.

## Checks and limits

Focused tests cover sequential reuse, failure/retry, double-click ownership, stop-after-current, project/unmount/late-response handling, adoption of running operations, optional-failure/basic-export independence and source refresh preserving drafts. Affected frontend tests/lint/typecheck and production build passed; two capability backend tests/Ruff passed. Final CI runs all existing frontend/backend/spike gates, including inherited media/lifecycle cases. Exact-commit result is recorded at handoff.

Local single-user operation, supported SDR/media/font/effect bounds and existing deadlines remain. Capabilities are setup signals, not universal compatibility. Experimental TikTok reliability/reuse rights, physical phones and production deployment remain unresolved. Matching remains assisted/limited; image mode remains unimplemented.
