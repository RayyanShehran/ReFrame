# Original lettering overlay feasibility — 2026-10-16

## Decision

**Keep the experiment developer-only; do not integrate an overlay into automatic generation.** Short clear and multiline intervals preserve recognizable original shapes better than the existing text approximation, but lose strokes/edge quality. Automatic regions miss genuine lines and the closing fade. Neither clean separation nor complete detection is established across the retained reference. No full extraction/export is justified by this evidence. Existing caption review/recovery and editable text remain unchanged.

This investigation answers three different questions:

| Area | Finding |
| --- | --- |
| Reading words | Unchanged. The preceding whole-reference check remains partial: 39 correct / 0 wrong / 36 missing evaluated words, plus unobserved intervals. No OCR or handwriting inference ran here. |
| Typeface reproduction | Repeated shapes suggest reusable glyphs, with moderate visual confidence; exact typeface identity remains unresolved. Patrick Hand is an available licensed approximation, not an identification. |
| Original lettering pixels | Useful local proof on selected intervals; estimated alpha has visible defects. Automatic region coverage fails. Not a supported product path. |
| Full-reference recreation | Incomplete. No new full WITH-captions export, no new whole-reference coverage claim. Image mode remains 0%. |

## Actual lettering and editable-text comparison

Retained reference: `@henriorfedits/video/7670291844579740950`, 39.64 seconds, 1920 × 1080. SHA-256 `303e0de877b9a90c9efae0a9c01a0b4d4c5099c6118fb13bc89577ba7f3f5e80`. Both retained reference records share these bytes; they are not independent test sources. No TikTok request occurred.

Native decoded frames inspected at 2, 6, 10, 16.5, 22.5, 27.5, 29.1, 33, 38.8, 39.4 and 39.53 seconds. Enlarged manual crops compare the two `know` words and repeated `you` at 2 seconds; the same opening at 6 seconds adds a third line. Later `you`, open bowls, long stems and heart-shaped dots on lowercase `i` retain similar construction despite different backgrounds and compression. This is consistent with a font, modified font or reused lettering assets. It does **not** establish the authoring method; custom handwriting/copying cannot be excluded. Animation, rasterization and compression make exact outline comparisons unreliable. No exact font name is claimed.

The existing catalog is default DejaVu Sans plus 18 pinned candidates, including Patrick Hand Regular. Patrick Hand's local SIL OFL 1.1 license and pinned hash remain in `backend/fonts/candidates/`; no new font was downloaded/bundled. The protected, reviewed `Boys you meet.` calibration selects Patrick Hand (revision 3). Earlier glyph-mask scores instead ranked Amiri Bold 47.019, Crimson Text Bold 42.476, Barlow Condensed Bold 40.058 and Poppins Bold 39.988: these heuristic scores are not identity evidence or calibrated confidence. The reviewed handwriting-like choice is a practical approximation, not the proven closest font in existence.

Actual saved proposal styles were rendered through the existing ASS text renderer on the same 1920 × 1080 background, without saving anything:

| Sample | Existing saved approximation | Observed comparison |
| --- | --- | --- |
| Opening, 2 s | DejaVu Sans, size 1.875% of canvas height, #EEC80F | Small, conventional sans-serif block; source lettering is much wider/taller and has distinctive irregular stems/dots. |
| Three lines, 22.5 s | Patrick Hand, 3.698%, #E8C561 | Rounded handwriting approximation; source is wider, thinner and less regular. The saved render is substantially narrower and nearer the bottom. |
| Ending, 33–39.5 s | DejaVu Sans, 1.898%, #F0C712, no animation | Different shapes/scale; saved appearance has no observed closing fade. |

These are actual saved settings at the reference canvas, not hand-fitted best-case font comparisons or a new app export. Font identification, fitting/placement and fade estimation are separate deficiencies. Missing words do not establish that the font is unavailable. No saved style or calibration was replaced.

| Intended mode | Practical path |
| --- | --- |
| Recreate reference captions | Original-pixel overlay could retain original words, glyph shapes and layout if complete automatic regions and clean mattes can be demonstrated. Present prototype is inadequate. Editable text remains approximate but supports correction. |
| Caption my audio in the reference style | Needs text rendering with an identified/licensed approximation. Original pixels cannot spell arbitrary new words. Overlay extraction does not fix transcription/OCR. |

## Bounded developer probe

`backend/caption_overlay_probe.py` is an isolated CLI, with no production caller, route, saved setting or database migration. It reuses existing process containment, monotonic deadline, tool-output bounds, digest validation and temporary-cleanup helpers. Dependencies are the already installed `visual` extra (Pillow/NumPy), not a new model.

