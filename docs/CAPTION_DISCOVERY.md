# Staged caption discovery and recognition — 2026-10-16

## Result and acceptance

**Full-timeline observation improved; reliable full handwritten recreation is not accepted.** Supported printed/multiline/bilingual fixtures still complete automatically. One fresh whole retained-reference run observed **81/81** discovery frames, but emitted wrong text and fragmented intervals; its saved proposal required restoration. That is **failed verification**, not completion. The final recognition guard has targeted real-interval and supported-export checks, **no subsequent fresh whole extraction**. No handwritten captioned export was forced. Overlay work remains isolated and production does not use it. Image mode: **0%**.

Keep separate: sampled observation coverage, word recognition, approximate appearance and a successful encode. None proves the others.

## Measured bottleneck

The baseline `e2c3231` reader was profiled on the retained handwritten **27–30 s** interval and the supported blue/white printed **0–1.5 s** interval. Source/caches/project metadata were copied into isolated evaluation storage; no evaluation answers entered production. cProfile adds substantial Python-loop overhead: these are measured same-harness profiles, not general speed benchmarks. Stage times below overlap where one function calls another.

| Profile stage | Handwritten baseline → decode/vector optimization | Printed baseline → optimization |
| --- | --- | --- |
| Complete detector call | 184.64 → 75.64 s | 20.66 → 8.64 s |
| Frame decode processes | 92 / 30.19 s → 34 / 12.58 s | 56 / 6.22 s → 22 / 2.50 s |
| Region scans | 92 / 53.03 s → 34 / 26.95 s | 56 / 8.22 s → 22 / 2.94 s |
| Component segmentation, included above | 1,075 / 25.76 s → 408 / 13.57 s | 436 / 4.44 s → 174 / 1.79 s |
| New OCR retry processes | 55 / 17.14 s → 57 / 16.48 s | 6, including context → 6 |
| New handwriting inference | **0**; 33 cached inputs consumed in both | **0**; 3 cached inputs consumed in both |
| Requested/observed discovery frames | 7/7 in this short diagnostic | 4/4 in this short diagnostic |

The baseline profile also spent over **50 seconds of recorded internal time in bytewise comparison generator/min loops**; the same normalized byte metric now uses installed NumPy, with an equivalent standard-library fallback for core-only tests. A native image is decoded once per expensive frame read and reused for visibility, resizing and crop OCR. Empty component rows are skipped without joining components across genuine gaps.

The earlier whole-reference failure observed only **39/81** points under the old approximately 100-second usable frame-reading window. Discovery was interleaved with crop OCR, context retries and repeated decoding/preprocessing, so later observations competed with expensive readings. **TrOCR was already batched**; per-frame new handwriting inference was not the measured dominant cost. No replacement model/download was justified by these runtime measurements.

The initial short profiles return detector proposals; the normal whole run below additionally measures actual source/snapshot-validated publication: **185.69 s detector / 187.28 s through the persisted failure/proposal**. No simulated publication is counted as a normal extraction.

## Final method: visible-tsv-adaptive-v17

