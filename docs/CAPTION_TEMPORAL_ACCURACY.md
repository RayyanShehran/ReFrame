# Caption temporal acceptance — 2026-10-17

## Current caption recovery — 2026-10-18

**Partial recovery, not complete handwritten acceptance.** [Per-cue baseline provenance, interval comparison and final actual-project evidence](CAPTION_BASELINE_PROVENANCE.md). The stronger review is mixed accumulated/retried/restored evidence with one manual correction and some unknown historical timing, not a clean automatic target.

Source-region binding/verified glyph continuity and refinement-conflict propagation corrected. Actual saved score improves **196.9133 / 3.2 / 51.9667 / 3.7867 → 227.7133 / 1.8000 / 22.5667 / 3.7867** (correct / wrong / missing / extra word-seconds, exact manual interval/title excluded). Both opening phases are continuous. Stronger mixed review remains 236.7133 / 0 / 15.3667 / 1.92; final replies remain wrong/missing.

First whole integration failed to publish coarse replacements. A normal targeted actual-project retry saved fine observations; an additional whole retry retained them: revision **61**, **185.24 s**, 19 saved cues, 18 unresolved regions. This is accumulated automatic evidence plus a separately protected manual cue, not a recorded-input replay or restored proposal. No forced handwritten export. Existing video/settings/fonts/analyses and both projects/all 16 clips remain intact.

**Caption repair 63% of the last reported correct-word deficit recovered; complete handwritten acceptance 0% / not passed.** The remaining deficit is 9.0 word-seconds; conflicting final replies, coarse whole-run timing and scenery false blockers remain. Customize stays unchanged and previously verified. Image mode **0%**; overall reassessment remains with the architect. Earlier results below are historical.

## Historical decision before this recovery

**Handwritten reference remains partial and is not accepted for automatic complete export.** This follow-up implements interval evaluation and targeted tracking/reading corrections, but the fresh whole-track accuracy still regresses and scenery false blockers remain. Caption follow-up estimate **60%**, an engineering completion estimate, not an accuracy score. Customize is delivered separately; [inventory and browser evidence](CUSTOMIZE_WORKSPACE.md). Overlay extraction remains outside production. Image mode **0%**.

## Earlier acceptance follow-up — 2026-10-18

**Partial repair, not handwritten acceptance.** Customize implementation **100%**, scoped UI verification **100%**, caption regression repair **60%**, complete handwritten acceptance **0%**. These are task-completion estimates, not accuracy percentages. Correct reading selection improved, but continuous baseline quality is not restored; no complete handwritten export was made. The whole-run result below is fresh recognition in an isolated copy; the separately labelled final publication replay is not a second extraction or a restored proposal. The real project's current review/output was not replaced.

### Comparable retained baseline

Same retained reference, same 11 checked multiline phases, same `caption_temporal_eval` interval sweep for every row. The stronger retained review is the saved review immediately before the v19 retry, not v16 fresh inference. It includes prior supported automatic readings; the protected manual cue is excluded from automatic credit. This evaluation removes only the exact **14–15.533333 s** manual interval (clipping intersecting cues), plus the separate genuine title. Unlike the historical start-based filter below, it **counts extra automatic tails after the correction**, and retains drug wording that begins before 15.9 s. Old tables remain historical; the following rows were all recomputed with this same precise exclusion. Answers never enter the recognizer. Human timing remains coarse, not exhaustive frame ground truth.

| Track | Correct | Incorrect | Missing | Extra | Missing / fragmented exact blocks | Exact-block gap seconds |
| --- | ---: | ---: | ---: | ---: | --- | ---: |
| Stronger retained baseline | 236.7133 | 0 | 15.3667 | 1.9200 | 0 / 2 | 2.0367 |
| Historical v16 fresh | 168.3733 | 3.5333 | 80.1733 | 12.8933 | 2 / 5 | 8.6433 |
| Failed v19 fresh | 62.6500 | 2.8000 | 186.6300 | 3.1500 | 8 / 1 | 16.1767 |
| Failed v19 saved | 196.9133 | 3.2000 | 51.9667 | 3.7867 | 3 / 1 | 6.9367 |
| Final v20 fresh | 79.3500 | 1.8000 | 170.9300 | 0.2500 | 7 / 2 | 14.5433 |
| Whole v20 saved, before publication correction | 200.9133 | 1.8000 | 49.3667 | 3.5867 | 3 / 2 | 6.4367 |
| Final publication replay, same fresh input (not new recognition) | 212.3133 | 1.8000 | 37.9667 | 3.7867 | 2 / 2 | 4.5367 |

