# Cuts on the music

Makes sequence slots cut where the reference cuts against its music. Design note written before
implementation; evidence is appended below once it exists.

## Findings that shape the design

Evidence lives in `evidence/beats/` in the isolated e2e copy.

- **One timeline:** every slot copies its reference shot's interval, so the output timeline is
  the reference timeline. With reference audio at offset 0, a slot boundary at a reference cut
  lands on the same musical moment as in the reference.
- **Today's music errors are missing cuts:** the exported cuts that exist sit exactly on true
  reference cuts, but slots come from scdet pacing, which misses 4 of 18 handwritten cuts and 8 of
  21 printed cuts. The missed cuts are the ones that carry the rhythm:
  - handwritten's ~1.76 s bar cuts;
  - printed's 3–5-frame montage cuts on a fast tick.
- **Detected beats are not reliable on these tracks:**
  - Handwritten is ambient music under dialogue with no clear beat (autocorrelation strength
    0.24); its cuts are on beat at chance rates.
  - Printed's montage follows a pulse rate, but whole frames make the phase wander ±1–2 frames.

  Snapping the references' own cuts to detected beats would move correct cuts away from the
  reference.

## Design

1. **Pacing finds every cut** (pacing blueprint schema **2**, algorithm `scdet-cut-detector-v3`; v2 before the caption-robust detector, see [CAMERA_MOTION.md](CAMERA_MOTION.md)).
   - The pacing operation keeps scdet and adds the camera-motion cut detector
     (`camera_motion.cut_frames`, PR #11) on its own 192-pixel 30 fps gray decode.
   - A detected cut within 2 frames of an scdet cut is the same cut, and the scdet timestamp is
     kept. Other detected cuts are added at `frame / 30`.
   - The blueprint keeps both lists (`scdet_cut_timestamps`, `detected_cut_frames`) and their
     union as `candidate_cut_timestamps`.
   - Without NumPy (optional extra) the detector is `unavailable` and pacing is scdet only, as
     before.
2. **The reference's rhythm is measured** (`beats.py`, NumPy only, no new dependency).
   - The pacing operation decodes the reference audio (mono, 22,050 Hz, bounded) and computes:
     - a spectral-flux onset envelope;
     - a tempo per 8 s window;
     - dynamic-programming beats that follow the local tempo.
   - A window's beats are **clear** when its autocorrelation strength reaches the threshold
     calibrated by the synthetic tests. Only clear beats are kept.
   - The blueprint stores tempo, strength and clear beats as `rhythm` (null without audio or
     NumPy). Like the other analyses it is an observation; it is not a setting.
3. **Slots follow every cut** (sequence schema **2**, algorithm `reference-slots-30fps-v2`).
   - Slot generation uses the schema-2 pacing shots, so slots cut at every true cut.
   - **Very short shots:**
     - shots of 3 frames or more stay their own slots, because the printed montage's 3–5-frame
       shots are its rhythm;
     - a 1–2-frame piece (a flash or scdet jitter, never a true shot in the references) joins the
       previous slot.
   - The 60-slot cap still applies.
4. **Beat snapping only where it can help.** The output's music is known only for `reference`
   and `mix` audio: output time t plays reference audio at t + offset. For each reference cut:
   - If the cut is within 2 frames of a clear reference beat (an on-beat cut, phase φ = cut −
     beat), it moves to the nearest clear output beat plus φ, but only when that moves it by 2
     frames or fewer and keeps every slot at least 3 frames long.
   - Off-beat cuts, `original`/`mute` audio, and music without clear beats keep the reference's
     cut timing: the fallback.
   - At offset 0 the output beats are the reference beats, so every cut stays exactly where the
     reference cut. The reference's own pattern (every beat, every 2nd or 4th beat, or
     off-beat) carries over unchanged.
   - The sequence records what it was synced to (`music`: audio mode, offset, whether beats
     were clear, how many cuts moved).
   - The mode is the saved audio choice. Generation also passes its requested mode
     (`Generate.audio_mode`), because it saves the audio choice only after the slots. Before
     2026-11-24, every new project's first generation synced to the default `original`, so it
     never snapped. That was found on the third reference, a new project.
5. **Old data becomes stale through existing bindings.**
   - A saved v1 pacing result fails its ready check with `algorithm_changed`, so the next
     generation (or an explicit Analyze) re-runs pacing.
   - Sequences and edit plans are bound to the pacing operation, so they turn stale. Assignments
     are not silently changed, and rendering a stale sequence returns 409 until slots are
     regenerated, as for any pacing change.
   - Saved v1 sequences still load.
6. **Camera motion and captions are unchanged.** Motion already splits pacing shots at the same
   detected cuts, so its shots, curves and timing stay the same. Slots now begin at those shot
   boundaries, so motion curves line up with slot starts. Captions use reference/output time,
   which is unchanged.

## Bounds and timing

- **Decodes:** the video decode reuses camera motion's bounds (30 s, 80 MiB raw). The audio
  decode is capped at 16 MiB and 30 s.
- **Shared limit:** both decodes and the beat analysis run inside pacing's existing 120-second
  operation bound, with the same staging, containment and failure handling.
- **Failure:** exceeding a bound fails pacing, as today.
- **Logging:** stage times are saved in the blueprint (`timings`) and logged.

## Not changed

- Footage matching, captions and camera motion.
- Whole-clip and cut-plan renders: cut plans also gain the extra pacing shots.
- No new audio source is analyzed: the user's own clip audio is cut with its slots and has no
  continuous beat to follow.

