# Consistent project framing

## Saved behavior

New automatic generations with untouched framing use the retained reference's display aspect, constrained to the existing 1280 × 720 / 720 × 1280 size limits without changing its aspect beyond even-pixel rounding. Rotation and pixel aspect are respected. Existing saved choices, including an explicit Original reset, are retained. No reference download is involved.

The existing schema-1 framing JSON is extended additively: `reference` format, server-derived `canvas`, `automatic` fit mode, `bars`, UUID-keyed `slots`, and server-owned `decisions`. Old records/specifications still default to Original/Fit/center, bars Off and no overrides. The existing table and optimistic framing revision remain; no database migration or second worker is needed. Clients cannot submit a canvas, detections, source hashes or algorithm versions.

Each decision records algorithm `stable-borders-center-v1`, footage UUID/hash, normalized 30 fps source-frame range, normalized left/top/right/bottom trims, whether evidence was sampled, and an explanation. Whole-clip/legacy cut rendering has a clip-wide route; sequences have one fixed route per saved slot. Only matching sampled evidence is reused. Turning Auto back on after Off/manual correction samples again. Changed ranges/sources require an explicit framing save; unaffected matching routes can reuse evidence.

## Detection and framing policy

- Five distinct midpoint frames from each applicable range, grayscale, aspect-preserving reduction to a maximum 320-pixel edge. Short ranges without five distinct frames retain their borders.
- Require at least four sufficiently lit samples (central-region 80th percentile ≥40/255). A near-black edge has at least 99.5% of its pixels ≤12/255. Require ≥3% edge extent and stability within 2% of the axis across usable frames; retain a 0.8% guard band. Reject crops removing more than 65% of either axis. Dark/fading scenes and changing/ambiguous bounds retain content.
- Bright border lettering/logos interrupt detection. This deliberately retains uncertain regions rather than asserting they are disposable. It can leave real bars behind; manual trim controls are the correction path. Persistent black scenery can still resemble padding: review is necessary.
- Automatic framing uses fixed **center** Fill only when it retains at least 72% of the active image; otherwise it uses centered Fit with black padding. This 28% loss limit is a conservative geometric policy, not subject detection. No object tracking, saliency estimate, moving pan or speed change is claimed.
- Manual per-slot Fill/Fit/Automatic, left/right and top/bottom position, 1–2× Fill zoom, Auto/Off bar handling and explicit border fractions override defaults. Reset selected slot changes only its draft back to Automatic. Save persists it; retries never replace it. Crop axes without movement and zoom in Fit are disabled. Off disables both detected and manual border trims while retaining the saved correction values for a later Auto choice.

All slots have one identical output canvas, square pixels, proportional scaling and even-aligned crops. Fit can require different padding thicknesses for different source shapes. Keeping every source pixel cannot also guarantee identical bars or full-screen footage.

Framing preparation has a 120-second overall deadline, 20-second sampling process cap, at most 61 routes (60 slots plus whole source), five samples per route, ≤512,001 bytes of sampled tool output per route and 16 MiB staging. The inherited 100 ms size watchdog permits overshoot; it is not a filesystem quota. Owned process containment, joined cancellation, explicit cleanup failures and quarantine use the existing infrastructure. Existing render bounds are unchanged.

## Preview/export order and controls

Source autorotation → square-pixel normalization / stable active-image crop → proportional resize → saved grade → final crop or black padding → final-canvas captions → existing encoder. Whole, sequence, caption still/motion and color previews reuse the same framing transform. Already assembled slots are not framed twice. Captions retain their saved output-timeline text, timing, font/style and placement, independent of source cropping.

The existing Framing destination separates project defaults from selected-slot controls. It shows an actual saved-settings frame, its framing revision and decision explanation. Unsaved drafts hide that image and state that the preview is pending; Save and Update preview are explicit. Slot changes preserve all drafts. Basic recipes continue using the existing Color before/after preview.

FFmpeg behavior checked against installed **9.0.2** and the official [scale](https://ffmpeg.org/ffmpeg-filters.html#scale), [crop](https://ffmpeg.org/ffmpeg-filters.html#crop), [pad](https://ffmpeg.org/ffmpeg-filters.html#pad), [fps](https://ffmpeg.org/ffmpeg-filters.html#fps) and [autorotation](https://ffmpeg.org/ffmpeg.html#Video-Options) documentation. Detection uses decoded pixels, not ffprobe metadata or cropdetect's black threshold alone.

## Focused evidence — 2026-10-18 UTC / 2026-10-19 Dubai handoff

The reusable real fixture includes landscape, portrait, square, horizontal and vertical embedded bars, a genuinely dark image, and an off-center red subject on blue. A seven-shot **720 × 720 / 7 s / 210-frame** export verifies bar removal, dark-image retention, fixed noncentral zoom/crop, Fit fallback and final-canvas captions. The actual red/blue crop boundary agrees between preview and export within one decoded test pixel. Comparing RGB bytes alone was not portable to CI's FFmpeg; the final regression compares the visible geometry directly, with both colors required. Existing geometry tests retain SAR/rotation, no-upscale Original and source-axis checks.

The retained user project has **15 slots**, sequence revision **3**, manual caption revision **3**, reference audio revision **2**, grading revision **1**. Its replacement is **1280 × 720, 39.633333 s, 1,189 decoded frames, 1,902,384 decoded audio samples**. SHA-256: `ec581b7f98eec9e691a4a8dcb7e1399348a12d2880985bd62622e998c4680162`. Output ID: `0c0a6856-01b7-4b64-a13e-7c92061c7baf`.

Native source inspection found a square image within a 478 × 850 padded upload. Four ranges (shots 1, 3, 6, 14) received explicit reviewed top/bottom trims of **186/850**, retaining the full square scene and its burned-in dialogue; the logo above the active image is excluded. These are disclosed manual framing corrections, not automatic detections. Portrait, dark-square and three-panel footage retain conservative Fit. Source text already burned into footage remains part of the picture.

The replacement's decoded PCM SHA-256 is identical to the archived captioned output: `c0b8d408f3ed7d90f6ca32039743acddf4883601ec79b60a082a6824aff360f6`. Every non-framing render specification field is unchanged. Caption/generation/sequence/audio/grading API snapshots and all source-file hashes remain unchanged. Only framing, render records and the normal project modification timestamp changed. Both preceding captioned and captions-off MP4s were archived outside managed media before replacement.

Inspected 79 distributed output frames plus one native frame per saved cue and before/after comparisons. Verified full decode, download hash, HTTP 206 seeking and saved-data rereads. Interactive browser playback/refresh was **not** exercised: the existing browser-tool access restriction was respected. UI checks use component tests, lint, TypeScript and production build; final CI is checked for the exact handed-off commit.

Automatic framing **100% implemented**; conservative embedded-bar handling **100% implemented** with the documented ambiguity/manual limits; verification **90%** (interactive browser check unavailable). These are engineering scope estimates, not universal visual-quality guarantees. Automatic handwritten recognition remains unsupported for the earlier reference. Image mode **0%**.