First four numeric columns are **word-seconds**, not probabilities or word counts. All tracks have zero overlapping cue pairs. Exact-block gaps also penalize missing lines even where some words are correct. Retained support is not fresh recognition. The fresh whole-worker saved result remains **35.8 correct word-seconds below** the stronger retained baseline; the final publication replay remains **24.4 below** it. Its automatic extra tail at 15.533–15.767 s, duplicate title fragments and redundant promise split are still disclosed, not credited as manual work or silently hidden.

### First demonstrated faults and stage trace

The evidence is available; a new model was not needed. The retained-source opening was visually checked, then a 1.5–7.1 s diagnostic replay traced actual crop coordinates, recognizer payloads and tracking in an isolated database. Its instrumentation is diagnostic, not an acceptance score. Subsequent targeted checks used normal Generate → worker → reconciliation → persisted review, without injected recognition or partial approval.

| Stage | Demonstrated finding / correction |
| --- | --- |
| Discovery / grouping | Captions are genuinely present. 960px thumbnail masks sometimes retain only part of a line; native 1920px crops contain the complete line. Discovery coverage is not proof of complete wording. |
| OCR routing — earliest demonstrated reading-selection fault | Mixed English/Arabic crop OCR produced a stray Arabic token in a Latin sentence. The “any Arabic character” guard skipped its independent English context/model check. Correct cached context readings were already available. v20 routes Latin-dominant lines to that check; Arabic-dominant lines retain their Arabic path. No confidence thresholds changed. Real mixed-language limitations still require review. |
| Model selection / agreement | Matching correct cached first-line contexts scored **0.943 / 0.943**, but selected frames **60 and 180** are four seconds apart. The existing agreement rule accepts at most 90 frames. v20 selects companions **3–90 frames** apart, rather than choosing an impossible corroborating pair. |
| Regional verification / tracking | Unselected discovery observations were cached as empty. When a partial thumbnail overlapped a known caption, the empty entry could be returned without native verification. v20 rechecks the source region before treating that observation as empty; the real printed fixture verifies continuous timing with deliberately partial discovery thumbnails. |
| Conflict / publication | Credible differing readings still block. Unsupported text is not supplied from evaluation answers. Some disagreements, incomplete multiline phases and refinement failures persist. The whole-run saved result demonstrated a separate publication bug: after timestamp reconciliation changed a cue key, the quality guard could not find its evidence and a three-line legacy phase became two lines. The final correction looks up reconciled evidence, while unchanged saved edges retain their own timing evidence. Focused tests and the explicitly labelled normal-worker publication replay preserve the third line and accept stronger wording without certifying unknown legacy timing. Malformed/unverified reply wording can still remain; no restoration masks that limitation. |

This identifies concrete selection/verification faults, **not a complete causal explanation or repair for every remaining region**. The v17–v19 independent-reader guards and changed fallback/order exposed these paths; reverting safeguards to reproduce the older favorable word-presence score would not establish correctness.

### The original 32 regions, classified by cause and time

Representative native frames/regions were decoded from the retained asset and inspected, including 2/3.5/5.1/6.5, 14.5/17.5, 19.5, 21.5–24, 26–29 and 32.5–35 s. The meaningful categories are:

| Cause | Regions | Union duration within category | Visual finding |
| --- | ---: | ---: | --- |
| Genuine caption regions unreadable to this pipeline | 18 | 6.8667 s | Opening/multiline, corrected cue's raw recognition, serious/promise/reply/closing text; readable source pixels do not imply a reliable machine reading. |
| Credible reading conflicts on genuine captions | 8 | 2.0000 s | Opening and replies; multiple rectangles can describe one time interval. |
| Genuine-caption timing uncertainty | 4 | 0.1333 s | Promise/reply/closing boundary observations. |
| Creator credit mistaken for an unreadable dialogue region | 1 | 0.0333 s | At 26 s the source visibly says **Henriorfedits**; it is an author credit, not spoken caption text. No hardcoded creator/position exemption was added. |
| Scenery/detector false alarm | 1 | 0.0333 s | At 19.5 s the frame contains underwater bubbles/person/background, no visible caption. Detector/padding failure must not be described as unreadable dialogue. |

