# ReFrame editing interface

## Design and scope

The zkPass reference informs the near-black surfaces, sharp borders and light editorial headings. This is the existing video workspace: Reference, Footage, Style & cuts, Audio & captions, Export. The compact introduction appears only at project entry; an open project has its own header and section status. Preparation is displayed in Reference, and running jobs remain visible across sections.

Tokens in `frontend/app/globals.css`: canvas #060606, header #000000, panels #1f1f1f, raised controls #252525, hover #313131, borders #3d3d3d, white headings, #e5e5e5 body, #a3a3a3 secondary text, #c5ff4a actions/active selection/focus. Errors and limitations use labeled neutral surfaces. Media and palette colors have no tint, opacity or filters. There are no gradients or decorative glows.

Source Serif 4 supplies a real 300-weight display face; Inter Tight supplies UI text. Both are locally bundled variable fonts under the SIL Open Font License, with licenses and source links in `frontend/app/fonts/README.md`. PT Serif is not simulated at an unavailable weight. Arabic input keeps `dir=auto` and system fallback; exported caption fonts are unchanged.

Desktop grading and frame comparisons share a workbench, as do export and framing. Slot choices sit alongside the source player. Controls retain labels, keyboard focus, 44-pixel targets and bounded form widths. At phone widths navigation uses a labeled section selector, panels stack and inputs are 16 pixels. Measurements, setup and optional caption assistance use native disclosures; their components stay mounted so closing a disclosure or switching sections retains drafts.

## Preserved behavior

Both first-upload and add-more controls keep visible Choose videos buttons, multiple native selection and drop targets. They share the existing sequential project-owned queue, limits, retry/removal and first-success behavior. Original, Basic adjustments and Reference color transfer remain explicit saved modes. Clip/slot routing, reference targets, saved revisions and strength remain available. Apply, Save, replacement confirmation, source validation, polling, cancellation, playback and download are unchanged. There are no API, persistence or media-processing changes.

## Verification - 2026-10-09

Affected upload, workspace, navigation, grading, preview, sequence, preparation, assembly, reference, audio, caption and export component tests passed; frontend lint, typecheck and production build passed. Exact-final-commit CI is checked at handoff.

One browser verification session used isolated ports 3001/8001 and a copy of existing generated fixture media/results, plus an empty disposable upload project. It made no TikTok requests, model downloads or recognition/analysis benchmark runs.

- Both actual chooser entry points reported multiple selection. Initial upload selected a long-name MP4, malformed MOV and another MP4: two succeeded, one failed clearly. Failed-only retry preserved successes; add-more selected two pending files, both removable.
- Inspected entry and all five sections at desktop, tablet and phone widths (1440, 768, 390). No page overflow: document widths 1425, 753, 375 respectively, excluding the scrollbar. Phone comparison images use one column; loading and genuine invalid-link/failed-file states were inspected.
- A 100% to 99% strength draft survived switching sections, saved as color revision 5 and restored after refresh. Arabic caption text survived a section switch, explicit stale-timeline rebind/save and refresh (caption revision 2).
- Existing preview decoded actual reference/original/graded frames. Source-range playback worked. A sequence render completed with saved color/audio/captions; native playback reported running. Downloaded MP4 verified with ffprobe: 320 x 180, 3.000 seconds, video plus audio, 154543 bytes. Refresh restored the output and saved settings.
- Genuine screenshots of entry, upload/library, color comparison, sequence editor and phone workspace accompany the handoff. Screenshot/media/database artifacts were not committed. Only the isolated verification servers were stopped; user data and other servers were preserved.

## Limits

This is a presentation change. Statistical color-transfer and subject-matching limits remain. No new inference or perceptual benchmark was performed. Responsive checks used browser viewport sizes, not a physical phone; assistive-technology and every font/motion/recognition result combination were not exhaustively exercised. Still previews are explicitly regenerated after refresh; saved settings and completed video persist. The render-mode selector retains its existing Whole clip default, so select Sequence for a sequence-bound caption track.

Redesign implementation: 100%, pending architect acceptance. Video functional acceptance baseline remains 97%; image mode 0%.
