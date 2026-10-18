# Retained caption baseline provenance and recovery — 2026-10-18

## Bounded reply decision — 2026-10-18

**NO-GO for unattended completion with the current recognition/acceptance approach.** This is a bounded decision, not a claim that the words are unreadable or that TrOCR cannot read them. Fresh TrOCR reads all three complete lines correctly; safe automatic acceptance and continuous saved publication remain unresolved. No production patch, saved-track change, manual replacement or export was made.

### One fresh experiment, only the failed replies

Same retained source/hash/operation as below; actual project remains revision **61**. Four native 1920×1080 frames at **27.2, 28.4, 29.1 and 29.7 s** were decoded locally. Complete source crops were visually inspected before inference. Both upper lines and the lower reply have all letters visible. Some production masks omit the beginning of the second upper line; the lower color-separated crop also includes shoulder/background ink after the last word. The complete grayscale crop retains the lower word and punctuation.

Two distinct frames per line, two preparations each (complete inverted/autocontrasted grayscale context and color-separated image): **12 unique image inputs**. Diagnostic rectangles were selected by inspecting this reference; they are not automatic production localization and were not added to product code. Expected wording stayed outside inference; no text prompts, dictionaries or replacements were provided.

Installed paths only: Tesseract 5.5.3.20260724, pinned English data, single-line mode; MIT-labelled TrOCR-base revision `eaacaf452b06415df8f10bb6fad3a4c11e609406`, CPU Torch 2.7.1 / Transformers 4.57.6, local-only fp32 greedy decoding, 80-token bound. One model batch, no repeated identical input and no download. Existing owned-process runner retained output/staging/deadline containment; the diagnostic batch had a 180-second stage limit and finished without timeout or capped outputs.

| Line / inspected frames | Fresh TrOCR result | Fresh Tesseract result | Saved status |
| --- | --- | --- | --- |
| “Okay. But I’m never gonna” — 27.2 / 28.4 s | Same correct words on all 4 preparations; model scores 0.979986–0.988109 | 0/4 meet existing reliable-wording criteria; empty or I/L/T errors/low token confidence | Correct automatic words already exist; upper line still has gaps when the combined block is rejected. Not newly resolved in saved track. |
| “do any of that, anyway.” — 27.2 / 28.4 s | Same correct words on all 4; 0.945008–0.995734 | Both separated crops correctly read the full line and meet existing criteria; context empty/low-confidence | Confirms clipping/selection contributes to malformed earlier crops. Existing normal selection/grouping still rejects conflicting blocks; no complete saved timing established. |
| “Well, that’s okay, too.” — 29.1 / 29.7 s | Same correct words on all 4; 0.865275–0.952602 | 0/4 reliable: ending read as `00).`, `LOC gag`, `LOO}` or `tocge,` | Correct model words exist, but an earlier high-confidence OCR “tool” conflicts. Automatic acceptance remains unresolved. |

Scores are model/token diagnostics, not calibrated probabilities. Punctuation/case variants above normalize identically; no words were supplied to obtain that agreement.

**Fresh versus reused:** all 12 experimental image hashes were cache misses: **12 new TrOCR input evaluations in one process/batch, 12 new Tesseract evaluations, 0 experimental cache hits**. Total 92.281 s; model portion 89.281 s. Two separate source/model-bound cache lookups confirmed that the current project already holds correct “too” context readings at 29.0 and 29.5 s (scores 0.904248 / 0.933388). Those were inspected, not rerun or substituted for the fresh experiment. No cache was cleared, refreshed or written; all experimental results stay outside the project database.

### Failure boundary and decision

The remaining words are not simply lost during publication. `automatic_captions.detect` explicitly marks a competing reliable OCR/model reading as a conflict before accepting the model, then refuses a cue spanning that conflict. The prior source-bound trace already contains the same correct model words; the current cache still contains them. Fresh recognition corroborates those words but does not remove the conflicting OCR evidence or establish automatic full-crop localization/timing. The previous normal targeted selection/tracking/publication check remains a failed acceptance check, not a successful one relabelled after this experiment. No repeat of its known-identical whole-reference request was made.