1. Observe the **entire requested timeline before OCR/model recognition**. Decode lossless thumbnails on the normalized 30 fps output timeline at **0.5-second spacing plus the last frame**, at most 960 pixels on the longest edge, without upscaling. At most 241 grid points for a supported 120-second reference. Four RGB thumbnails per owned batch keep even square-frame raw PNG data around 11 MiB plus small overhead under the existing 16 MiB staging watchdog. Source-video timestamps are copied and the video origin is subtracted; seeking starts before the requested frame so a low-fps last visible image is not discarded. A real 12 fps color fixture verifies boundaries/tail across multiple batches.
2. Run existing multicolor/neutral glyph detection and line grouping, without OCR or model calls in discovery. Candidate geometry, layout and compact glyph signatures are recorded separately from wording. More than three candidate lines remains an explicit unsupported-region blocker. Detection has at most **75 seconds**; incomplete discovery still blocks completion. A complete grid means pixels were examined at that resolution, not that every caption was detected.
3. Group similar observed line shapes within the existing local temporal/geometry constraints. Select two actual frames per distinct group when available, spread recognition across the timeline, and reuse compatible source/tool/input-bound readings. The tested whole source chose **41 recognition frames instead of recognizing all 81 independently**. Expensive native reads use the existing remaining stage/request bounds; completed observations remain available even when recognition stops.
4. Existing OCR, bounded optional handwriting batching, recovery and timing refinement use the remaining budget. No new worker, persistent cache framework or database table. Existing 300-second overall deadline, 600 new requests, at most 16 new model inputs / 96 selected inputs, 8 MiB crop memory and 16 MiB staging, bounded captures, cancellation/containment, source/revision checks and atomic previous-output preservation remain. The watchdog can overshoot and is not a strict filesystem quota. Missing wording or discovery stays explicit; completion thresholds were not relaxed.
5. Mask selection now scores the **whole observed block**, rather than one strongest line. At the actual **29.1 s** failure, a slightly stronger single-reply mask previously beat the mask containing all three real lines. Native selection now includes `(225,265)–(1905,572)` and all three lines. Crop/block grouping supports line-height ratios up to 3:1 with a regression. Neutral/chroma detection is not tied to yellow or a fixed location; incidental/creator-text classification still happens separately after recognition.
6. Previously checked regions can supply words through a fade only while the existing strict glyph comparison supports them, including when current mask detection vanishes. This does **not** solve the real final fade: at 39.4 s the standalone region detector still misses faint visible letters. Direct real signatures at 38.8 versus 39.4 have similarity about **0.754**, below the unchanged 0.9 recovery rule. Warm scenery/quantization contaminate the mask; no words are invented to bridge it.

Additive optional evidence fields record `discovery_seconds` and `recognition_frames`. Older bindings v1–v16 remain readable; no migration discards prior results/settings.

### Late recognition correction and its verification boundary

The one whole v16 run showed confident Tesseract `66 any o at` and `tool` in genuine caption regions. Native crop inspection at 27, 29 and 29.5 s confirmed that compatible cached TrOCR context readings already contained partial/correct alternatives, including `any of that anyway` and `...okay, too`. The context-agreement shortcut skipped those independent facts. Source geometry still clipped initial words in some frames; high token scores alone did not establish correctness.

v17 reuses an already cached handwriting **context** even when OCR/context OCR agree, without requiring a new inference for an otherwise confirmed OCR line. Credible disagreements remain unresolved; unsupported/conflicted wording is withheld from fresh cues. It never changes `tool` to `too` by a dictionary/known reference sentence. One generated real-media regression gives the two readers deliberately conflicting texts and verifies cached cross-check consumption, **zero new model requests**, partial status and no silently chosen wording.

The final 27–30 s real diagnostic observed **7/7 frames in 2.047 s**, selected six recognition frames and completed in **94.53 s under cProfile**: 58 new OCR calls, zero new model calls, 24 cached OCR / 27 cached model inputs. Its proposed lines have **10 correct / 0 wrong / 4 missing** evaluated words; the disputed last reply is withheld. Eight unresolved groups plus the diagnostic-only unobserved-whole-track marker remain. Timing is fragmented and a stray punctuation mark remains. This is safer targeted behavior, **not proof of a complete final handwritten track**. No second whole extraction or publication replay was run after restoration. The last four-frame batching bound and unequal-line grouping received affected tests, not another whole media benchmark.

## Existing evaluation set

C/W/M is correct / wrong / missing normalized words; case/punctuation are ignored. Printed counts cover expected cues; handwritten counts choose the best overlapping observed line (token similarity ≥0.6), exclude the protected manual `Boys you meet.` correction and genuine title, and **do not measure continuous timed accuracy**. The same line metric reproduces the earlier v14 **39/0/36** result. Runtime/cache counts are from real runs, not cold-inference benchmarks. Before values are historical v14 evidence; databases/caches differ between fixtures and the real project.