Overall union is **9.0333 s**, not the sum of overlapping category durations. The original 32 do not mean 32 missing sentences. Separate sunset reflection false alarms remain known limitations; no reference-specific exclusions or blanket threshold changes were introduced.

### Targeted publication, then one final whole extraction

Pair selection alone did not improve saved coverage enough. After native verification and routing corrections, the normal isolated targeted worker saved a correct continuous two-line opening **1.5667–5.1000 s**, replacing malformed **1.5667–3.0000 s** text. In the failed first checked phase (1.6–5.1), correct coverage rose **18.2 → 49.0 word-seconds**, wrong **1.4 → 0**, missing **29.4 → 0**. The later three-line phase still truncated at 5.5 s; this was disclosed before the whole run. Diagnostic-window completion stays partial by design.

With the final **detector** code, before the later publication-only correction, one whole-reference extraction ran through normal production orchestration in an isolated copy of the current project: operation **8ba9e156-e887-4663-964a-23af97230d5e**, review revision **59**, **207.062 s** extraction / **209.281 s** to saved `failed/caption_partial`. Method **visible-tsv-adaptive-v20**; **81/81** discovery points, **32.094 s** discovery, **65** selected recognition frames; **1 new model input**, **1 other new OCR input**, **99 cached OCR / 58 cached model** inputs. No model download, injected words, restored proposal, partial acceptance or verified-correction replacement. Existing execution/process/cache/staging bounds remain. Results: **13 fresh cues, 19 saved review cues, 32 unresolved regions** (19 unreadable / 8 conflicts / 5 timing), **6.2333 s overall union**. A smaller union is not completeness.

Whole-run saved failures before that publication correction: opening has fragments **1.5667–3.0 and 3.5–4.5**, leaving **1.1 s** of exact-block gaps; **5.1–7.0333 loses its third line**. Promise support remains retained, with a redundant split at 24.3 s. The 26.5–27 and 29–30 multiline replies have no complete correct block; 27–29 has **1.9 s** exact-block gap and mixed-script/`tool` wording remains in unverified retained cues. Closing is retained continuously 32–39.5, short by about **0.033 s**. Other retained bounds remain approximately −0.017/+0.017 s for parties, 0/0 for drugs, 0/+0.033 for serious, ±0.003 for promise. This is **not restored baseline accuracy** and is not permission to export incomplete handwritten captions.

### Final publication correction and explicit verification limit

The one complete detector run exposed the evidence-key bug; shipping it would allow a retry to erase a retained line. The final publication-only correction was therefore verified with focused legacy/multiline/word-quality regressions and **one replay of the recorded real extraction through the normal isolated Generate worker**, not new OCR. It stayed `failed/caption_partial`, retained 19 review cues and the protected manual correction, and restored the full **5.1–7.0333** three-line phase. Source/snapshot checks, normal reconciliation and database publication ran; the detector boundary was explicitly supplied the recorded result. Replay elapsed **2.2 s**. No older review was restored or labelled successful.

That replay's scores are a separate row above: **212.3133 correct / 1.8 wrong / 37.9667 missing / 3.7867 extra word-seconds**, with **4.5367 s** exact-block gaps. Opening still has **1.1 s** of gaps; 26.5–27 and 29–30 still lack correct complete blocks; 27–29 still has **1.9 s** gap. Previous title/tail/reply/closing limitations remain. **The detector received one complete final-code extraction; a fresh full end-to-end extraction after the final publication edit was not repeated.** This limit is intentional and is not claimed as complete handwriting acceptance. Neither full-run nor replay was applied to the real user's review or track.

### Checks, preservation and output

Affected detection/reconciliation/evaluator suites: **149 passed** (before the final narrow routing edit); routing + real Arabic checks afterward: **3 passed**. Final publication/reconciliation/worker suite afterward: **44 passed**, with FFmpeg available (an initial shell without its PATH had skipped 14 media-dependent cases; the 44-pass rerun exercises them). The added partial-thumbnail real-media check passed; model-pair regression and existing handwriting checks passed **18** focused tests. Frontend workspace/grading/upload tests **20 passed**, lint/typecheck/build passed. Exact-final CI covers all suites. The existing printed/multiline/Arabic checks and supported multi-cut browser/export evidence are reused where unchanged; sunset fixtures were not re-inferred and their recorded false blockers are not claimed fixed. Overlay work remains outside production.

