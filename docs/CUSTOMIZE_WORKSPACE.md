# Customize workspace — 2026-10-17

## Layout rationale and capability inventory

The automatic creation flow remains Reference → clips → Generate → result. Customize is optional. The rendered audit found five broad sections with the player, color/cut controls and caption tools dispersed through long forms. The replacement uses stable playback, independent task destinations and explicit selection scope.

Practical references: [Premiere Properties](https://helpx.adobe.com/premiere/desktop/edit-projects/intro-to-editing/edit-video-using-the-properties-panel.html) exposes controls for a selected timeline item; [Resolve Edit](https://www.blackmagicdesign.com/products/davinciresolve/edit/) connects a timeline/viewer with the selected clip or subtitle inspector. ReFrame adopts selection/context, without adding their multi-selection, effects or NLE infrastructure.

| Destination | Existing capabilities retained |
| --- | --- |
| Reference | Saved metadata/link, experimental retrieval/retry and source details, coordinated preparation and optional pacing preparation |
| Media | Original clip inspection, multiple-file sequential queue, per-file errors/retry, clip library, rename/removal and budgets |
| Cuts | Persistent sequence strip; selected slot assignment, trim/range playback, reorder/add/remove/save; automatic assembly, measurements/visual matching, locks/reuse and proposal review; named single-clip cut-plan group; estimated pacing |
| Color | Original/Basic/Transfer modes; project-reference match per source or assigned reference shot per slot; strength and named advanced tone/chroma controls; Basic recipe/regeneration; real before/after frame requests; immutable color measurements/comparison |
| Captions | Cue list and selected text/timing editor, validation filter, SRT preview/import, enable/timeline/rebind; track defaults and per-cue overrides, all fonts/custom upload, colors/size/outline/shadow/placement/alignment, Arabic and supported animation; selected-cue reference evidence and actual saved-caption/motion previews; named assisted font/appearance/motion tools; transcription review; incomplete recognition proposal/corrections/verification and direct retry/Off recovery |
| Audio | Original/reference/mix/mute, gains/offset, stream availability, reuse notice and explicit save; optional readiness details |
| Framing | Original/fixed formats, Fit/Fill, bounded crop axes, draft reset, save and source geometry |
| Export | Saved mode/revisions and actionable blockers, real render/retry, retained prior output, playback/seek/download; refresh restores last export's render mode |

Nothing is a mandatory step or a fake Save all. Each panel retains its original save/revision checks. A ready sequence does not require a legacy cut plan. Source-clip color settings and slot-specific shot targets have distinct labels. Slot/cue selection seeks the previous actual output where possible; it does not synthesize a draft preview. Selecting a source clip does not imply multiple-object editing.

Editors stay mounted while categories are hidden; portals place the existing output media and sequence strip in stable surfaces. Cue/slot selection is independent; switching categories does not reset drafts or playback. The existing recognition correction component keeps its state while its presentation moves between result and Customize. It edits a recognition proposal, separately from Save captions for the actual track. Protected correction replacement stays explicit.

Desktop (1440px) has navigation, monitor/strip and a separately scrolling inspector. At 768px, navigation becomes a category picker with full-width playback and editor. At 390px, secondary copy collapses, the player is compact, controls remain in document flow, and only the sequence/cue lists scroll internally. Media retains actual colors and aspect ratio. No hover-only operation, fake CSS grade or live crop preview.

## Browser verification

One workflow used a disposable **copy** of the existing persisted three-cut generated fixture, served on ports 3001/8001. Neither user project was used for browser editing. The browser file-chooser event timed out; a second 3-second generated MP4 was therefore uploaded through the normal **isolated** API before the source-replacement walkthrough. This is not browser upload verification.

Verified through actual controls:

- Open saved result → Customize; select slot 2, replace its source with a long-named second clip, trim its start to **0.2 s**, switch Audio/Cuts with a draft, then save sequence **revision 3**.
- Prepare the changed assigned shot using the existing bounded match action; change slot strength **70% → 69%**, switch categories, and save color **revision 2**.
- Edit cue 1 to two lines, set its per-cue size **5.1%**, switch categories, explicitly confirm sequence rebinding, save captions **revision 2** and inspect retained reference evidence.
- Save original volume **99%**, audio **revision 2**; save Square/Fit **720 × 720**, framing **revision 1**.
- Render the sequence, play native video, download MP4, refresh and restore the edited range, strength, caption text/size, audio/framing, sequence render mode and the exact output URL.

Output **4d784b36-4453-494a-b110-27f2394ac79b**: **3.000 s**, **720 × 720**, **90 decoded frames**, **143,960 decoded mono 48 kHz audio samples**; SHA-256 `c1c897ed43311b0407cd242982d9a527bfd3a6b860c4547d482847b48059c99e`. Playback reported `paused=false`, `readyState=4`; selected-cue seeking displayed both edited caption lines on the actual padded final canvas. Browser download completed. This contains the deliberate fixture edit, not the handwritten project's protected correction, and is not new automatic-wording acceptance.

Actual local screenshots under the external `caption-workspace` evidence directory: `desktop-workspace.jpg`, `selected-slot-color.jpg`, `captions-desktop.jpg`, `tablet-workspace.jpg`, `mobile-workspace.jpg`, `mobile-captions.jpg`, and `real-partial-review.jpg`. Width checks found no page overflow: 1440/1425, 768/753 and 390/375 (viewport/document scroll width; scrollbar occupies 15px). The real project's partial proposal was inspected without editing it. A staging/publication failure remains a visible failure, never a no-caption result.

Browser findings fixed: optional null-only font keys were omitted in persisted per-cue appearances and rejected by the frontend; the validator now accepts missing/null identifiers while still rejecting unexpected IDs. Desktop navigation inherited a five-column grid inside its narrow rail; it now has one column. Selection survives sequence save, preview choices follow slot context, and refresh defaults to the last actual render mode.

Focused tests cover navigation/drafts, independent selections, correction portal draft preservation, saved-font compatibility, render-mode restoration and existing editors. Frontend lint/typecheck/build passed; exact-final-commit CI provides all suites. Specialized model/font inference was not repeated for every advanced tool. Device sizes are browser viewport checks, not physical-phone testing. UI implementation/verification: **100% of this scoped redesign**; no claim that all caption recognition or the whole product is complete. Image mode **0%**.


## Acceptance audit — 2026-10-18

Rechecked the actual rendered isolated fixture at **1440 × 1000** and **390 × 844**. Selected slots/cues have pressed states, explicit names and local controls; color scope distinguishes a source clip from one assigned slot. Desktop output remains beside the independently scrolling inspector. Mobile has a category picker, compact video and document-flow controls (not a physical-phone check). The player is the previous export; Update preview requests saved-setting stills; each Save persists only its panel; Export produces replacement video. Advanced appearance/font/motion, recognition, cut-plan and grading controls remain reachable. Existing draft/playback/save tests and the previous supported export remain applicable.

Two small display corrections: default Cuts/Captions scope no longer misleadingly says “Whole project” while a local slot/cue editor is open; reference/source shot times use three decimal places instead of long binary fractions. No editor state or saving logic changed.

**Native picker verified, separately from the historical API upload:** in disposable project `2a22fdc1-d2d0-4e1b-b4a8-c00b9139b232` on 3001/8001, actual Choose videos returned `multiple=true`; two existing generated MP4 files entered the queue and both reached succeeded through the real sequential upload endpoints. Customize → Media → Add footage → Choose videos also returned `multiple=true`; a third MP4 was selected, queued and uploaded. Refresh restored **3/10 clips**, all three names and retained metadata. No API file upload substituted for these actions. The supported chooser's file-return calls took approximately **411 s** and **10,307 s**; this browser-tool delay is distinct from application upload processing. No further picker retries were needed.

The initial disposable creation unexpectedly attempted oEmbed and received rate limiting. There was no media retrieval. Subsequent creation used explicitly local fixture metadata and generation was blocked in that isolated verification server, preventing further TikTok requests. This checks picker/queue/retention, not live project creation or a new generation/export.

Genuine JPEG artifacts are delivered as images, with a separate local gallery, rather than an empty screenshot table: `desktop-overview-1440.jpg`, `selected-slot-color-1440.jpg`, `selected-cue-1440.jpg`, `mobile-workspace-390.jpg`, `mobile-cue-390.jpg`, `native-upload-restored.jpg`. Page scroll widths were **1425/1440** and **375/390** (15 px scrollbar), with no horizontal page overflow. The screenshots show the supported saved three-cut fixture, not a complete handwritten result. UI implementation **100%**, scoped browser verification **100%**; caption recognition acceptance remains a separate, incomplete decision below the temporal report.