| Example | Historical v14 C/W/M; decision | v17 C/W/M; decision | Observed grid | Missed phases / false cues; unresolved groups | Seconds; new OCR/model; cached OCR/model |
| --- | --- | --- | --- | --- | --- | --- |
| Blue/white printed | 6/0/0; complete | 6/0/0; complete | 7/7 | 0 missed / 0 false cues; 0 groups | 9.38; 4/0; 3/3 |
| White/red multiline | 8/0/0; complete | 8/0/0; complete | 7/7 | 0 missed / 0 false cues; 0 groups | 7.06; 2/0; 5/0 |
| Arabic/English | 4/0/0; complete | 4/0/0; complete | 7/7 | 0 missed / 0 false cues; 0 groups | 6.61; 1/0; 3/0 |
| Plain sunset | 0/0/0; no_captions | 0/0/0; partial | 13/13 | N/A / 0 false cues; 1 groups | 26.23; 38/0; 40/0 |
| Sunset + incidental label | 0/0/0; partial, 2 groups | 0/0/0; partial | 7/7 | N/A / 0 false cues; 1 groups | 15.62; 22/0; 32/0 |
| Three-cut multiline | 8/0/0; complete/export | 8/0/0; ready | 7/7 | 0 missed / 0 false cues; 0 groups | 12.75; 3/0; 5/6 |
| Whole handwritten v16, **failed** | 39/0/36; partial | **74/1/0; partial**, favorable best-line metric only | **81/81**, previously 39/81 | Many within-phase gaps/wrong fragments; 25 groups | 185.69 detector (187.28 worker); 55/6; 48/63 |
| Handwritten 27–30 s, final v17 diagnostic | v16: 13/1/0 best-line words; partial | **10/0/4; partial** (reply withheld) | 7/7 within diagnostic | Reply missing; no invented cue; 8 groups + diagnostic marker | 94.53 profiled; 58/0; 24/27 |

The no-caption and incidental sources have **zero false proposed/exported cues**, but still have false blocking regions; the plain-scenery completion regresses from historical `no_captions` to `partial`. This is an unresolved classification failure. The incidental version derives from the same sunset footage; it is not an independent reference. No threshold was lowered or special scenery exception added to force completion.

### Independent existing real footage

An already uploaded, independently captured road/hiking video contains white two-line captions over moving scenery, separate from the handwritten source. Local authorized evaluation uses only **0.5–3.5 s** of `clip-641cddf657584e3fa624f5fe761fb612.mp4`, SHA-256 `4a0b479184400f4202f629af56a1448cece62272b947a430cdef4d658f76033d`. Visual source frames establish the seven words `People can't walk around / the world being`. An isolated reference fixture returned all seven correctly with zero extra cue in **4.83 s**, **7/7 observed frames**, 0.672 s discovery, two selected frames, two new OCR calls and **no handwriting inference/cache consumption**. A CapCut mark was visible separately. No media was fetched or uploaded externally.

This was a bounded v16 diagnostic, before the final guard; it deliberately returns partial because the rest of the **42.67 s** video was not examined. No claim about its full captions/timing/export or universal real-reference support. Its local fixture uses a synthetic test reference record; it must not be attributed to that fixture's documentation-sample TikTok URL. Synthetic examples remain synthetic; this one additional real interval is limited generalization evidence, not a broad benchmark.

## Whole handwritten run, restoration and output

Exactly **one normal Generate request** used the actual retained handwritten reference and compatible caches: operation `aaf7ee60-fc4c-45fe-a159-3eb7d89b3fa5`, method v16. It observed **81/81** discovery frames in **13.92 s**, versus historical 39/81. It used **55 new OCR / 6 new model inputs**, consumed **48 cached OCR / 63 cached model inputs**, and retained 69 completed model results including cached ones. Total detector **185.69 s**, normal worker/proposal publication **187.28 s**. Source/revision/cleanup safeguards remained active.

Best line presence improved **39/0/36 → 74/1/0**, but the extra `l` in `tool` is a real wrong word, and this favorable line-presence count hides bad `66...` wording in an earlier phase. Entire/partial intervals still drop out, including opening gaps, the serious reply, promise-line gaps and early closing-fade cutoff. Some boundaries are coarse or extend past visible text. **25 unresolved groups** remain: 18 unreadable, 4 conflicting and 3 timing groups. There were no invented unrelated caption subjects, but incorrectly recognized fragments in real captions are still wrong results.