The complete final extraction deliberately used an isolated database/media view. Both actual user projects and all **16 physical clip hashes** remain, with saved analyses/fonts/settings/correction/track/render tables unchanged from the diagnostic copy's starting state; the only table difference against the initial diagnostic copy is its subsequently written reading cache (not a write to the real project). The actual project's generation review remains revision **58**, not the isolated revision 59. Manual **Boys you meet.**, **14–15.533333 s**, appearance and calibration remain protected. The prior caption-disabled output still hashes **bd2a20d634851c30702ff62338d48b74f46cdf670ae180ac60c088757759660c**. No new handwritten export exists; the previous supported three-cut export verifies rendering capability, not handwritten wording or exact font recovery. Approximate Patrick Hand appearance is separate from recognition readiness. No repeated TikTok retrieval, transcription inference, model download or image-mode work. The one unexpected disposable oEmbed request is disclosed in CUSTOMIZE_WORKSPACE.md.

## Historical evidence — 2026-10-17

## Changes and measurement

Method **visible-tsv-adaptive-v19** retains v1–v18 bindings and existing discovery/recognizer caches. Supported reliable token changes are separate phases rather than merged by character similarity. A more confident but strictly partial retry cannot replace a complete supported observation. Recovery may use the complete observed line only when the current source-pixel signature matches and the partial wording is a subsequence; genuinely contradictory words still block recovery. Refinement consolidates repeated observed text without crossing an observed absence. No reference-specific words, times, positions or colors entered production. No model/threshold replacement.

`tools/caption_temporal_eval.py expected.json actual.json` evaluates plain cue arrays offline. Half-open interval sweeps integrate correct/incorrect/missing/extra **word-seconds**; each token error is charged for its displayed/missing duration. It also reports exact normalized block presence, fragments, gaps, overlapping cues and signed early/late boundaries. Case/punctuation are ignored; extra words outside the target interval still count. Tests cover gaps, duplicates/fragments, nonfinite/reversed inputs and early/late boundaries. Manually checked evaluation answers remain outside production and Git.

The retained-reference comparison uses the existing checked line intervals, combined into **11 multiline phases**. These historical human checks are coarse (roughly 0.03–0.1 s), not an exhaustive source-frame transcription. Genuine title and the protected manually corrected **14–15.533 s** phase are excluded from automatic recognition credit. Short captions between observations remain unproven. The former favorable **74 correct / 1 wrong / 0 missing** best-overlapping-line result remains a separately labelled historical diagnostic in CAPTION_DISCOVERY.md; it is not timed acceptance.

| Track, automatic-credit intervals | Correct | Incorrect | Missing | Extra | Missing exact blocks / fragmented blocks |
| --- | ---: | ---: | ---: | ---: | --- |
| Historical v16 fresh, failed | 163.8733 | 3.5333 | 84.6733 | 11.4933 | 3 / 5 |
| Final v19 fresh | 58.1500 | 2.8000 | 191.1300 | 1.7500 | 9 / 1 |
| Final v19 saved review, including retained prior support | 196.9133 | 3.2000 | 51.9667 | 3.0867 | 3 / 1 |

Numbers are **word-seconds**, not word counts or model probabilities. Fewer extras does not compensate for lost coverage. Retained saved support must not be credited to fresh inference. No overlapping cue pairs were emitted in these comparisons. Exact-block metrics penalize a partial/malformed line even when some words are correct; the word-time metric separately credits those words.

## Whole-reference execution and actual saved timeline

Two attempts, with a concrete cleanup correction between them:

1. `c10da674-ba22-4989-b82b-7dcb0c45c043` stopped after **185.58 s** with `cleanup_failure`, before a fresh proposal returned. Removal of confirmed-stopped handwriting staging failed on Windows. The original OS exception was sanitized, so a transient sharing/permission lock is an inference, not a confirmed root cause. The owned temporary files were archived externally and removed; only this operation's cleanup flag was cleared for ordinary retry. No recognition result or successful export is claimed for it.
2. With the tested bounded two-second PermissionError retry in safe caption staging, `c6c29610-048a-4db3-958b-789b5a101d0a` completed fresh extraction in **186.19 s**, normal worker/publication in **187.94 s**. Final v19, **81/81** discovery frames, **24.171 s** discovery, **65** selected recognition frames; **26 new OCR**, **0 new model**, **106 cached OCR / 57 cached model** inputs. Existing 300-second deadline, request/model/staging/output bounds and containment remain. Persistent deletion failure or unsafe termination still retains staging with explicit error; tests cover transient success, persistent error and unsafe retention.