Method `local-median-color-projection-v1`: estimate a local background B with a 15-pixel spatial median; estimate one foreground color F from the observed region using the existing glyph-color estimator; project C−B onto F−B to estimate alpha. Reject large residuals and foreground/background color collisions, retain soft edges near strong seeds, optionally reuse support from an explicitly selected clear calibration frame. RGB is encoded SDR, not a physically calibrated linear-light matte. Output straight RGBA uses the estimated F and alpha, never a rectangular copy of the reference background. It preserves observed shape/layout where separation works, not every original RGB pixel or multicolor shading.

All parameters were fixed before evaluating the intervals and additional style. There is no yellow-only production rule, known-text mask or per-example threshold tuning. Background texture matching the foreground can survive; median filtering can erase thick strokes or mistake scenery for letters. Calibration support does not establish ongoing visibility, and no motion tracking is implemented. Flattened/compressed C does not uniquely determine F, B and alpha. This is **not pixel-perfect recovery**.

Bounds per invocation: ≤1.2 seconds / 36 frames at 30 fps; source ≤50 MiB, background ≤100 MiB; source-derived canvas at most 1920 pixels on its longest edge; 120-second deadline, individual tools capped at 30 seconds/remaining deadline, 64 KiB captured output. The 192 MiB aggregate directory watchdog polls during tools using existing process controls; Python image writes check the same budget after each image. Polling/write checks can overshoot and are not a strict disk quota. Source/background hashes are checked before/after. Staging is removed only after owned processes finish; unconfirmed process cleanup retains it. Diagnostic RGBA/PNG/MP4 artifacts are intentionally retained outside project media and Git, never published as app outputs. Every report is `needs_visual_review`, even with an empty/missing region.

Example (use existing local media and a **new directory outside project storage**):

```powershell
backend\.venv\Scripts\python.exe backend\caption_overlay_probe.py <retained-reference> <new-diagnostic-directory> --start 2 --end 2.8 --region 0.22,0.30,0.56,0.17 --background <existing-footage>
```

Without `--region`, the probe uses the first existing automatic mask crop per frame; it does not prove complete multi-region coverage. `--calibration 33` explicitly selects a clear color/support frame for the fade experiment, not an automatically discovered timing. No reference-specific time/rectangle is embedded in production code or probe defaults.

## Real short-interval evidence

Windows, Intel i5-12450H CPU / approximately 16 GB RAM, Python 3.12.14, FFmpeg/FFprobe 9.0.2, Pillow 12.3.0, NumPy 2.5.3. All retained-media access was read-only. Times below include extraction/compositing/preview encoding, not model inference.

Three reference intervals were each extracted at native 1920 × 1080, 24 frames / 0.8 seconds, composited onto three existing user clips: dark concert (`088d1640…`), daylight outdoors (`95169343…`) and moving textured sunset waves (`f1ee5e45…`). The initial daylight framing is mostly shaded; a separate disclosed light control brightens the wave footage with FFmpeg `eq=brightness=0.55` **before compositing**, reusing the same extracted layers. It is transformed user footage, not another independent reference. Caption RGB is not graded. No production upload/render was invoked.

| Interval | Manual evaluation selection/calibration | Estimated foreground | Elapsed seconds, dark / outdoors / waves | Actual visual findings |
| --- | --- | --- | --- | --- |
| Clear, 2.0–2.8 s | normalized x/y/w/h .22/.30/.56/.17; first frame | RGB 240/199/22 | 21.16 / 24.12 / 25.28 | Original irregular shapes, heart dot and two-line placement survive. Thin strokes have pinholes, broken/jagged edges and noisy fringes. No broad reference rectangle is visible. Light control exposes weak contrast; this is not a clean source alpha. |
| Multiline, 22.3–23.1 s | .21/.74/.57/.24; first frame | 240/201/25 | 36.31 / 37.78 / 39.27 | All three lines retain their original geometry, substantially closer than Patrick Hand. Fine strokes/dropouts vary over the patterned source; outline quality is uneven. Background-colored contamination cannot be certified absent. No large scenery patch/rectangle is observed in these composites. |
| Closing fade, 38.8–39.6 s | .26/.45/.48/.10; explicit clear calibration at 33 s | 241/199/13 | 20.31 / 22.78 / 24.38 | Visible decline/disappearance survives with manual support, but faint strokes disappear unevenly and the final soft text is not accurately reconstructed. Warm scenery and ink are difficult to separate. The copied source fade is not an independently recovered alpha animation. |