Accepting manually selected diagnostic boxes as automatic localization, allowing the correct model words to override the other reader solely because they match the visually checked answer, or changing cross-reader trust from this one reference would require a separately validated recognition/acceptance change. That is outside this bounded attempt. There is no demonstrated small lost-cue publication bug to patch here; manufacturing a guard relaxation would not establish reliable completion.

**Stop this approach. Minimum next option:** optional manual correction/review of these three reply lines and their visible timings in the existing editor. It was not applied. A separately scoped alternative-recognizer evaluation is another option, not started. Do not bypass incomplete-caption approval.

Actual saved score remains **227.7133 correct / 1.8 wrong / 22.5667 missing / 3.7867 extra word-seconds**, with the same exact manual/title exclusions. All **30 database tables** have unchanged contents and all **19 existing media/output files** match their pre-experiment SHA-256; both projects, 16 clips, recovered opening, protected “Boys you meet.” correction, fonts/settings and prior captions-off output remain intact. No new manual correction is included. Customize and unrelated `next-env.d.ts` remain untouched.

**Full handwritten captioned export: not produced. Handwritten acceptance: not passed. Image mode: 0%.** No whole-reference run, rendering, browser walkthrough, transcription or TikTok request was repeated. This documentation-only result requires no new regression tests or local suite rerun. Private source-bound candidates, complete crop contact sheet, raw recognizer outputs, current-cache checks and preservation fingerprints are retained under `caption-reply-decision-20261018`, outside Git. Diagnosis stopped within the 60-minute effort bound; final CI waiting is separate.

## Comparison contract

The stronger retained review is **mixed**, not a single automatic run: automatic evidence accumulated across targeted retries; one explicit protected manual correction; older reviews restored after failed verification; several legacy boundaries with unknown history. A `fresh` event inside a trace is raw automatic evidence; an `after` review or publication replay alone is not. No evaluation wording was supplied to production.

All compared records bind the same reference video `7670291844579740950`, media SHA-256 `303e0de877b9a90c9efae0a9c01a0b4d4c5099c6118fb13bc89577ba7f3f5e80`, and retained operation `38a9e232-a624-4160-aff3-f26cfd96da11`. The 11 existing checked phases and case/punctuation-normalized half-open word-second evaluator are unchanged. Only the exact manual interval 14–15.533333 seconds and the separate title are excluded from automatic scores; automatic tails still count. These coarse checked intervals are not exhaustive frame ground truth.

Earliest v3 saved-review metadata names TrOCR-small (historical license problem, unused now); later independent v8/v10/v13 fresh traces use the currently installed MIT-labelled TrOCR-base revision `eaacaf452b06415df8f10bb6fad3a4c11e609406`, Transformers 4.57.6 / CPU Torch 2.7.1. Some v9 whole fresh reads were Tesseract-only. A review’s final v14 engine/method label does **not** establish which run acquired every retained cue.

The retained `caption-trace`, `caption-final-workflow`, `caption-generalization`, `caption-coverage`, `caption-discovery` and `caption-workspace` artifacts were audited per cue. `replay.py`, `publication.py`, `publication-final.py` and restoration records explicitly restore authentic older reviews and/or replay recorded fresh inputs through publication. Those are legitimate preservation evidence, not fresh whole recognition. Absence of a manual flag is not proof of automatic history.

A later matching `fresh` event proves automatic attainability, not necessarily the first acquisition of that saved cue. There is no event-level journal joining every legacy cue to one retry. Where that link is absent, original acquisition is unknown; the table reports the located evidence rather than inventing a history.

Trace locators: `caption-trace/{opening-saved,restored-opening,parties-before,promise-saved-retry,final-promise,replies-after,full-final}-trace.json`; `caption-final-workflow/{parties-evidence,reply-evidence}-trace.json`; `caption-generalization/{promise-v13,tail-final-v13,replies-final-v13,reply-complete-v13}-trace.json`. Each automatic claim uses its **stage=fresh** event, not its after-review. Private crops, media and databases remain outside Git.

## Per-cue provenance of the stronger 18-cue review