Publication replaced parts of the valid prior proposal. The failed result and fresh readings are retained in local evidence. After verifying unchanged source identity and generation snapshot, a revision/operation-checked restoration restored the **exact prior 18-cue review object**, including original observation metadata and the manual correction; generation revision 56 records the restoration notice. No cues were newly marked verified, and no result replay masked the failure. **A run needing restoration is failed verification.** Final v17 has the targeted checks above, not another fresh whole pass. The actual project stays partial, with retry/correction/explicit Off and incomplete-caption acceptance safeguards unchanged; it is **not now exportable with certified complete automatic captions**.

## Supported export and preservation

The three-cut example completed normal **Generate → fresh extraction → saved automatic_reference track → new captioned export** in **12.75 s** on v17, without manual Apply/Save, injected recognition text or incomplete-caption acceptance. Output `8f0f74a1-4adc-44d2-a36f-af5b03afb89e`: 640 × 360, 3.000 s, 90 decoded frames, 143,960 decoded mono 48 kHz audio samples. SHA-256 `19c51279cfa17d0bcccb70319b49bf70dfbb057482c660f2698ea1a1be846dc5`, byte-identical to the inspected v16 export. Decoded 0.1/2.833 s frames have no captions; 0.5 s has both upper white lines and 1.8 s both lower red lines. Rendering, cuts and audio code did not change.

Real API checks on those identical bytes: **206 bytes 0–1023/63092**, **200 attachment** download, matching SHA-256. This follow-up supplies fresh extraction/render/API/media evidence; it does **not** claim a new browser walkthrough. Prior browser playback/download/refresh evidence for unchanged serving is retained separately in AUTOMATIC_GENERATION.md. No manual correction is included in the synthetic export. The real project's protected manual correction is in its restored review, not new recognition credit.

Comparison with the pre-task database backup changes only `generation_operations` and `caption_readings`. Both projects remain; **all 16 physical clip hashes**, both reference hashes, saved caption track/analyses/settings/font calibration and render records remain unchanged. The prior caption-disabled user output still hashes to `bd2a20d634851c30702ff62338d48b74f46cdf670ae180ac60c088757759660c`. The restored prior review compares exactly. Media, crops, databases, cache/model assets stay outside Git; unrelated next-env.d.ts is not staged.

Affected local checks: **128 caption/publication tests passed** with FFmpeg; separate affected unequal-height/batch checks pass; the real confident-reader disagreement check passes without new model inference. Earlier focused real printed/timestamp tests pass; affected Ruff lint/format pass. Exact-final-commit CI supplies the full repository gates. Optional-imaging skips in core CI are not evidence that those imaging paths ran there; they ran locally with installed dependencies. No full local suite, new model/font download, TikTok retrieval, transcription inference, overlay development or UI redesign.

## Remaining limits and estimates

The affordable pass separates observation from recognition, but the tracker still derives boundaries from recognized block strings. Changing crops, partial readings and conflicting characters can create false gaps; complete sampled detection does not establish a continuous caption track. Thin/low-contrast edge glyphs remain clipped in some frames, faint fades lose support, and reflections can resemble letters. High Tesseract/model scores are not calibrated accuracy probabilities. No new detector/model replacement was introduced; the installed alternative supplied useful contradictory readings, not a universal fix. A replacement would need an explicit local setup/permissions check and the same small comparisons first.

Evidence-grounded engineering estimates, **not accuracy percentages**: detection/coverage **~85%** (81/81 grid, demonstrated remaining false/missed regions); recognition **~75%** (supported fixtures/independent interval pass, handwritten wrong/withheld words and failed whole acceptance); appearance **~60%**, unchanged approximate Patrick Hand/unresolved exact identity; export verification **~75%** (supported normal multi-cut encode/API/media, prior browser evidence; no accepted handwritten export). Overall project acceptance stays with the architect. Image mode **0%**.
