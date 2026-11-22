# Camera motion copying

Copies the reference's camera moves (zooms, pans, punch-ins and shake) onto the user's clips in
sequence exports. Design note written before implementation; measured evidence is appended below
once it exists.

## Data model

- **Observation:** method `phase-similarity-v1` measures one curve per reference pacing shot.
  `ShotMotion` holds the shot's 30 fps frame range, a compact intentional-move keyframe list
  (`frame`, `scale`, `x`, `y`, `angle`) and per-axis shake waves (`amplitude`, `frequency`,
  `phase`). Every value is relative to the shot's first frame. Translations are fractions of frame
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
frame number, plus the shake sinusoids. Rotation is measured and stored but **not applied**,
because zoompan crops axis-aligned rectangles.

## How it combines with framing

- **Fill** (and Automatic → Fill): motion runs on the final canvas after framing's crop.
- **Fit:** motion runs on the scaled image before framing pads it, so the bars never move or grow.
- **Original:** motion runs on the scaled frame.
- **No black edges:** the base zoom `z0` is the smallest zoom whose crop window stays inside the
  frame for the slot's largest translation and smallest scale. It is capped at 1.35; beyond that
  the slot's move is scaled down to fit and a notice is recorded. Expressions are also clamped to
  the frame, so a crop can never leave the source image.
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

- **Pacing:** missed cuts and very long "shots" are pacing limits. A 28.5 s printed shot copies
  15% of its accumulated move, and the reference's shot boundaries decide the curves.
- **Large moves:** moves beyond the 1.35 base-zoom margin are scaled down.
- **Rotation:** rotation is measured, not applied.
- **Camera vs subject:** detection cannot tell a camera move from a large subject that fills the
  frame. Without a frame-by-frame camera truth for real footage, the real-reference curves are
  plausible estimates, not verified ground truth.
