# Local video MVP release — Milestone 27

## Start / stop checklist (Windows)

- [x] README commands match Node 22/npm 10, Python 3.12 and the committed dependency locks. First setup uses `npm ci` and `uv sync --locked`; no reinstall/model setup is needed to repeat startup.
- [x] FFmpeg/FFprobe are required on PATH. Basic editing does not require OCR/transcription assets. Missing prerequisites/builds and occupied ports have actionable errors.
- [x] From the repository, `./Start-ReFrame.ps1 -Check`, then `./Start-ReFrame.ps1`. Open `http://127.0.0.1:3000` after Ready. Python-command fallback is documented for restrictive PowerShell policies.
- [x] One owned backend, frontend and descendants; correct working directories; loopback-only API/frontend/CORS. No uv sync, optional setup or download at launch. Windows reuses suspended launch/private Job Objects; POSIX reuses private process groups. No PID scanning/killing by process name.
- [x] Ctrl+C gives the servers up to ten seconds to exit, then confirms owned-tree termination. Uncertain termination is an error. Startup/server failure stops the other owned server. No project data is deleted by Stop. Forced stops retain existing interrupted-operation recovery limitations.
- [x] `-Production` uses an existing frontend build; `npm run build` first. Custom ports/API build-time configuration use the separate manual commands.

## One smoke session — 2026-10-06

Windows, Python **3.12.14**, Node **22.16.0**, FFmpeg/FFprobe **9.0.2**, Next.js **16.3.7**. Existing installed dependencies/assets were reused. Actual PowerShell launcher prerequisite checks passed, including the existing-production-build check. A second check while the smoke servers were running returned the explicit **Port 3000 is occupied** error without stopping them.

The existing launcher then started both servers in their correct directories. To preserve real projects, a launcher test wrapper replaced only the API entry command with an external test server using isolated storage; all launcher containment/monitoring/stop code and the normal frontend command were real. No fixture support or alternate storage switch was added to production code.

**Real network evidence:** one browser Check reference for the documented public sample `https://www.tiktok.com/@scout2015/video/6718335390845095173` reached the normal official TikTok oEmbed boundary and returned HTTP 200, creator **Scout, Suki & Stella**, and the sample's title. No live download was attempted; this is metadata-entry evidence, not renewed media-retrieval reliability or rights evidence. No repeated live attempt occurred.

**Labeled fixture evidence:** switch explicitly to **M27 LOCAL GENERATED REFERENCE — no TikTok request**. Only subsequent upstream metadata/retrieval used the external fixture boundary; project/upload/retention/hash checks/analyses/settings/render/serving remained real. Reused M26 generated 4-second reference and 6-second user footage; no seeded analyses or settings.

Create **M27 local release smoke** → upload → Prepare required steps sequentially (pacing unchecked) → review generated recipe → change strength to **51%** and Save revision **2** → whole-clip Render with default original audio, Original framing and disabled captions/cuts → native playback and Download → refresh → reopen from project list → same settings and output restored. Optional OCR/transcription/matching/pacing were never prerequisites or launched.

Downloaded output `e6ba3b59-4bd2-4a3c-851a-afc446416cce`: **739,936 bytes**, **640 × 360 H.264**, **6.000-second video and AAC audio**. Native playback: readyState **4**, playing, no media error. Phone **390 × 740** and desktop **1440 × 900** showed document widths **375/1425 px**, equal to scroll widths. Viewport/pointer/keyboard simulation is not physical phone validation.

Ctrl+C produced **Application shutdown complete** and **Owned servers stopped. Saved project data is retained.** Both ports were freed. Disposable storage was removed only after termination was confirmed; downloaded output/screenshots remain outside the repository/synced sources. No runtime media/database/model/secret is committed.

## Reused support and limits

- M26's accepted complete workflow verifies cut-plan + mixed audio + animated caption + square export/restoration. Existing media/lifecycle tests cover unchanged combinations, cleanup, cancellation, stale sources/revisions and atomic output replacement; no matrix rerun here.
- Font matching gives **assisted candidates**, OCR gives **reviewed text**, static appearance gives **supported suggestions**, and motion gives **supported estimates**. Arbitrary-font identity/every TikTok effect/exact visual recreation are not guaranteed. Manual editing remains available.
- Color is sampled SDR pixel measurement with heuristic creative settings; pacing can miss cuts or detect flashes/motion. TikTok full-link access remains experimental and content rights/platform permission remain unresolved. Short links/reference upload fallback are unsupported.
- Optional setup: [automatic captions](../README.md#local-automatic-captions-optional), [local OCR](REFERENCE_CAPTION_OCR.md). Missing optional dependencies do not block basic export. No new recognition inference/model download occurred.
- Real storage is **repository-root `data/`**: SQLite **schema 18** (Milestone 28 adds multiple footage clips and manual sequences) plus retained inputs/outputs. Back up database and files together while stopped. The saved-project list provides Open/Delete with explicit confirmation; failed cleanup/deletion stays visible and retryable. Unsaved drafts are not restored after refresh; saved settings/results are.
- Single user/one backend worker on loopback; no authentication/public deployment. Physical iPhone/Android, native keyboards/dialogs/touch and non-Windows full-app startup remain unverified. POSIX owned-helper behavior is exercised in CI, not a full Linux desktop workflow.
- Image mode, public deployment, multi-clip editing, semantic matching and unrestricted automatic recreation remain future scope.

## Verification and release decision

Two focused launcher tests cover prerequisite/build/port errors, real working-directory launch, confirmed owned cleanup and unrelated-process preservation. Windows tests, affected Ruff lint/format and actual startup/stop passed. No frontend behavior was changed; the current-source development server was exercised rather than rerunning prior production builds. Final exact-commit CI runs the full frontend/backend/spike suite; its result and fresh Git equality are reported at handoff.

**Milestone 27: 100% complete; defined local video MVP: 100% once final CI passes. Image mode: 0%.** This means the bounded local release criteria pass, not public deployment or universal style recreation. No reproducible editing/export blocker remained in the smoke path. Stop after Milestone 27.