Normal Generate used no partial acceptance, no replacement of verified/corrected cues, no injected/replayed recognition and no restoration afterward. It persisted **18 review cues**, revision **58**, with **32 unresolved regions**, status `failed / caption_partial`. The harness's final console print hit Windows' encoding limitation **after** writing both full JSON evidence files; that print failure is not a second processing failure. The saved timeline is the actual failed proposal, not an earlier review substituted as success.

Fresh failures include malformed script fragments in the opening/replies, missing multiline promise/serious phases, incorrect `tool`, and closing gaps. The small fixes do not solve these. The sampled observer is complete, the caption track is not.

| Checked interval | Final fresh finding | Actual saved review finding |
| --- | --- | --- |
| 1.6–5.1 | Incorrect/incomplete opening; no exact block | Still no exact block; prior support insufficiently retained here |
| 5.1–7.0 | Three-line block missing | Prior supported block retained; end +0.033 s |
| 9.25–10.45 | Starts +0.250 s, ends +0.050 s | Prior range retained, −0.017/+0.017 s |
| 16–17.5 | Boundary/wording does not exactly match checked block | Prior exact range retained |
| 17.5–19 | Missing | Prior wording/range retained, end +0.033 s |
| 20.97–24.53 | Missing complete promise phases | Prior phases retained; approximately ±0.003 s, one redundant split |
| 26.5–30 | Missing/incorrect multiline phases and conflicting reply | 26.5–27 absent as full block; 27–29 lacks full block until 28.9; 29–30 still incorrect |
| 32–39.533 | Four fragments; gaps 32.1–33.5, 34–34.5, 35–35.033, 39.5–39.533 | Retained continuous 32–39.5; final 0.033 s unsupported |

These intervals identify failure, not permission to fill words from evaluation answers. The protected **“Boys you meet.”** correction remains exactly **14–15.533333 s**, including appearance/metadata. No handwritten captioned render was forced. Previous caption-disabled output still hashes to `bd2a20d634851c30702ff62338d48b74f46cdf670ae180ac60c088757759660c`.

## Focused existing set

| Case | Final interval errors (incorrect/missing/extra word-seconds) | Decision / runtime |
| --- | --- | --- |
| Blue/white printed | 0 / 0 / 0 | Complete, 10.53 s; exact 0.2–1.1 and 1.3–2.8 s |
| White/red multiline | 0 / 0 / 0 | Complete, 8.78 s; exact 0.2–1.2 and 1.4–2.8 s |
| Arabic/English | 0 / 0 / 0 | Complete, 8.17 s; exact 0.2–2.8 s |
| Plain sunset | 0 false words, but one false blocker | Partial, 31.83 s; **5.700–5.733 s** |
| Sunset + incidental label | 0 false words, but one false blocker | Partial, 22.83 s; **1.600–1.633 s** |

Actual native frames/thresholded regions at both scenery failures contain **sunlight/wave reflections**, not captions. Their narrow shapes can look like letters and repeat across source checks. They were inspected visually, not dismissed by OCR confidence. This regression is explicitly **unfixed**; no special asset/position exception or relaxed completion threshold was added. The incidental-label example derives from the same sunset, not an independent source.

Supported multi-cut export is additionally exercised by the new real browser editing/export workflow documented in CUSTOMIZE_WORKSPACE.md. It is a saved fixture edit, not a new handwritten acceptance run. Earlier normal automatic multi-cut evidence remains historical.

Preservation comparison changes only `generation_operations` and bounded `caption_readings`: both user projects, all **16 clips** (physical SHA-256 checks), reference/analyses/settings/fonts and prior render records remain. Real evaluation artifacts, frames, DBs and models stay outside Git. Unrelated `next-env.d.ts` remains unstaged. No TikTok requests, model downloads, transcription inference or overlay production work.