Full-resolution PNG composites on dark, outdoors, waves and the bright control were inspected. Short silent H.264 previews and decoded first/middle/last motion filmstrips were inspected as well; small-edge shimmer/dropouts remain. There is no large-position drift in the short static caption intervals, but moving-caption suitability is unverified. Clear-interval alpha mass ranges 8,073–8,109 pixels; multiline ranges 11,651–12,864; manual fade first/middle/last masses are 4,061 / 1,372 / 0. These are transparency diagnostics, **not accuracy scores**, ground-truth opacity or proof of clean separation. Multiline mass changes also include actual source variation; they do not isolate extractor flicker.

Separate runs with automatic region selection, on the same wave footage:

| Interval | Elapsed | Automatic observation |
| --- | --- | --- |
| Clear | 37.00 s | Region present in 24/24 frames; same visible edge defects as manual extraction. |
| Multiline | 51.95 s | Region present in 24/24; all three lines included in this selected interval. |
| Fade | 30.81 s | **No region in 10/24 frames**, starting 39.267 s. Faint real lettering remains at 39.4 s. Earlier boxes also shrink and lose parts of the text; manual 33 s color/support calibration was still required. |

An additional native frame at **29.1 s** contains two upper-right lines plus a left reply. Existing automatic selection returned only the left reply `(225,501)-(851,571)`, omitting the genuine upper-right lines. At 38.8 s it selected only part of the closing text; at 39.4 s it returned none. Manual crops therefore must not be presented as a one-button result. A full-reference coverage pass was deliberately not run after these failures.

### Additional style, kept separate

The existing synthetic blue printed example `printed-final-clean/reference.mp4` was withheld from choosing/tuning this matte method. It is an existing OCR evaluation fixture, **not a newly independent real reference**. Evaluated once with fixed parameters, .3–1.0 s / 21 frames / 640 × 360, automatic regions and first-frame calibration; same three backgrounds. Estimated RGB 42/110/255; runtimes 4.86 / 5.06 / 5.00 s. Placement/color and words remain recognizable, but printed stroke interiors/edges become mottled and perforated. It does not validate generalization to bold/outlined/multicolor/Arabic captions. No parameters were changed after seeing it.

## Verification and preservation

- Focused probe tests: **15 passed** locally, including soft alpha in three colors, disappearance with fixed support, ambiguous equal-color background, invalid/unbounded intervals/regions and aggregate Python-written output bounds. Affected Ruff lint/format pass. Core CI may skip the five imaging cases when the optional visual dependencies are absent; local installed-visual checks ran them.
- Fifteen small probe invocations completed (about **386 seconds summed**); three light-control previews reuse the completed alpha layers without another extraction. Local evidence under the task's `caption-appearance` visualization directory includes source frames, repeated-word crops, RGBA sequences, reports, font comparisons, full-resolution composites and silent previews. Private assets remain uncommitted.
- Browser/media distinction: the loopback-only diagnostic page played the 0.8 s 1920 × 1080 multiline preview (`readyState=4`, `paused=false`, then reached 0.8 s/end), plus clear/dark and light-control fade previews. This verifies playable **diagnostic media**, not app save/refresh/download, full-reference timing or a captioned project export. The simple diagnostic server did not support the attempted seek; decoded frames provide midpoint/fade inspection. No product browser workflow changed.
- Logical comparison with the pre-task database backup: **every table unchanged**, both projects remain. All **16 physical clip hashes** match their saved digests. Saved correction, analyses, settings, fonts/calibration and render records therefore remain unchanged. Reference and prior playable caption-disabled output hashes remain unchanged; output hash `bd2a20d634851c30702ff62338d48b74f46cdf670ae180ac60c088757759660c`.
- No new model/dependency/font download, transcription inference, OCR run, external media upload, TikTok retrieval, full-reference extraction or forced partial-caption export. Production caption code is unchanged; exact-final-commit CI covers existing paths.

## Integration gate and remaining work

An optional supported overlay path is **not implemented**: no overlay persistence, UI switch, canvas-mapped preview/export integration or automatic method selection is claimed. Before that is safe, evidence must establish complete multi-region/timing coverage, reliable background separation through fades and collisions, acceptable motion quality and source/revision-bound artifact publication. The existing explicit incomplete-caption recovery remains necessary. Editable text can use corrected wording and approximate licensed fonts today; this probe cannot supply arbitrary new words or fix missing OCR. Do not interpret a recognizable extracted sentence as completion of the handwritten reference.
