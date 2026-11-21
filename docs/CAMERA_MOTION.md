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
