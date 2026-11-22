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

1. **Pacing finds every cut** (pacing blueprint schema **2**, algorithm `scdet-cut-detector-v2`).
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