| Saved seconds | Cue / phase | Located provenance and limits |
| --- | --- | --- |
| 1.567–5.1 | Opening, two lines | Automatic wording/range independently reproduced in targeted v8 and v10 fresh traces; already present in v3 review. Later accumulated/restored. Baseline has no boundary observation. |
| 5.1–7.033 | Opening, three lines | Targeted v8/v10 fresh automatic reading and range. Accumulated/restored; missing baseline timing provenance. |
| 7.033–7.5 | aftersun title | v10 targeted fresh automatic; excluded from dialogue metric. |
| 7.5–9 | aftersun title | v9 whole fresh Tesseract; redundant split, excluded from dialogue metric. |
| 9.233–10.467 | Whatever parties… | v7 and v11 targeted fresh automatic. Baseline observation unresolved; fine brackets exist but are not certified. |
| 14–15.533 | Boys you meet. | Explicit manual correction, protected. Excluded from automatic credit; preserved in the review. |
| 16–17.5 | Drugs you take. | v9 whole fresh Tesseract, exact range; legacy baseline has no observation. |
| 17.5–19.033 | No. I’m serious, Soph- | Legacy v3 review wording; v16 fresh fragments corroborate words, but exact continuous range has no located raw-run proof. History/timing unknown, not claimed as fresh automatic. |
| 20.967–21.967 | I’ve done it all… | v8/v10 targeted fresh automatic. Baseline paired-model wording with supported 1/30-second brackets. |
| 21.967–24.3 | Promise, three lines | v13 targeted fresh words, raw end 24.733; later saved end 24.3 after reconciliation/split. Exact saved boundary provenance unknown. |
| 24.3–24.533 | Promise, punctuation variant | v13 tail targeted fresh words/range; baseline observation absent. |
| 26.433–26.533 | Okay. But I’m never gonna | v13 replies targeted fresh exact. Paired-model baseline, supported 1/30-second brackets. |
| 26.567–26.933 | Same upper reply | v13 raw end 26.967, later saved 26.933. Start supported; end provenance absent. |
| 27.033–27.5 | Same upper reply | v9/v13 targeted fresh exact. Baseline start supported; end bracket absent. |
| 28.5–28.9 | do any of that, anyway | v9 whole fresh Tesseract 28.5–29; later split at 28.9 by added multiline cue. Exact shortened boundary not independently proved. |
| 28.9–29 | Upper reply, two lines | v11/v13 targeted fresh exact normalized words/range. OCR observation, supported end bracket. |
| 29–30 | Upper reply + “well…too” | v13 replies-final fresh exact three-line wording/range, installed TrOCR-base. Automatic evidence, not injected/manual; baseline OCR timing coarse (start 28.6–29, end 29.5–30). |
| 32–39.5 | But if you do, remember, okay | v9 whole fresh Tesseract exact. Accumulated/restored; baseline timing observation absent. |

The explicit manual cue is not counted as automatic. The remaining score is an **automatic-only exclusion view of a mixed historical review**, not proof that every legacy second was generated automatically. In particular the continuous serious-response range remains unproven. No “all-automatic baseline” is claimed.

## Interval-level difference before this correction

`B` is the stronger retained review; `C` is the last publication replay at reviewed HEAD 8235cb7 (not a fresh extraction). C/W/M/E = correct / wrong / missing / extra word-seconds. Times below are clipped to the checked phase; text preserves lines with `<br>`. Case/punctuation variations score equally.