## Evidence — 2026-11-23

**Synthetic tests:**

| File | Covers |
|---|---|
| `tests/test_beats.py` | **Known tempo:** a 120 BPM click track is clear, read at 120 ± 2 BPM, with ≥90% of beats within a frame and no extra beats. **Tempo change:** both halves of 120 → 90 BPM tracked within a frame. **No beat:** irregular hits give no clear beats. **Snapping:** on-beat cuts follow music offset by one frame and keep their every-2nd-beat pattern; a cut keeps its phase; at offset 0 the reference cuts stay exactly; off-beat cuts stay; no clear beats falls back; snapping follows a tempo change; snapping never leaves a slot under 3 frames. |
| `tests/test_beat_slots.py` | Sequences keep 3- and 5-frame shots and merge a 1–2-frame piece into the previous slot. On-beat cuts move with an offset; at offset 0, with `original`/`mute` audio, or without clear beats, slots keep the reference cuts. A sequence built on superseded pacing is stale. |
| `tests/test_pacing_analysis.py` | Dark cuts that scdet misses are added by the detector. A click-track reference's rhythm is measured. |
| `tests/test_generation.py` | Checkpoints from an older pacing algorithm are rebuilt, not reused. |

**Rhythm clarity** (normalized autocorrelation per 8 s window, threshold 0.5):

| Signal | Window strength |
|---|---|
| Click tracks | 0.58–0.94 |
| Irregular hits | 0.11–0.15 |
| Handwritten reference | 0.11–0.36 |
| Printed reference | 0.09–0.20 |

Neither reference has a clear beat.

**Real references** (isolated e2e). The PR #11 projects were regenerated with **Generate again**
in the UI, and the detected captions were then accepted:

| | Handwritten before | Handwritten after | Printed before | Printed after |
|---|---|---|---|---|
| Pacing | scdet v1: 14 cuts | 14 scdet + 4 detector = 18 | scdet v1: 13 cuts | 13 scdet + 8 detector = 21 |
| Slots | 15 | 19 | 14 | 22 |
| Export cuts on a true cut | 14/18 | **18/18, every one exact** | 13/21 | **21/21, every one exact** |
| Slot frames, min / median / max | 51 / 53 / 211 | 50 / 53 / 211 | 4 / 5 / 855 | 3 / 4 / 844 |
| Export cuts within ±1 / ±2 frames of an onset (reference: hw 28% / 50%, pr 52% / 86%) | 36% / 57% | 28% / 50% (= reference) | 69% / 85% | 52% / 86% (= reference) |
| Export cuts within ±1 / ±2 frames of a tracked beat (reference: hw 6% / 6%, pr 14% / 24%) | 7% / 7% | 6% / 6% (= reference) | 23% / 23% | 14% / 24% (= reference) |
| Rhythm | — | 132.5 BPM, not clear | — | 123.0 BPM, not clear |
| Cuts moved by snapping | — | 0 (offset 0, no clear beat) | — | 0 |

- **Slot lengths:**
  - Handwritten's 3.5 s slots, which spanned two bars, became two ~1.76 s bar slots.
  - Printed gained the eight missing 3–5-frame montage slots, and its long-take slot now starts
    at the true cut (frame 128, 844 frames).
- **Onset and beat rates:** the "before" rates differ from the reference only because cuts were
  missing. The "after" rates equal the reference's by construction.
- **Camera motion:** shots, keyframes and shake are identical to before. Every slot starts at a
  motion shot boundary, and no slot spans a motion cut.
- **Cut detection:** 18/18 and 21/21.
- **Captions:** the handwritten caption retry scores 251.61 / 0 / 0.47 / 2.55, 17/22, with
  byte-identical cues.

**Timing** (UI run, seconds from start):

| Stage | Handwritten | Printed |
|---|---|---|
| Reading reference (pacing re-analysis) | 14.6 (scdet 7.7, detector 4.0, rhythm 0.5) | 6.3 (scdet 2.3, detector 2.1, rhythm 0.3) |
| Choosing your clips | 52.1 | 45.4 |
| Matching the look (camera motion 16.8 / 20.2 s) | 125.7 | 53.4 |
| Recreating captions, to the review pause | 276.5 | 290.2 |
| Total to the pause | 468 | 395 |
| Accepting captions and rendering | 44 | 44 |

**Comparisons:**
- PR #12's regenerations through the API took 355 s and 336 s, and PR #11's new projects took
  539 s and 400 s.
- The handwritten caption retry ran from scratch this once (467 s, against 285 s), because its
  checkpoints came from the old pacing algorithm. That is the intended one-time cost after
  upgrading.

**Limits:**

- **Snapping untested on real music:** neither reference has a clear beat, so snapping is
  verified only on synthetic audio. On these references it changes nothing.
- **Clarity threshold:** 0.5 separates synthetic clicks from these references, but real music
  with a steady beat between 0.36 and 0.5 would also fall back.
- **When snapping runs:** it applies only when slots are generated. Changing the audio offset
  afterwards does not re-snap or mark slots stale, because regenerating slots would discard
  assignments. Use **Regenerate slots** after changing the offset.
- **Montage slots:** printed's 3–5-frame montage slots are now real slots. Footage matching ran
  on them without a footage shortage, but very short slots give the matcher little to compare.
- **First run after upgrading:** existing projects re-analyze pacing and rebuild slots, which
  discards saved slot assignments.
