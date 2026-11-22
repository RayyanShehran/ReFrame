# Camera motion copying

Copies the reference's camera moves (zooms, pans, rolls, punch-ins and shake) onto the user's clips in
sequence exports. Design note written before implementation; measured evidence is appended below
once it exists.

## Data model

- **Observation:** method `phase-similarity-v1` measures one curve per reference pacing shot.
  `ShotMotion` holds the shot's 30 fps frame range, a compact intentional-move keyframe list
  (`frame`, `scale`, `x`, `y`, `angle`) and per-axis shake waves (`amplitude`, `frequency`,
  `phase`) for `x`, `y` and, separately, `angle`. Every value is relative to the shot's first frame.
  `angle` is in radians, clockwise on screen, and is zero unless the shot passes the
  [rotation](#rotation) noise floor and evidence gate. Translations are fractions of frame
  height around the frame centre, and `scale` is the content magnification. A shot with no
  measurable move has one identity keyframe and no shake waves.
- **Saved setting:** a new revisioned `camera_motion_settings` row (schema 23, additive) stores
  `enabled` (default **on**), the copied shots, the reference binding (retained reference
  operation UUID and media SHA-256), the method and the stage timings. The automatic generation
  coordinator writes the measured shots into this setting, like it saves 70% color transfer.
  Rendering reads only the saved setting, never the raw measurement. No row means revision 0,
  enabled, no shots: no motion.
- **API:** `GET /api/projects/{UUID}/camera-motion` returns the setting and its status: stale when
  the retained reference operation changed. `POST` with `{expected_revision, enabled}` toggles it
  (409 on conflict). Render requests add `expected_motion_revision`, which may be omitted only at
  revision 0, as for the other settings. The setting is bound into the render specification, so a
  change marks earlier outputs outdated.

## Where detection runs

Detection runs inside the generation worker as part of "Matching the look", after the sequence
and framing are saved. No new job framework or table is needed beyond the setting. The steps:

1. **Decode.** One contained FFmpeg decode of the retained reference: PTS normalized, 15 fps,
   area-scaled to a 192-pixel long edge, 8-bit gray, written as raw frames to owned staging
   (≤40 MiB). The decode gets ≤30 s, estimation ≤30 s and the whole step a ≤60 s overall
   deadline. Exceeding a bound fails the motion step only: generation records a notice and
   renders without new motion. Per-stage timings are logged and saved.
2. **Pairwise motion.** Full-frame phase correlation (Hann window, sub-pixel parabolic peak)
   gives the global shift. Then 32-pixel windows on a 16-pixel grid, pre-shifted by it, give
   local shifts. A weighted, outlier-trimmed least-squares fit of a similarity model (scale,
   rotation, translation) follows. Static overlays such as captions, and flat windows, drop out
   as outliers or low-texture windows.
3. **Per shot.** Pairwise models compose from the shot's first frame and reset at every pacing
   cut. A single pair whose scale jumps by more than 4% is kept as a **punch-in** step. Elsewhere
   a 0.6 s moving average gives the **intentional** curve, and the residual is **shake**.
   Douglas-Peucker simplification keeps at most 12 keyframes per shot (tolerance 0.3% of height,
   0.3% scale). Shake whose RMS is at least 0.15% of height is summarized by its two strongest
   FFT components per axis.

NumPy comes from the optional `visual` extra. Without it, detection reports unavailable and
generation continues without motion. No new dependency or model.

## How it is applied per slot

Each sequence slot carries its reference interval, so its curve is the enclosing shot's curve
sampled over that interval and rebased to identity at the slot's first frame. Manual slots
without a reference interval get no motion. In `video_render.assemble_sequence`, after resize and
color, one `zoompan=d=1` filter applies the per-frame zoom `Z = z0 * scale(t)` and the crop centre
`-translation / scale`. These are server-generated piecewise-linear expressions over the output
frame number, plus the shake sinusoids. When the slot rolls, an FFmpeg `rotate` filter with the
same kind of expression turns the frame about its centre first (see [Rotation](#rotation)).

## How it combines with framing

- **Fill** (and Automatic → Fill): motion runs on the final canvas after framing's crop.
- **Fit:** motion runs on the scaled image before framing pads it, so the bars never move or grow.
- **Original:** motion runs on the scaled frame.
- **No black edges:** the base zoom `z0` is the smallest zoom whose crop window stays inside the
  frame for the slot's largest translation and smallest scale. It is capped at 1.35; beyond that
  the slot's move is scaled down to fit and a notice is recorded. Expressions are also clamped to
  the frame, so a crop can never leave the source image. A rolling slot's base zoom also covers
  the turned crop's corners (see [Rotation](#rotation)); the clamp cannot do that for a turned
  crop.
- Captions burn after motion, on the final canvas, so they do not move. Frame and caption previews
  share `assemble_sequence`, so interval previews apply the same motion with the slot's frame
  offset.
- Whole-clip and cut-plan renders are unchanged. Turning the setting off renders exactly as
  before.

## Implementation notes

- **Sub-pixel accuracy:** the sub-pixel peak uses Foroosh's phase-correlation estimate, not a
  parabola, which locked toward whole pixels and read slow pans 25–45% short. Each frame is
  measured against an anchor up to 8 frames back, which re-anchors after 6 px or a 4% scale change.
- **No evidence, no motion:** a frame pair reports no motion when fewer than 35% of the grid
  windows carry texture, or when fewer than half of them agree with the camera model. Dark scenes
  with a moving light, and moving subjects, therefore read as static.
- **Size on screen:** the move is applied after the base zoom, so it keeps its size on screen.
  The base zoom is `z0 = max(1/s, (1 + 2|x|·W⁻¹·H)/s, (1 + 2|y|)/s)` over the slot.
- **Previews:** caption previews and generation's unpublished assembled-audio export use the
  current saved motion revision.
- **Customize:** Export shows the toggle and how many reference shots move.
- **Optional step:** generation's motion step always completes, with a notice when NumPy is
  missing or measuring fails.
- **Snapshot:** the setting is not part of generation's customized-settings snapshot, so turning
  it on or off never forces a replacement prompt.

## Evidence — 2026-11-22

**Synthetic, `tests/test_camera_motion.py`:** NumPy-warped texture clips with exact ground truth,
encoded with H.264.

| Truth | Measured |
|---|---|
| zoom 1.12, pan −0.06 / +0.03 | 1.118, −0.060, +0.029 |
| pan 0.080 | 0.0795 |
| slow pan 0.030 / −0.020 | 0.0297 / −0.0198 |
| zoom 1.10 | 1.098 |
| 4 Hz shake, 0.012 amplitude | separated as shake (frequency and amplitude within tolerance) |
| static clip; dark frame with a sweeping light | no motion |

Applying a known curve and re-measuring it gives the curve back within tolerance, with no black
border pixels.

**Real references:** isolated e2e copy, normal UI flows; the real `data/` is unchanged. Each
export was compared per slot with a second render of the same sequence with motion off. That
isolates the applied motion from the footage's own handheld movement.

- **Handwritten (15 shots, 8 moving):**
  - The 7 static slots are pixel-identical to the motion-off render.
  - 6 of the 8 moving slots measure 0.94–1.06 of the applied magnitude, in the dominant
    direction, with half-move timing within 0–3 frames.
  - One slot under-reads (0.28); in 9 of 12 checked frames the intended curve explains the
    export better than no motion.
  - One dark night slot cannot be measured.
- **Printed (14 shots, 5 moving):** 4 of the 5 moving slots measure 0.91–1.03 of the applied
  magnitude, in the dominant direction. One dark slot cannot be measured (the intended curve
  fits better in 10 of 11 frames).
- **Edges:** on every slot and frame the requested crop window lies inside the picture (0 px
  overshoot).
- **Timing:** detection took 14.0 s (handwritten, 39.6 s reference) and 15.8 s (printed, 32.4 s
  reference). Generation times are within run-to-run variation: handwritten retry 383 s (370–372 s
  before), printed to the caption-review pause 420 s (391–423 s before).

**Limits:**

- **Long takes:** a 28.2 s printed shot is a real long take, not a missed cut. Its slot copies
  15% of the accumulated move (see [Shot cuts](#shot-cuts) for the missed-cut part).
- **Large moves:** moves beyond the 1.35 base-zoom margin are scaled down.
- **Rotation:** measured, not applied at the time; applied since 2026-11-23 (see [Rotation](#rotation)).
- **Camera vs subject:** detection cannot tell a camera move from a large subject that fills the
  frame. Without a frame-by-frame camera truth for real footage, the real-reference curves are
  plausible estimates, not verified ground truth.

## Shot cuts

Added 2026-11-22. Before this, curves followed pacing's scdet shots exactly, and those miss
cuts.

**Ground truth:** the cuts in both e2e references were hand-labelled to the frame before the
detector output was examined (`evidence/shots/` in the isolated e2e copy).

- **Handwritten:** 18 hard cuts.
- **Printed:** 21 hard cuts in a 4.3 s montage of 3–5-frame shots, then one continuous 28.2 s
  tracking take.

**Why scdet misses cuts:** all of its errors are misses (precision 1.0).

- Missed cuts score 3.8–9.5, against threshold 10.
- Flashing lights and a fade reach 5.5 without a cut, so a lower threshold cannot separate
  them.
- The misses are dark-to-dark cuts, cuts into near-black, and cuts between shots with similar
  brightness and color grading.
- Ruled out as causes: sampling rate, minimum shot length, dissolves and flashes.

**Detector** (`camera_motion.cut_frames`): it runs on the motion step's own 192-pixel gray
30 fps decode, adding no second decode or new bound.

1. **Hard cut:** a frame pair is a cut when:
   - the edge-structure change (1 − correlation of normalized gradient-magnitude maps) is at
     least 0.35 **and** at least twice the motion level on *both* sides (the larger of two
     neighbouring pairs per side);
   - **and** the brightness-normalized luma histogram changes by 0.12 or more, unless the edge
     change alone is at least 0.75.

   Continuous motion (whip pans, moving lights) raises both sides and is rejected. Two pairs
   per side keep neighbouring cuts out of the window for shots of 3 or more frames.
2. **Near-black frames** (luma std < 9): a cut is a mean-luma jump of 20 or more. Fades change
   only a few levels per frame.
3. **Flash:** a change is not a cut when the picture returns to the previous frame within 3
   frames, or when the frame is that return.
4. **Dissolve:** the middle frame is close to the average of two different pictures 6 frames
   either side, with no hard cut between them.

**Use:**

- Motion shots are pacing's shots split at detected cuts. A detected cut within 2 frames of a
  pacing boundary is the same cut, so no 1-frame shots appear.
- A slot whose reference interval spans a cut continues from where its curve is with the next
  shot's motion, because the footage does not cut there. Past the last shot it holds.
- The cut step's time is saved as `timings.cuts_seconds` and logged with the other stages.

**Pacing and slot timing used scdet here; since 2026-11-23 they use these cuts too** ([BEAT_SYNC.md](BEAT_SYNC.md)). At the time, cut accuracy clearly improved, but changing the
pacing algorithm:

- bumps pacing blueprint schema 1 (whose algorithm, threshold and method are literals);
- marks every saved edit plan and sequence stale;
- needs a raw-frame decode beyond pacing's 1 MiB/2 MiB bounds;
- would add eight 3–5-frame slots to the printed reference, which footage matching and captions
  have not been run against.

That belongs in its own change, with these truth files as its benchmark.

### Evidence — 2026-11-22

Precision and recall within 2 frames, against the hand-labelled truth:

| Reference | Before (scdet) | After (scdet + detector) | Longest shot before → after (true) |
|---|---|---|---|
| Handwritten, 18 cuts | P 1.00, R 0.78 (missed 4) | P 1.00, R 1.00 | 7.03 → 7.03 s (7.03 s) |
| Printed, 21 cuts | P 1.00, R 0.62 (missed 8) | P 1.00, R 1.00 | 28.53 → 28.17 s (28.17 s) |

Synthetic tests cover hard cuts (including a 4-frame shot), a dissolve, dark-to-dark and
into-near-black cuts, a flash and a white frame that are not cuts, and shot splitting.

**Timing:** the cut step took 0.6–0.8 s offline and 0.6–2.0 s inside generation. The whole
motion step took 20.4 s (handwritten, previously 14.3 s) and 12.4 s (printed, previously
16.0 s). New-project generation to the caption-review pause took 539 s (handwritten) and 400 s
(printed; 420 s in the previous run).

**Per-slot motion (motion on vs off):** fake moves across missed cuts are gone, and fewer slots
need their move scaled down.

| Slot | Before | After |
|---|---|---|
| Handwritten 10 (rug → back → night) | fake move scaled to 56% | the rug shot's pan at full size; still too dark to measure (the intended curve fits 30 of 34 frames, 15 before) |
| Handwritten 12 (beach → sky) | fake +0.09 pan scaled to 35% | the sky shot's move at full size, measured at 0.96 |
| Handwritten 14 (field → sea) | 1.5% zoom-out | the sea shot's full 3.3% zoom-out, measured at 0.93 |
| Printed 1 | fake move scaled to 85%; unmeasurable | its real pan at full size, measured at 0.78 |
| Printed 3 | fake 6.6% zoom-out across a dark cut | static (both shots are static) |
| Printed 11 | fake move scaled to 58% | its real pan at full size, measured at 0.98 |

- **Unchanged slots:** they measure as before (0.93–1.04). The handwritten slot 3 under-read
  (0.23) and the printed long take (share 0.15) remain.
- **Edges:** no requested crop leaves the picture.
- **Captions:** the handwritten caption retry score is unchanged at
  251.61 / 0 / 0.47 / 2.55, 17/22.

**Limits:**

- The thresholds were tuned on these two references, so the margins are thin and this is not
  independent validation: a histogram minimum of 0.15, an edge minimum of 0.45 or a contrast
  floor of 12 each loses a cut.
- Cuts between two near-black shots of similar brightness, and dissolves longer than about 12
  frames, can still be missed.
- An A-B-A intercut whose middle shot is shorter than 3 frames reads as a flash.
- App INFO logs, including the stage timings, are not shown by the launcher, because logging is
  not configured. The timings are saved in the setting and the generation notice.

## Rotation

Added 2026-11-23. Before this, rotation was measured but never applied, and the measured angles
contained noise, subject motion and drift.

**Findings** (`evidence/motion/rotation/` in the isolated e2e copy, written before the code
changed): every reference shot's raw and smoothed angle was listed, checked by eye against
level guide lines, and tested by warping frames back with and without the measured angle.

- **Real roll:** handwritten shot 2 (a treeline turning against the sky, +2.3° over 1.7 s) and
  printed shot 13 (heads, arm and shoulder turning together, −3.1° over 4 frames).
- **Not camera roll, although above 1°:**
  - the underwater swimmer (−3.9°) and a close-up body (3.1°, swinging back): subject motion;
  - a blurred 6-frame hand (+1.7°): noise;
  - the printed 28 s long take (−26° accumulated while the framed pictures on the wall stay
    level): drift plus foreground parallax.
- **Still shots** read at most 0.05°, and noise-only shots at most 0.64°.

**Method:**

1. **Noise floor:** a shot rolls only when its smoothed angle (or its angle shake) reaches 1.0°.
2. **Evidence gate:** the shot is split into 10-frame windows (one window for shorter shots).
   Each window's last frame is warped back onto its first with the measured similarity, and
   again with the angle set to 0. The roll is kept only when both hold:
   - windows where the angle improves alignment carry at least 80% of the rotation;
   - the mean improvement is at least 0.2 gray levels.

   Otherwise the angle is zero. Subject motion and drift turn the similarity fit without
   aligning the picture.
3. **Shake:** angle shake (raw minus smoothed, RMS ≥ 0.15°) is stored as up to two sine waves
   in `shake_angle`, like `shake_x`/`shake_y`. The roll is then re-fitted with that wobble
   removed, because a wobble biases the 0.6 s fit at the shot's ends.
4. **Saved format:** settings schema version 2. Version 1 settings, measured without the floor
   and gate, still load but their angles are never applied. Turning motion off and on keeps the
   old version; generating again saves version 2.

**Applying it:**

- **Filter:** `rotate=a='<piecewise angle over n>':c=black` turns the frame clockwise about its
  centre, then the existing `zoompan` applies zoom and pan.
- **Corners:** the base zoom keeps all four turned crop corners inside the picture, 2 pixels in,
  because zoompan crops at whole pixels and `rotate` blends its edge:
  `Z ≥ |cos| + |sin|·H/W + 2|x cos + y sin|·H/W` and `Z ≥ |cos| + |sin|·W/H + 2|x sin − y cos|`.
- **Zoom cap:** the 1.35 cap still holds. A move that cannot fit (scale, pan and angle together)
  is scaled down with the existing notice.
- **Slots:** angles chain across shots: the accumulated base offset turns with the new roll.
- **Framing:** Fill turns the final canvas crop. Fit turns only the picture before its bars, so
  the bars never turn.
- **Renderer:** a rolling render uses renderer `sdr-eq-mp4-v12`. Other motion renders stay on v11,
  so their outputs are not marked outdated.

### Evidence — 2026-11-23

Synthetic tests:

| Test | Result |
|---|---|
| known roll | 3° measured within 0.2° |
| still shot and a 0.5° roll | no rotation |
| shaky roll (3° plus 1° at 4 Hz) | roll within 0.4°, shake at 4 Hz with amplitude within 0.35° |
| roll in a 5-frame shot | kept |
| rendered 3° roll | no black corner or edge pixel; re-measures within 0.3° |
| Fit portrait render with a roll | bars stay black on every frame; picture edges never black |
| corner geometry | inside the picture for landscape and portrait; a 20° roll is scaled down with a notice |

Real references (isolated e2e). Both projects were regenerated from scratch through the API,
then each export was compared per slot with a motion-off render:

| Slot | Before (PR #11) | After |
|---|---|---|
| Handwritten 3 (treeline) | pan only (0.23); its noisy "rotate" not applied | +2.29° roll applied; the intended angle explains the export better than no roll in 11/11 checked frames; the estimator reads +0.39° (the slot is dark and already under-read its pan) |
| Printed 11 (spans the kiss shot) | pan 0.98 | −3.08° applied, −3.03° measured (0.99); the angle fits 3/3 frames; pan 0.96 |
| Handwritten 7, 9, 11; printed 9 | labelled "rotate" from noise | no roll; zoom/pan unchanged (1.04, 0.98, 1.01, 0.98) |
| Every other slot | — | zoom/pan magnitude and share unchanged within ±0.01; static slots identical to motion off |

- **Edges:** requested crop corners stay inside the picture on every slot and frame (0 px
  overshoot).
- **Cuts:** still 18/18 and 21/21 with no false positives; longest shots 7.03 s and 28.17 s.
- **Captions:** the handwritten caption retry score is unchanged at 251.61 / 0 / 0.47 / 2.55,
  17/22, with byte-identical cues.
- **Timing:**
  - Motion step inside generation: 10.5 s (handwritten) and 20.7 s (printed), against a 60 s
    bound. PR #11 measured 20.4 s and 12.4 s; the spread is run-to-run.
  - Offline: 9.9 s and 11.6 s, with the gate's warps included in the estimate stage.
  - Regenerating to the caption-review pause: 355 s and 336 s.

**Limits:**

- **Thin margins:** the floor and gate were tuned on these two references. Handwritten 12 (a 72%
  share against an 80% bar) and 17 (a 0.08 gain against 0.2) are close to passing.
- **Gate scope:** the gate is per shot, so a long take with a real local roll and drift elsewhere
  gets no roll.
- **Measuring applied roll:** dark, low-texture slots under-read the applied roll, as they do pan;
  the pixel check is the stronger evidence there.
- **Baked-in borders:** footage with black borders baked into the picture that framing keeps
  (here a CapCut letterbox with a watermark, "borders retained") now shows that border's edge
  tilting. Pan and zoom only shifted it before.
- **Shake:** no reference shot passed the gate with measurable angle shake, so rotational shake is
  covered only by the synthetic test.