| Checked seconds / expected wording | Baseline saved spans/text | Current saved spans/text | B C/W/M/E | C C/W/M/E | First demonstrated loss / evidence |
| --- | --- | --- | --- | --- | --- |
| 1.600–5.100: You know I want you to know<br>you can talk to me about anything | 1.600–5.100: You know I want you to know,<br>you can talk to me about anything. .. | 1.600–3.000: You know I want you to know,<br>you Can talk to me about anything. ..; 3.500–4.500: You know I want you to know,<br>you Can talk to me about anything. .. | 49.0000 / 0.0000 / 0.0000 / 0.0000 | 33.6000 / 0.0000 / 15.4000 / 0.0000 | Correct cached wording acquired, selected, then lost in region binding/tracking; missing 3–3.5 and 4.5–5.1. Targeted fresh automatic baseline corroboration exists. |
| 5.100–7.000: You know I want you to know<br>you can talk to me about anything<br>As you get older you know | 5.100–7.000: You know I want you to know,<br>you Can talk to me about anything. ..<br>As you get older, you know! | 5.100–7.000: You know I want you to know,<br>you Can talk to me about anything. ..<br>As you get older, you know! | 38.0000 / 0.0000 / 0.0000 / 0.0000 | 38.0000 / 0.0000 / 0.0000 / 0.0000 | No saved score difference; fresh retries could truncate after 5.5. Duplicate region binding and expiry of wording prototypes demonstrated. |
| 9.250–10.450: Whatever parties you go to | 9.250–10.450: Whatever parties you go to . | 9.250–10.450: Whatever parties you go to, | 6.0000 / 0.0000 / 0.0000 / 0.0000 | 6.0000 / 0.0000 / 0.0000 / 0.0000 | No normalized score difference; automatic targeted evidence. |
| 16.000–17.500: Drugs you take | 16.000–17.500: Drugs you take. | 16.000–17.500: Drugs you take, | 4.5000 / 0.0000 / 0.0000 / 0.0000 | 4.5000 / 0.0000 / 0.0000 / 0.0000 | No difference; automatic v9 evidence. |
| 17.500–19.000: No I'm serious Soph | 17.500–19.000: No. I'm serious , Soph- | 17.500–19.000: No. I'm serious , Soph- | 6.0000 / 0.0000 / 0.0000 / 0.0000 | 6.0000 / 0.0000 / 0.0000 / 0.0000 | No difference; exact historical continuous boundary provenance unknown. |
| 20.970–21.970: I've done it all and you can too | 20.970–21.967: I've done it all and you can too .; 21.967–21.970: I've done it all and you can too .<br>I just want you to promise me that<br>you'll talk to me about it, okay! | 20.970–21.967: I've done it all and you can too .; 21.967–21.970: I've done it all and you can too .<br>I just want you to promise me that<br>you'll talk to me about it, okay! | 8.0000 / 0.0000 / 0.0000 / 0.0500 | 8.0000 / 0.0000 / 0.0000 / 0.0500 | No difference; 0.0033 s phase-boundary rounding produces 0.05 extra in both. |
| 21.970–24.530: I've done it all and you can too<br>I just want you to promise me that<br>you'll talk to me about it okay | 21.970–24.300: I've done it all and you can too .<br>I just want you to promise me that<br>you'll talk to me about it, okay!; 24.300–24.530: I've done it all and you can too .<br>I just want you to promise me that<br>you'll talk to me about it . Okay ? | 21.970–24.300: I've done it all and you can too .<br>I just want you to promise me that<br>you'll talk to me about it, okay!; 24.300–24.530: I've done it all and you can too .<br>I just want you to promise me that<br>you'll talk to me about it . Okay ? | 58.8800 / 0.0000 / 0.0000 / 0.0000 | 58.8800 / 0.0000 / 0.0000 / 0.0000 | No difference; accumulated targeted automatic words, later split boundaries. |
| 26.500–27.000: Okay But I'm never gonna | 26.500–26.533: Okay . But I'm never gonna; 26.567–26.933: Okay . But I'm never gonna | 26.500–27.000: Okay . But I'm never gonna<br>do any يه‎ | 2.0000 / 0.0000 / 0.5000 / 0.0000 | 2.5000 / 0.0000 / 0.0000 / 1.5000 | Current gains 0.5 correct but adds 1.5 extra via premature malformed second line. Confident crop OCR conflicts with compatible complete model readings. |
| 27.000–29.000: Okay But I'm never gonna<br>do any of that anyway | 27.033–27.500: Okay . But I'm never gonna; 28.500–28.900: do any of that, anyway,; 28.900–29.000: Okay, But I’m never gonna<br>do any of that, anyway, | 27.033–27.500: Okay . But I'm never gonna; 28.500–28.900: do any of that, anyway,; 28.900–29.000: Okay, But I’m never gonna<br>do any of that, anyway, | 5.3333 / 0.0000 / 14.6667 / 0.0000 | 5.3333 / 0.0000 / 14.6667 / 0.0000 | Both already miss 14.6667 word-seconds. Baseline is not a complete target. Partial/clipped OCR conflicts and tracking fragment the genuine second line. |
| 29.000–30.000: Okay But I'm never gonna<br>do any of that anyway<br>Well that's okay too | 29.000–30.000: Okay . But I'm never gonna<br>do any of that, anyway,<br>well . That's okay , too . | 29.100–30.000: do any يه‎<br>Well, that’s okay, tool | 14.0000 / 0.0000 / 0.0000 / 0.0000 | 4.5000 / 1.8000 / 7.7000 / 0.0000 | Correct base-model “too” exists at independent frames; rejected against confident OCR “tool”. Wrong OCR can reappear during refinement outside the original conflict frame. Upper-line coverage also lost in grouping/tracking. |
| 32.000–39.533: But if you do remember okay | 32.000–39.500: But if You do, remember, okay! | 32.000–39.500: But if You do, remember, okay! | 45.0000 / 0.0000 / 0.2000 / 0.0000 | 45.0000 / 0.0000 / 0.2000 / 0.0000 | No difference; both omit final 0.0333 checked seconds. Legacy baseline boundary lacks an observation. |

