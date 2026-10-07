# Reference color transfer and shared footage upload

## User workflow

Both first-footage and Add footage entry points expose **Choose videos** and accept multiple MP4/MOV files or drops into the same queue. Help and the queue are separate blocks; the hidden native input no longer competes for flex width. Uploads are sequential. Queue ownership survives the first success/readiness update, stops further submissions after project switching, preserves successes, retries failed items only, and allows pending removal. Retained plus queued clips count against 10 clips / 500 MiB; the unchanged server also enforces 100 MiB per file. Queues are temporary and clear on refresh; uploaded clips remain.

Prepare the retained reference and original-footage color measurements, then **Match reference colors**. Preparation does not activate a grade. Choose and explicitly save one mode:

- **Original / no color adjustment:** bypass creative color processing; no reference or recipe required.
- **Basic adjustments:** existing saved brightness/contrast/saturation recipe. Existing projects default to this mode and retain old behavior.
- **Reference color transfer:** separate prepared fixed transform for every used clip. Strength defaults to 50%; review before saving/exporting.

Select a clip to refine strength, shadow/midtone/highlight brightness, shadow/highlight red/blue casts and saturation. Reset changes only that draft. Previously edited controls survive explicit rematching. For a saved sequence slot with a reference interval, **Match assigned reference shot** prepares its selected footage range against that interval; explicitly choose the assigned-shot match. Otherwise the clip's project-reference look is used, including unassociated slots. Repeated slots reuse the clip transform unless explicitly assigned their own match.

The comparison shows Reference look / Original / Graded for the selected clip or slot and source time. It uses saved settings, not a live slider/CSS preview. Save changes before preview/export; prior images/output remain visible and are marked outdated when appropriate. Saved mode, per-source controls, matches and video restore after refresh; still images intentionally do not.

## Method and limits

`regularized-encoded-tone-chroma-v1` is a deterministic local **statistical SDR approximation**, not AI grading, recovered creator LUTs, exposure recovery or white-balance estimation. It requires no model or network request. Existing blueprints remain immutable. The existing ordinary encoded-RGB SDR policy rejects identified HDR; absent metadata assumes BT.709/limited YCbCr and cannot reliably identify untagged HDR. No tone mapping.

Twelve midpoint frames across the whole source or explicitly assigned interval are decoded at 64 × 64 through the existing sampling/color policy. A stratified 32 × 32 grid yields at most 12,288 pixels per source. Persistently near-black (all sampled channels ≤8/255) contiguous outside rows/columns across all frames are excluded, at most 16 pixels per edge. This heuristic can mistake consistently dark scene edges for bars, or miss nonblack/variable bars. It is not semantic segmentation.

A nine-knot encoded-luma curve uses seven interior quantiles, ≥0.025 knot spacing, 70% movement toward reference quantiles, bounded ±0.15 displacement and monotone slopes 0.25–2. Smooth shadow/midtone/highlight weights support tonal casts. A regularized 2 × 2 opponent-chroma covariance mapping (R−Y, B−Y; ridge 0.0025) adjusts chroma spread/orientation with diagonal 0.75–1.35 and cross terms ±0.25. Tonal cast differences are bounded ±0.08, with sparse-bin estimates shrunk toward the global mean. Flat/low-chroma inputs use bounded offset/neutral matrix fallbacks. Out-of-gamut chroma is compressed together rather than independently clipped; refinements are bounded. Final RGB blends with the original at strength 0–1.

The saved model/refinements produce a server-owned 17³ / tetrahedral 3D LUT (about 147 KiB). **Strength zero bypasses the LUT and RGB conversions entirely.** The same LUT/filter builder serves preview, whole clip, legacy cuts, multi-clip segments and caption still/motion previews. Processing order: source display normalization/selection → exactly one selected creative grade → framing → captions on the final canvas → H.264 encoding. Transfer explicitly converts full-range RGB to limited BT.709 YUV; Basic is never stacked onto it. Audio and cut/caption timing bindings remain unchanged.

One shared media worker, five-minute overall deadline, 60-second capped sampling stages, 16 MiB aggregate staging budget, bounded raw output (147,456 bytes/sample set), at most 10 clip + 60 shot matches, owned-process containment, cancellation/recovery/deletion and cleanup checks reuse existing components. Polling budgets allow bounded overshoot, not a strict filesystem quota. No per-frame refitting: each route has one fixed transform, avoiding adaptive flicker. This does not remove source flicker or compression artifacts.

