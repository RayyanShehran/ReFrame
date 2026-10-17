# Caption temporal acceptance — 2026-10-17

## Decision

**Handwritten reference remains partial and is not accepted for automatic complete export.** This follow-up implements interval evaluation and targeted tracking/reading corrections, but the fresh whole-track accuracy still regresses and scenery false blockers remain. Caption follow-up estimate **60%**, an engineering completion estimate, not an accuracy score. Customize is delivered separately; [inventory and browser evidence](CUSTOMIZE_WORKSPACE.md). Overlay extraction remains outside production. Image mode **0%**.

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

1. `c10da674-ba22-4989-b82b-7dcb0c45c043` stopped after **185.58 s** with `cleanup_failure`, before a fresh proposal returned. A Windows file lock interrupted removal of confirmed-stopped handwriting staging. The owned temporary files were archived externally and removed; only this operation's cleanup flag was cleared for ordinary retry. No recognition result or successful export is claimed for it.
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
