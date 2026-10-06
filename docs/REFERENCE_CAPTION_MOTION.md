# Assisted reference-caption motion (Milestone 25)

## Workflow and bindings

Explicitly select 0.4–4 seconds of retained reference video, including a settled caption and entrance/exit where possible. A separate expanded motion rectangle preserves the saved static crop. Confirm text and review a font through the existing controls first. Valid saved appearance suggestions are reused; otherwise the current manual appearance is used. Preview the interval, Analyze caption motion, explicitly Compare, review the reconstruction, then Apply animation to draft. Save captions and use the actual footage motion preview. Analysis does not save captions or render a full export.

Schema 1 / method `temporal-glyph-fit-v1` persists one suggestion and its full bindings: reference identity/hash, selection revision/token, confirmed text, reviewed face/hash, interval, motion rectangle, appearance inputs/token and method. Database version 16 adds `caption_motion_suggestions` without changing prior records. GET/POST `/api/projects/{id}/reference-motion` reads/analyzes; POST suffixes `/preview`, `/compare`, `/apply` are explicit. Apply validates the saved revision/token and current inputs and returns only supported animation fields. Unestimated fields stay unchanged. Caption words/timing/style/font/provenance/audio/cut bindings remain intact; existing caption Save revisions outdate exports. Settings restore; comparison videos are transient. Previous successful results survive failed replacement and are labeled outdated after relevant changes.

## Method, evidence and limits

The existing normalized SDR decode handles rotation/SAR and samples consecutive frames at **10 fps**, downscaled without cropping to maximum **512-pixel edge**, at most **40 grayscale frames**. Exact decoded byte/frame counts are required. Otsu foreground masks, median foreground/background contrast, normalized glyph bounds/centers and overlap with a real reviewed-font rendering provide lightweight evidence. This is not OCR, optical flow or a statistical confidence score.

At least four readable samples (contrast ≥35/255) and three consecutive settled samples are required. Settled means ≥90% peak contrast, ≥94% peak width, ≥90% peak height and adjacent vertical movement <0.8% canvas. Every visible mask must overlap the reviewed glyph template by ≥0.55; overlap variation >0.22 is flagged. These heuristics can reject valid references or miss subtle word changes; confirmed text and visual review remain necessary.

Fade uses contrast ramps; Pop uses width growth; Slide up uses vertical settling. Static requires sufficient stable visible evidence and means **no animation observed in this interval**. Combined effects, horizontal movement, reversals, clipped/tracking-lost masks, inconsistent glyphs, strong background contamination or too little settled evidence return unsupported/inconclusive. No forced nearest family. Pop/Slide exits are not estimated. Entrance requires a preceding absent sample and measured ramp; exit requires a following absent sample and ramp. Missing evidence remains null, yielding a partial suggestion when useful supported evidence remains.

Sampling precision is **0.1 seconds**, with practical fixture tolerances **±0.15 seconds**, initial scale **±0.15**, and displacement **±0.02 canvas height**. These are test tolerances, not universal accuracy guarantees. Motion weaker than sampling/thresholds, unavailable fonts, complex backgrounds, rotation/bounce and word-level effects require manual controls. No arbitrary-reference or Arabic motion benchmark was performed.

## Real reconstruction and bounds

The selected reference and neutral-gray reconstruction are real silent MP4s, using the existing libass subtitle renderer and reviewed text/font/style. Subtitle rendering uses the full reference phase before trimming/rebasing; an interval beginning mid-entrance does not restart it. Unobserved effects are neutral in reconstruction, explicitly labeled; Apply leaves those draft values unchanged. Existing short-cue duration rules still apply after saving.

Shared media ownership, cancellation joins, containment, source revalidation and cleanup/quarantine are unchanged. Each operation has a **30-second monotonic wall deadline**, finite frame work, two decode threads and one filter thread; this is not a separate OS CPU-time quota. Staging uses the existing **16 MiB aggregate watchdog**, polled every **100 ms**, permitting overshoot rather than enforcing a filesystem quota. Captured diagnostics are bounded (64 KiB per tool capture). Sampling has ≤10 MiB raw frames, removed before reconstruction. Each comparison video is ≤**3 MiB**, ≤4 seconds, ≤512-pixel edge and ≤120 frames; real decode verifies the full frame count and silence. Two video payloads have at most 8 MiB combined base64 plus bounded schema metadata/40 observations. Deadline, size, decoding, stale binding and cleanup failures are explicit. No new dependency, worker framework or full-export renderer change.

## Focused verification — 2026-10-06

Windows Python 3.12.14 / FFmpeg 9.0.2 generated 640 × 360, 30 fps, 2.4-second known-Anton references. Cue: 0.3–2.1 seconds; entrance/exit 0.4 seconds; Pop initial scale 0.6; Slide displacement 0.08 canvas. Clean measurements:

| Fixture | Result | Measurement / error |
| --- | --- | --- |
| None | Static | Stable readable text; no invented boundary duration |
| Fade | Supported | Entrance 0.397 s / exit 0.382 s; errors 3 / 18 ms. Observed onset/end 0.2693 / 2.0576 s; errors 31 / 42 ms |
| Pop | Supported | Entrance 0.4 s; initial scale 0.636, error 0.036 |
| Slide up | Supported | Entrance 0.4 s; displacement 0.073, error 0.007 canvas |
| Combined Fade + Pop | Inconclusive | No animation patch |
| Fade interval ending at 1.5 s | Partial | Exit/end remain unestimated |

Real reference/reconstruction comparisons decoded **72 frames / 2.4 s**, with no audio; a 0.4–1.2 s selected interval decoded 24 frames. Foreground energy at 0.1/0.4/1.0/1.9/2.2 s was reference 0/20000/78975/40085/0 and neutral reconstruction 0/10567/40848/17808/5, verifying absent/rising/settled/falling/absent phases rather than process exit alone. Sizes were 4,461 / 4,400 bytes in the manual-style fixture. Saved valid appearance reuse, source mutation, font/interval/region staleness, finite validation, Apply-only fields, prior-result preservation and staging cleanup are covered by focused integration checks.

One production-build browser workflow used this generated Fade and explicitly synthetic stored color measurements: select → analyze → real comparison/play → reviewed Apply → Save caption revision 2 → actual footage motion preview/play → refresh. User words “My own words”, 0.3–2.1 s timing, Anton/yellow/manual position and SRT-import provenance stayed intact. Saved Fade is 0.397/0.382 s; unestimated scale 0.7 and displacement 0.08 stayed unchanged. Valid saved appearance was reused. Own-footage preview decoded 66 frames / 2.2 s / 640 × 360. Refresh restored values/bindings/captions; transient videos required explicit regeneration. Phone 390 × 740 and tablet 768 × 900 had no horizontal overflow; section switches preserved drafts/results. Physical touch devices remain unverified.

Local checks: four new backend integration checks across development; affected migration/source checks; Ruff lint/format; 17 frontend tests across motion/editor/animation/font components, affected ESLint, typecheck and production build. One inherited real animation test was rerun to correct a one-pixel glyph-box tolerance exposed by Linux CI antialiasing/compression; subtitle rendering did not change. First two CI runs failed only that inherited assertion; the UI commit passed all CI jobs. Final-commit full CI is reported in the handoff. No TikTok, OCR rerun, transcription, model downloads or new full export.

Related: [saved animation method](CAPTION_ANIMATION.md), [suggestion schema](REFERENCE_CAPTION_MOTION.schema.json), [FFmpeg filter reference](https://www.ffmpeg.org/ffmpeg-filters.html), [ASS tags](https://aegisub.org/docs/latest/ass_tags/).
