# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Keep this file current:** when a change significantly alters features, architecture, development commands or project conventions, update the affected part of this file in the same change. Skip routine changes; keep entries brief.

## What this is

ReFrame is a single-user, loopback-only Windows app: a TikTok link is the *reference edit*, separately uploaded user-owned MP4/MOV clips are the *footage*, and the backend analyzes the reference (color, pacing, captions, motion) and renders a new MP4 locally with FFmpeg. It has no auth, no external DB, no cloud keys. Never design for network exposure, multiple workers, or multiple users.

- `frontend/` — Next.js 16 / React 19 / Tailwind 4 app (all UI lives flat in `frontend/app/`, each `foo.tsx` beside `foo.test.tsx`).
- `backend/` — FastAPI on Python 3.12 managed by `uv`; flat modules, one per feature, routes all declared in `main.py`.
- `tools/` — separate uv project: developer-only TikTok retrieval CLI and caption evaluation. The API never imports the CLI entry point; shared logic lives in `backend/reference_engine.py`.
- `docs/` — per-feature method/limits docs plus `*.schema.json` files describing persisted JSON models. `docs/ARCHITECTURE.md` is the deep reference; `docs/STATUS.md` is a dated milestone log.

**Next.js warning (from `frontend/AGENTS.md`):** this Next.js version has breaking changes vs. training data. Read the relevant guide in `frontend/node_modules/next/dist/docs/` before writing Next.js code. `next dev` re-adds that block to `frontend/AGENTS.md` and touches `next-env.d.ts`; those diffs are expected noise.

## Commands

Prerequisites: Node 22 / npm 10, Python 3.12 / uv 0.12.20, FFmpeg + FFprobe on `PATH` (the launcher can also find `.tools/ffmpeg-bin/<build>/bin`; manual server/test commands need `PATH`).

```powershell
# Setup
cd frontend; npm ci
cd ..\backend; uv sync --locked          # optional: --extra visual --extra transcription (pass both on every later sync)

# Run both servers (127.0.0.1, ports 3000/8000)
.\Start-ReFrame.ps1 -Check               # verify deps/ports only
.\Start-ReFrame.ps1                      # or: backend\.venv\Scripts\python.exe backend\start_local.py
# Manual backend: cd backend; uv run --no-sync uvicorn main:app --host 127.0.0.1 --port 8000
# Manual frontend: cd frontend; npm run dev
```

Validation (mirrors `.github/workflows/foundation.yml`, which runs on Ubuntu):

```powershell
cd frontend
npm run lint; npm run typecheck; npm test; npm run build
cd ..\backend
uv run --locked pytest
uv run --locked ruff check .
uv run --locked ruff format --check .     # line length 100, rules E/F/I
cd ..\tools
uv run --locked python -m unittest discover -s tests -p 'test_tiktok_reference.py'
```

Single tests:
- Backend: `uv run --locked pytest tests/test_framing.py::test_name` (or `-k pattern`). Many tests generate real media and need FFmpeg on `PATH`.
- Frontend: `npx vitest run app/framing-controls.test.tsx` (or `-t "name"`).

Config: `frontend/.env.local` (copy from `.env.example`) holds `NEXT_PUBLIC_API_BASE_URL` (default `http://127.0.0.1:8000`; embedded at build time). Backend CORS uses `REFRAME_ALLOWED_ORIGINS`.

## Architecture

**Persistence.** `backend/projects.py` owns `data/reframe.sqlite3` (`DATA_DIR` = repo-root `data/`, git-ignored). The schema is versioned via `PRAGMA user_version` (`initialize()` lists the accepted versions and sets the current one; README and `docs/ARCHITECTURE.md` state the current number, so update them when bumping it); startup migrates earlier versions *additively* and must preserve existing projects, media and results — when adding tables/columns, bump the version and add a migration step there, and unknown versions must refuse to start. Each connection is created, used and closed on one worker thread; blocking DB/filesystem work runs off the event loop. Tests isolate storage by monkeypatching `DATA_DIR` on the relevant modules to `tmp_path`.

**Storage safety.** Retained media lives under `data/projects/<UUID>/` with generated names; original filenames are display-only and API responses never contain filesystem paths. Uploads follow staging → validated → same-filesystem move → ready; operations use `data/<kind>-staging/<operation UUID>/`. Startup reconciliation (`recover()` functions) cleans abandoned staging, verifies digests and turns interrupted `running` operations into failed. Preserve these lifecycles when touching file handling.

