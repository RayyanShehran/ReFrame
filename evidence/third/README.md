# Third reference: generalisation test (truth labelled 2026-11-24, before any app analysis)

Reference: `https://www.tiktok.com/t/ZPNBcEBoe/` → `@ahdo_c/video/7568129756005207316`.
It is a film-dialogue edit: 38.75 s, **1440×1080 landscape, HEVC, 60 fps**, AAC 44.1 kHz stereo.
The first two references were portrait. Words are revealed one at a time as captions.

All truth is on the 30 fps decode the app uses (`setpts=PTS-STARTPTS,fps=30`, frames 0–1161).
The media, contact sheets and frame strips are not committed: they are copyrighted film stills,
and the scripts below regenerate them from the reference.

## Truth

### Cuts (`truth.json`)

There are 19 cuts, so 20 shots. Each cut is the first frame where the previous shot is gone:

> 64, 185, 232, 281, 357, 423, 468, 513, 623, 660, 761, 819, 899, 919, 987, 1037, 1084, 1125, 1138

- **Flash transitions.** Four cuts go through a flash before the new shot appears:
  - 357: a yellow light leak (357–362) that dissolves into the next shot at 363;
  - 919: a 2-frame white flash, new shot at 921;
  - 1037: a 2-frame white flash, new shot at 1039;
  - 1084: a 3-frame white flash, new shot at 1087.

  A detection anywhere from the cut to the new shot's first frame + 2 matches.
- **Hard cases:**
  - dark-to-dark cuts: 660, 761, 819;
  - a cut to black for the last 0.8 s: 1138.
- **Uncertain, so not counted either way:**
  - 617: the projector picture inside the theatre shot whites out for 617–622 and the
    silhouettes shift a little;
  - 939, 950 and 963 in the 8 mm flower-field home movie: a double exposure, a framing jump, and
    an overexposure flash with the same scene on both sides.

### Beats (`rhythm-app.json`, `rhythm-windows.txt`, `wave.png`)

**There is no clear beat (max clarity 0.25).**

| | |
|---|---|
| App `beats.analyze` | 114.8 BPM, strength 0.25, threshold 0.5, so `clear=False` and no beats |
| Tempo in the 8 s windows | 79–178 BPM |
| Clarity in the 8 s windows | 0.12–0.25 |
| Whole-track autocorrelation | at most 0.09, against about 0.9 for a click track |

- The waveform plot shows speech and sung vocals over sparse, irregular onsets, with no grid to
  label beats on.
- Truth cuts sit on stronger onsets than random times: median envelope 0.97 against 0.53.
  8 of 19 cuts are above the random 75th percentile. The editor cut on sounds, not on a beat.

### Camera roll

**No confident real roll** (`eye.py`: 4 frames per shot with level guides).

- **Shots 0–13** are steady close-ups and silhouettes. Their framing stays level; the movement is
  the subjects, or the slow push in the theatre shot.
- **Shots 14–17** are handheld 8 mm footage (field, pool, walking child, arch). They wobble, but
  any roll there is below what I can judge by eye, about 1–2°. These shots are uncertain.

### Captions (`captions-truth.json`)

There are 45 displayed phases, revealed word by word, covering 25.7 s. They were read frame by
frame from caption-band crops (`capsheet.py`).

| Frames | Style |
|---|---|
| 0–64 | yellow subtitles, bottom |
| 114–185 | dark blue subtitles, bottom, low contrast over a pale sky |
| 185–899 | white lyrics, screen centre |

There are no captions after frame 899.

Uncertain:
- 520–535: white text over the bright projector light;
- 617–622: the text is washed out by the white flash, but labelled as continuous.

`score.py` scores a cue file against these phases with the unchanged PR #3 scorer
(`../score_captions.py`). Perfect cues score 88.53 / 0 / 0 / 0 with 90/90 boundaries.

## Scripts

All need FFmpeg on `PATH` and the e2e backend's Python.

| Script | What it does |
|---|---|
| `sheets.py` | contact sheets and frame strips |
| `eye.py` | roll by eye |
| `capsheet.py` | every-frame caption bands |
| `captions_truth.py` | writes `captions-truth.json` |
| `beat_plot.py` | beat windows and the waveform plot |
| `score.py` | caption scoring |

## Findings (2026-11-24/25)

Two fresh UI projects were run in the isolated e2e copy, on the same reference and the same 7 IDK
clips, each to an export:

| Run | Code | Project |
|---|---|---|
| Before | `main` 6b32e84 | `14470f12` |
| After | this branch | `ce1dec41` |

Both runs used Reference audio and Recreate reference captions, then "Retry caption reading"
once and "Use detected captions". The first two references were then regenerated on this branch
(`regress/`).

### Results

| | Handwritten | Printed | Third: before | Third: after |
|---|---|---|---|---|
| Cut recall (slots) | 18/18 | 21/21 | 18/19 (missed 819) | **19/19** |
| Slot precision | 1.00 | 1.00 | 0.75 | 0.83 |
| False slot cuts | 0 | 0 | 6 | 4 (flash edges) |
| Slots | 19 | 22 | 26 | 25 |
| Clear beat / snapping | no (≤ 0.36) / off | no / off | no (0.25) / off | no (0.25) / off |
| Slot music mode | reference | reference | **original** | reference |
| Camera motion copied | 19 shots | 22 shots | **none** | 27 shots, 1 still |
| Applied vs intended | unchanged | unchanged | — | 19/19 measurable match |
| Rolls applied | +2.29° (1 real) | 3.08° (1 real) | — | 2 (1 plausible, 1 false) |
| Captions C/W/M/E | 251.61 / 0 / 0.47 / 2.55 | — | 30.73 / 2.27 / 55.53 / 4.27 | 30.33 / 2.13 / 56.07 / 4.27 |
| Caption boundaries | 17/22 | — | 7/90 | 6/90 |

