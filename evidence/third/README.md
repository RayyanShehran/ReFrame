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