**Long-running operations.** Expensive work (reference retrieval, color/pacing/footage analysis, rendering, caption OCR, automatic generation) follows one pattern, pioneered in `reference_jobs.py`: POST starts an operation (idempotent; a single global expensive-work owner), the work runs in a worker thread with a cooperative stop event and hard time/size bounds, GET reads status from SQLite, and the frontend polls (shared hook `frontend/app/use-media-operation.ts`). External processes go through `reference_engine.run_command`, which contains children in a Windows Job Object / POSIX process group; unconfirmed termination sets `cleanup_safe = 0` and blocks further work until manual review. Exceeding any bound fails the whole operation rather than returning partial results.

**Observations vs. instructions.** Analysis results (style/pacing blueprints, footage color, OCR proposals) are read-only observations. Separate *saved, revisioned* user settings (color recipe/grading, edit plan, sequence, audio, captions, framing, fonts) are what rendering consumes. Writes and renders carry `expected_*_revision` fields and the server returns 409 on mismatch; renders resolve all saved revisions server-side and mark outputs outdated when inputs change. Never let analysis output silently become render input.

**Pipeline.** TikTok link → `references.py` (oEmbed metadata snapshot) → `reference_jobs.py`/`reference_engine.py` (yt-dlp media retrieval) → analyses (`color_analysis.py`, `pacing_analysis.py` (scdet plus the camera-motion cut detector, and the reference rhythm via `beats.py`; slots follow every cut), `footage_analysis.py`, `reference_motion.py` (caption animation), `camera_motion.py` (per-shot camera zoom/pan/roll/shake, applied per sequence slot; roll only above a noise floor and evidence gate; shares its cut detector with pacing), caption OCR/`automatic_captions.py`) → saved settings → `edit_plan.py` / `sequence.py` / `assembly.py` → `video_render.py` (FFmpeg, H.264 30 fps, 720×1280 or 1280×720). `generation.py` orchestrates the one-click automatic "Generate video" flow over these stages; the Customize editor (`guided-workspace.tsx` and section components) exposes them individually.

**Models and schemas.** Request/response/persisted models are Pydantic (most derive from `color_analysis.Schema`, often imported as `color`; persisted payloads carry `schema_version` literals). The matching `docs/*.schema.json` files document those models; keep them in sync when changing a persisted model.

**Pages and header navigation.** The app is currently one route (`app/page.tsx` renders `AutomaticMaker`; project state lives in the query string). Whenever you add a new page (`frontend/app/<route>/page.tsx`), add a link to it in the site header, the `header.topbar` element, using Next's `Link`. There is one header component, `AppHeader` in `app-header.tsx`. `AutomaticMaker` renders it on every screen, including Customize, and the embedded `Workspace` portals its "Saved projects" menu into the header's menu slot. In Customize the header holds the only exit ("Close Customize"), and `GuidedWorkspace` portals a Working badge into the menu slot and a "Render video" shortcut (opens Export) into the action slot. A standalone `Workspace` renders its own `AppHeader`.

**Customize editor shell.** `GuidedWorkspace` lays out a section rail, a stage (the `preview` and `strip` portal targets) and an inspector with a sticky `footer` portal target for Save/Discard. Rail dots mark "Needs attention" and "Unsaved"; unsaved edits show as a warning banner at the top of that section's inspector. Below 1100 px the rail is a tab bar; below 600 px it is a native select.

**UI styling.** Design tokens (colours, radii, text sizes, 4 px spacing) live in `globals.css` under Tailwind 4 `@theme static`. The older variable names (`--canvas`, `--panel`, `--text`, …) are aliases of them. Inter Tight is the only typeface.
- **Primitives:** `app/ui.tsx` holds `Button`, `Badge`, `Banner`, `Panel`, `Field`, `EmptyState` and `ProgressSteps`. They render native elements and pass every prop through, so accessible names and roles stay the caller's.
- **Colour pairs:** text needs at least 4.5:1, and control borders and focus need at least 3:1. Control outlines use `--color-border-strong`, never the decorative `--color-border`.

**Optional extras.** Visual matching (`visual_matching.py`, onnxruntime), camera motion detection (`camera_motion.py`, NumPy from the visual extra) and transcription (`transcription.py`, faster-whisper) are optional; code must degrade gracefully when they or FFprobe are absent (`workflow_capabilities.py` reports per-project workflow availability). Model setup is explicit — never add automatic downloads.