Notes on the table:
- **First two references:** their figures are after this branch, and they are identical to `main`:
  - the same slot cuts and slot count;
  - camera motion shots, curves, shakes, moves and rolls identical;
  - handwritten retry cues byte-identical.
- **Third reference, pacing only** (precision counts all found cuts, with uncertain frames
  ignored):
  - before: 18/19 found, precision 0.69, with caption false cuts at 751 and 833;
  - after: 19/19 found, precision 0.76.
- **Applied vs intended motion:**
  - 2 more moving slots read as zero because their footage is dark or black; the on/off
    exports do differ there.
  - Both roll slots apply their angle (−2.10° vs −2.15°, +4.49° vs +4.44°).

### What generalised

- **scdet plus the cut detector:** 19/19 cuts on landscape, HEVC, 60 fps media, including
  dark-to-dark cuts. scdet alone found 15/19.
- **The 30 fps grid, the shared output/reference timeline and the slot builder:** these worked
  unchanged on 4:3 landscape. The export is 960×720.
- **Beats:** no clear beat was correctly reported (max clarity 0.25), so snapping stayed off, at
  offset 0 and at +0.25 s (`offset-run.txt`: 0 cuts moved, identical boundaries).
- **The roll gate:** it rejected 7 of the 10 shots whose angle passed the 1° floor. One of them
  is a 5.1° hand close-up (subject motion).
- **Motion application** matches the saved curves where it can be measured.

### Fixes (each with a failing test first)

1. **Caption text drove the cut detector** (751 and 833 false, 819 missed).
   - **Cause:** on dark, soft shots, caption letters are the sharpest structure, and the edge
     correlation is gradient-weighted. 751 scored edge 0.70; 819 scored 0.27 under the same
     caption.
   - **Fix:** cap gradients at each frame's 95th percentile, and require the change to survive
     a ±1 px alignment. The alignment gate was needed because the cap pushed a stepping
     synthetic pan from 0.746 to 0.76, over `CUT_STRONG` 0.75; its old margin was only 0.004.
   - **Margins** (`clip-margins.txt`, `detector-check.txt`):
     - 819 and 833 are fixed for caps of p99.5–p93;
     - 751 only for caps of p95–p93 (thin);
     - p92 and below add a flash-edge cut;
     - the pan scores 0.33 after alignment, against the 0.35 threshold;
     - the first two references' detected cut lists are identical at every cap from p97 to
       p91, and their lowest truth-cut edge change is 0.53.
   - **Versions:** detector v2, pacing `scdet-cut-detector-v3`.
2. **One unmeasurable shot dropped camera motion for the whole reference.**
   - **Cause:** the hazy 8 mm shot 920–968 drifted to x = −2.11 heights. The tracker re-anchors
     on every frame when steps exceed 6 px, and its errors add up. The Keyframe bound of ±2
     then raised a `ValueError`, and generation copied no motion at all.
   - **Fix:** a shot beyond the model's bounds gets a still curve, and the others keep theirs.
3. **A new project's slots never synced to the chosen audio.**
   - **Cause:** generation saves the audio choice after building slots, so a first generation
     read the default `original`. The PR #13 runs regenerated projects whose audio was already
     saved, which hid this.
   - **Fix:** generation passes its requested mode (`Generate.audio_mode`).

### Not fixed (cause found, no principled change)

- **Flash and light-leak edges become 3–6-frame slots** (362, 1039, 1087) and 968 is a false
  cut. scdet fires on both edges of a flash.
  - Brightness and structure do not separate these from real montage shots
    (`flash-pieces.txt`): printed shot 75–80 is +47/+55 brighter than its neighbours, with std
    55; flash 1036–1039 is +57/+41, with std 40.
  - Fixing this needs transition-type recognition.
- **False roll on the kiss shot 899–919** (2.4° measured, 2.1° applied): the heads tilt and
  fill the frame.
- **The pool shot's 9.9° roll is plausible but overstated:** about 5° by eye.
- **8 mm tracking:** shot 968–986 still pans −0.87 heights, which is probably drift.
- **Captions** score about 30.5 / 2.2 / 55.8 / 4.3 of 88.5 word-seconds:
  - OCR exports only each finished line, 0.27–0.43 s late, and never the growing word-by-word
    phrases;
  - it misses "but who knows" (white text over a bright window) and "to the grave" (white over
    saturated blue);
  - both reads took about 280 s of the 300 s caption bound and ended "incomplete";
  - the cause of the dropped partial phrases is not confirmed, so caption code was not
    changed.
- **On-beat share of exported cuts:** not applicable, because there are no clear beats.

### Timing (fresh project, after; before in brackets)

| Stage | Time |
|---|---|
| Reading reference, including download and pacing | 29.7 s (41.7) |
| Choosing your clips | 119.8 s (122.8) |
| Matching the look, including camera motion | 199.5 s (198.1) |
| Recreating captions, first read | 278.1 s (280.9) |
| Caption retry | 279.2 s (275.5) |
| Render after acceptance | 35.1 s (35.7) |

- **Pacing:** scdet 8.5 s, detector 5.3 s, rhythm 0.5 s.
- **Camera motion:** 29.1 s (decode 3.4, cuts 2.1, estimate 23.6) of its 60 s bound. The run
  before failed outright.
- **The regenerations, to the export:** handwritten 441 + 46 s, printed 367 + 46 s.