The 24.4 correct word-second gap decomposes exactly into **15.4 opening loss + 9.5 final-reply loss − 0.5 early-reply gain**. Unchanged phases do not justify rerunning the whole source to investigate them. The baseline itself has missing mid-reply coverage and uncertain legacy timing.

## Demonstrated correction and targeted saved verification

v21 binds a reliable reading only to a source region with compatible vertical position/width and overlap, instead of assigning it to every intersecting discovery rectangle. In the real opening trace a caption line was assigned to a differently shaped overlapping title region; duplicates displaced real lines during grouping. No glyph-similarity or confidence threshold was lowered.

After model selection, chronological recovery remembers only reliable words whose glyphs were actually verified at each observed frame, retaining the original word-sample origin for appearance. Previously correct representative readings could expire after the 90-frame window while the same lettering remained observed. It does not recover words through an empty/different shape or an unobserved frame.

Normal Generate → worker → tracking → reconciliation → saved-review checks used an isolated copy of the retained user project, no replacement of verified corrections and no partial-export approval. Opening targeted v20 saved score was **57 correct / 0 wrong / 30 missing / 0 extra** across the first two checked phases. v21 saves **1.566667–5.1 two lines and 5.1–7.033333 three lines**: **87 correct / 0 wrong / 0 missing / 0 extra**. Extraction 102.62 s, all recognition from compatible cache (0 new tool reads). These are targeted retry results, not a clean whole-run score.

The final-reply trace confirms correct cached TrOCR-base context readings (“too”, scores 0.904/0.933) but confident Tesseract “tool”; masked/gray variants also disagree. A bounded diagnostic with the installed OCR’s line/raw-line modes on the native crop did not supply a reliable independent resolution. No word was forced, threshold lowered or language globally excluded. Remaining reply failure is disclosed separately from the recovered opening.


The final-code opening retry (v22) independently retains the same two complete phases: **92.953 s**, **26 cached OCR / 17 cached model** inputs, **0 new OCR / 0 new model** inputs, normal saved result unchanged at 87/0/0/0 on the opening checks. The window deliberately does not certify the rest of the reference.

The v22 targeted reply retry is a **failed recognition check**, not a repaired reply: 180.875 s, 43 new OCR inputs, 65 cached OCR / 34 cached model, 0 new model inputs; no fresh publishable cues. Its older partial saved cues are retained rather than silently deleted. The conflict fix does not recover “too”; it prevents the disputed OCR from regaining reliable status at neighboring refinement frames. The remaining wrong wording stays disclosed in the retained review, and incomplete-export safeguards remain active.

## Final actual-project verification

The first final-code whole integration **failed to publish** the recovered opening: operation `680bd5bd-fefc-4cb4-ab7a-d626366bc16b`, revision 59, 184.62 s extraction / 186.31 s saved partial. Correct words arrived with coarse 2–5.5 / 5.5–7.5 second phases; fine-timing/multiline guards correctly rejected those replacements. Actual saved score stayed 196.9133 / 3.2 / 51.9667 / 3.7867. This failed verification is not labelled success.

The proven targeted retry then ran normally on the **actual** project: operation `09fe85ae-9656-432c-9946-1f98b165105b`, revision 60, 87.11 s extraction / 88.39 s saved partial, 26 cached OCR / 17 cached model inputs, zero new inference. It saved the continuous fine-timed opening; no older proposal was restored.

