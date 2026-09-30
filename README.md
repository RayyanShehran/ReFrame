# ReFrame

ReFrame takes a TikTok link as the reference edit and a separately uploaded user-owned clip as footage. The app inspects public TikTok reference metadata, then validates the technical details of one uploaded clip. Saved projects retain one validated user clip locally and a snapshot of reference metadata. The app does not edit or analyze the style of either video. The developer-only media feasibility spike is documented in [TikTok reference feasibility](docs/TIKTOK_REFERENCE_FEASIBILITY.md).

## Prerequisites

- Node.js 22 LTS and npm 10 (developed with Node 22.16.0, npm 10.9.2)
- Python 3.12 and uv 0.12.20 (developed with Python 3.12.14)
- FFprobe on `PATH` for clip inspection, and FFmpeg on `PATH` to run the generated-media integration tests. Install the [FFmpeg Windows build linked by FFmpeg.org](https://ffmpeg.org/download.html#build-windows) and add its `bin` directory to `PATH`, or use `winget install "FFmpeg (Essentials Build)"` where WinGet is available. On Ubuntu: `sudo apt-get update && sudo apt-get install -y ffmpeg`.
- PowerShell

Dependency versions are pinned in `frontend/package-lock.json` and `backend/uv.lock`. No Docker, external database server, cloud credential, or AI key is required. SQLite is provided by Python. The health and reference metadata endpoints work without FFprobe; the clip endpoint returns a safe unavailable error when it is missing.

## Setup in PowerShell

```powershell
cd C:\Projects\Reframe
Copy-Item frontend\.env.example frontend\.env.local
cd frontend
npm ci
cd ..\backend
uv sync --locked
```

`frontend/.env.local` sets `NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000` by default. This address is public browser configuration; never put a secret in a `NEXT_PUBLIC_` variable. `backend/.env.example` documents `REFRAME_ALLOWED_ORIGINS`; set it in the backend PowerShell window if changing origins. The defaults permit `http://localhost:3000` and `http://127.0.0.1:3000`.

## Run

In one PowerShell window:

```powershell
cd C:\Projects\Reframe\backend
uv run --locked uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

In another:

```powershell
cd C:\Projects\Reframe\frontend
npm run dev
```

Open <http://127.0.0.1:3000>. To change ports, pass another `--port` to uvicorn or `npm run dev -- --port 3001`, update `NEXT_PUBLIC_API_BASE_URL`, and set `REFRAME_ALLOWED_ORIGINS` in the backend shell to include the frontend origin (for example `$env:REFRAME_ALLOWED_ORIGINS='http://127.0.0.1:3001'`). Restart both applications after changing environment values.

Paste a full HTTPS TikTok video URL such as `https://www.tiktok.com/@scout2015/video/6718335390845095173`, select **Check reference**, then **Use this reference**. The app shows public title and creator metadata from TikTok's [oEmbed API](https://developers.tiktok.com/docs/en/embed-videos). Bare `tiktok.com` and `m.tiktok.com` video links are normalized to `www.tiktok.com`; tracking parameters are removed. Short links are not supported. An unsaved reference selection lives in browser memory. Create a named project to save its metadata snapshot; the project ID in the browser URL restores it after refresh. Metadata availability depends on TikTok and does not imply permission to reuse media.

After selecting a reference, enter a project name (1–80 trimmed characters) and select **Create project**. In the saved workspace, choose one user-owned MP4 or MOV clip and select **Save clip**. Up to 10 projects are allowed, each with one fixed reference and one clip; a second upload returns 409. The saved-project list offers open and delete actions. Deletion requires confirmation naming the project and explaining removal of its uploaded clip. The limit is **100 MiB per file**, **101 MiB for the complete multipart request**, **120 seconds**, and **4096 pixels in either dimension**; audio is optional. The server allows one inspection at a time per process, caps the complete request at 30 seconds and FFprobe at 15 seconds, and rejects extra multipart fields or files. It verifies the MIME type, extension, container and video stream. Generic `application/octet-stream` MIME is accepted only when inspection confirms MP4 or MOV. The response reports container metadata, not full decoding or browser playback compatibility.

## Local storage and recovery

Run one backend process bound to `127.0.0.1`. This is a single-user development app with no authentication, suitable only for the local machine. Do not expose it to the network or run multiple workers.

- `data/reframe.sqlite3`: project names, canonical reference URL/string ID, reference snapshot and UTC inspection timestamp, clip metadata, SHA-256 and lifecycle state. Python's [sqlite3 module](https://docs.python.org/3.12/library/sqlite3.html) uses separate worker-owned connections, parameterized SQL, explicit transactions, foreign keys and schema version 1.
- `data/project-staging/`: generated names for unfinished project uploads and scoped multipart spools.
- `data/projects/<UUID>/`: generated media names for retained validated clips. Original filenames are display data. API responses never include filesystem paths.
- `data/clip-inspection/`: the existing temporary-only API remains separate and deletes its uploads after inspection. It is no longer the main UI flow.

Runtime data is ignored by Git and never served from `frontend/public`. Projects survive refresh and backend restart. Reopening reads the saved snapshot without contacting TikTok. It does not refresh reference metadata automatically.

Upload lifecycle: record **staging**, stream/validate and compute SHA-256, close multipart spools, record **validated** metadata and generated destination, move the file on the same filesystem, then commit **ready**. Success requires both the retained file and ready metadata. SQLite transactions do not make filesystem moves atomic. Move/database failures attempt compensation and return safe errors; cleanup failures remain visible. Cancellation joins owned worker operations before releasing locks or deleting staging. If cancellation arrives after a completed commit, the clip may already be ready: reopen the project to check before retrying.

Startup deletes only owned abandoned staging files and clears staging records. It checks retained size and SHA-256: an interrupted validated commit with matching retained bytes becomes ready, while missing/corrupt media or metadata becomes **unavailable**, with no ready clip returned. Reads also detect missing/changed-size files. SHA-256 is checked at startup, not on every read. Valid ready clips are never touched by temporary-inspection cleanup. Interrupted deletions remain **deleting** and can be retried; deletion succeeds only after project files and the database record are removed. A cleanup failure is reported/logged rather than claiming deletion. The schema version must be supported; an unknown version prevents startup. Back up the SQLite file and retained media together while the backend is stopped.

Project API: `POST /api/projects` with `{"name":"My project","reference_url":"<full TikTok URL>"}` re-inspects the reference server-side; `GET /api/projects` lists newest updated first; `GET /api/projects/{UUID}` reopens; `POST /api/projects/{UUID}/clip` accepts multipart `file`; `DELETE /api/projects/{UUID}` removes retained files and metadata. Upload/delete operations are serialized in this process. Saved clip metadata includes `storage_status: "retained"`, SHA-256 and a UTC save timestamp. No playback/media-serving endpoint is implemented.

The API endpoint is `POST /api/references/inspect` with JSON `{"url":"https://www.tiktok.com/@scout2015/video/6718335390845095173"}`. Success returns provider, string video ID, canonical URL, nullable title and author, `metadata_status: "available"`, and `analysis_status: "not_started"`. Errors use `{"error":{"code":"...","message":"..."}}`. The backend contacts only `https://www.tiktok.com/oembed`; it does not fetch or store reference media.

`POST /api/clips/inspect` accepts exactly one multipart `file` field. Success includes sanitized filename, size, duration, dimensions, codec names, audio presence, optional frame rate, `validation_status: "accepted"`, and `storage_status: "not_retained"`. It uses the same safe error envelope.

## Validate

```powershell
cd C:\Projects\Reframe\frontend
npm run lint
npm run typecheck
npm test
npm run build
cd ..\backend
uv run --locked pytest
uv run --locked ruff check .
uv run --locked ruff format --check .
cd ..\tools
uv run --locked python -m unittest discover -s tests -p 'test_tiktok_reference.py'
```

If the page says **API unavailable**, confirm the backend window is running and <http://127.0.0.1:8000/health> returns `{"status":"ok","service":"reframe-api"}`. Check that the frontend API URL points to that host and port and that the frontend origin is allowed by the backend. Then select **Retry**.

See [architecture](docs/ARCHITECTURE.md) and [status](docs/STATUS.md).