Different scene content biases distributions; skin/objects are not recognized. A red scene versus a blue scene can suggest unwanted casts. Twelve samples can miss brief events. Saturated/clipped footage cannot recover lost detail. Fixed tone/chroma statistics do not reproduce arbitrary selective or spatial grades. Review different moments and the actual export; color-distance improvement alone is not perceptual-quality proof.

## Persistence/API

Additive database schema **20** preserves older projects/results: `grading_settings`, `color_matches`, `grading_operations`. Settings/matches/snapshots have schema version 1 and algorithm version above. `GET /api/projects/{id}/grading`, `POST` to save (`expected_revision`, mode, controls, shot_slots), `POST /prepare` (`expected_revision`, replace, optional shot_slots / expected_sequence_revision), and `/cancel` use the existing operation/polling envelope.

Server preparation reads validated media identities, hashes, reference color analysis version/operation and exact selected ranges; client-supplied models/hashes are not accepted. Saved settings require finite bounded controls and optimistic revisions. Hash/version/reference-analysis changes invalidate applicable matches; slot-association/range changes invalidate that shot only, while unchanged project clip matches survive. Preparation failure preserves settings/prior matches; explicit retry/replacement is required. Worker publication revalidates sources, saved revision, project/operation ownership, cancellation and deadline.

Render specifications bind saved mode/revision, exact models/controls and all used source/shot identities. Old specifications without grading keep Basic semantics. Changed saved controls/matches or missing/stale required routes prevent reuse/new export, with previous output retained until atomic replacement succeeds. Original can render independently of color analyses.

## Verification — 2026-10-07

Local Windows FFmpeg **9.0.2 essentials**, Python 3.12.14; generated 320 × 180 / 30 fps / 3 s moving `testsrc2` with 440 Hz AAC, second source brightness −0.08 / contrast 0.9 / differing red-blue balance, and a controlled warm reference from the same source. These deterministic same-content variants test transform/routing, not artistic quality.

| Actual check | Result |
| --- | --- |
| Separate source/assigned-shot fits | Two project clip models plus one assigned-shot model; different balances produce distinct models; repeated slots reuse their clip route |
| Strength 0 / 50 / 100%, decoded frame difference from original | **0.000 / 2.441 / 3.243** mean RGB levels out of 255 |
| Preview versus independently encoded whole export | **1.349/255** mean absolute RGB error; test permits <5 for lossy encoding |
| All three actual sequence routes versus their independently decoded previews | **1.669 / 1.961 / 2.034 RGB levels** MAE: first clip, assigned-shot second clip, repeated first clip; outside caption intervals, <6 tolerance for two H.264 passes |
| Moving footage, same static patch at 0.2 / 0.4 / 0.7 s | **RGB (94,8,0)** at all three inspected moments; fixed transform, no adaptive refitting |
| Whole / legacy cut / multi-clip output | Ready; legacy 2 s / 60 frames; sequence 3 s / 90 frames with original audio and saved caption 1.1–1.9 s |
| Assigned-shot range edit | Only assigned-shot match stale, two project matches valid, old output retained/outdated, new stale render rejected |

One isolated desktop/390 × 844 browser session used actual **Choose videos** controls: first chooser `multiple=true`, two selected MP4s sequentially succeeded across first-upload readiness; add-more chooser `multiple=true`, third file succeeded. Saved transfer at 51% produced a 3.000 s / 320 × 180 MP4, native player entered playing state and download completed. Refresh restored all three clips, prepared models, mode/strength and saved output. Phone document widths 375 px during export and 390 px in the restored library: no overflow.

A different-content pair used the warm moving test pattern as reference and a coffee photograph clip: [Cup Coffee](https://commons.wikimedia.org/wiki/File:Cup_Coffee.jpg), ProjectManhattan, CC0 1.0 (author/rights on source page). Inspecting its reference/original/graded frames demonstrates routing across different content, **not** reliable style matching. All verification media, generated LUTs, databases and caches remain outside Git. No TikTok, transcription or model download was used.

Focused algorithm/API/renderer tests cover identity-neutral behavior, flat/black/clipped finite bounded output, low variation, numeric validation, stale revisions/sources, measured-analysis preservation, old Basic defaults, per-source/shot routing, strength, real preview/export agreement and audio/caption timing. Existing lifecycle tests remain authoritative for unchanged containment/deletion. Exact-final-commit CI results are reported in handoff.