An additional complete retry checked this concrete integration failure: **two whole checks**, not one relabelled run; production code was unchanged between them. Final operation `1a4af264-a2cd-4951-9eb7-4aa49f734419`, revision **61**, method `visible-tsv-adaptive-v22`: **183.828 s extraction / 185.235 s through saved failed/caption_partial**. Discovery **81/81**, 24.156 s; 65 recognition frames. **0 new OCR / 0 new model** inputs; **106 cached OCR / 58 cached model** inputs. Cache reuse is automatic evidence, not fresh model inference. The initial whole check imported 728 source/tool/input-bound facts through the existing bounded cache; this repeat used actual saved evidence without reimport. Neither run replayed recorded recognition results.

| Track, manual/title excluded | Correct | Wrong | Missing | Extra |
| --- | ---: | ---: | ---: | ---: |
| Stronger mixed historical review | 236.7133 | 0 | 15.3667 | 1.9200 |
| Actual saved before this correction | 196.9133 | 3.2000 | 51.9667 | 3.7867 |
| Prior publication replay, not fresh | 212.3133 | 1.8000 | 37.9667 | 3.7867 |
| Final whole extraction alone | 153.8500 | 0.0000 | 98.2300 | 11.6500 |
| Final actual saved review, accumulated | 227.7133 | 1.8000 | 22.5667 | 3.7867 |

Comparable word-seconds: actual saved gains **30.8 correct**, removes **1.4 wrong / 29.4 missing**. Against the last reported replay it recovers **15.4 correct / 15.4 missing**; the remaining deficit to the stronger review is **9.0**. This is accumulated automatic retry behavior, not a clean single-run result. The exact manual “Boys you meet.” cue (14–15.533333 s, **4.6 manual correct word-seconds**, excluded above) retains its text, timing and appearance.

Final extraction: 11 raw cues; 19 saved review cues. Still **partial**, 18 regions: {'unreadable_caption': 6, 'detector_failure': 1, 'conflicting_readings': 8, 'uncertain_caption_timing': 3}; reading budget exhausted. Opening recovery survives. Replies at 26.5–30 s retain premature/malformed second-line text, missing upper lines and “tool” rather than “too”. Serious/promise/closing readings/timing also have unresolved observations; older cues are not promoted to new verification. Redundant genuine-title fragments and automatic tails remain disclosed, not hidden by scoring.

**Caption repair: 63% of the reported correct-word deficit recovered** (15.4/24.4; a defined repair measure, not global OCR accuracy). **Complete handwritten acceptance: 0% / not passed**: reliable final replies, complete single-pass coverage and a complete real handwritten export remain unmet. No incomplete-export approval or forced handwritten export. Prior captions-off output `d0fce00f-8bb2-4716-807d-8b9db8786b4e` remains byte-identical, SHA-256 `bd2a20d634851c30702ff62338d48b74f46cdf670ae180ac60c088757759660c`.

Both projects, 16 clips and all 19 existing media/output files retained with matching SHA-256. Only `generation_operations` and bounded `caption_readings` changed; caption tracks, font calibration, analyses, plans, recipes, settings and render records are unchanged. Customize and unrelated `next-env.d.ts` remain untouched. Image mode **0%**; overall reassessment remains with the architect.

## Focused verification

- Tracking/publication/manual/source regressions: `pytest tests/test_automatic_captions.py tests/test_generation.py -q -k 'not real'`: **139 passed** before the conflict follow-up.
- Existing printed, multiline, Arabic/mixed-script and scenery tests: **9 passed**. Final conflict/region/continuity subset: **10 passed**; generated independent-reader integration: **2 passed**. Ruff check and format passed on the three affected Python files.
- Existing multi-cut fixture: complete, both lines at 0.2–1.2 / 1.4–2.8 s, **8 correct words / zero wrong, missing, extra**. Only detection rerun; unchanged renderer evidence reused.
- Held-out scenery: no fabricated cues, but existing false blocker at 5.7 s remains partial. No lowered threshold to disguise it.
- No full local suite, browser repetition, model download, TikTok retrieval, transcription or overlay work. Final-commit CI runs the full suite. Private evidence stays under `caption-recovery`, outside Git.
