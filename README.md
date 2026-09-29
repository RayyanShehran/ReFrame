# Reframe

Reframe takes a TikTok link as the reference edit and, separately, will later take user-uploaded clips as footage to edit. The app can inspect public TikTok reference metadata and select one reference in memory. It does not download or analyze video. The developer-only media feasibility spike is documented in [TikTok reference feasibility](docs/TIKTOK_REFERENCE_FEASIBILITY.md).

## Prerequisites

- Node.js 22 LTS and npm 10 (developed with Node 22.16.0, npm 10.9.2)
- Python 3.12 and uv 0.12.20 (developed with Python 3.12.14)
- PowerShell

Dependency versions are pinned in `frontend/package-lock.json` and `backend/uv.lock`. No Docker, FFmpeg, database, cloud credential, or AI key is required.

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

Paste a full HTTPS TikTok video URL such as `https://www.tiktok.com/@scout2015/video/6718335390845095173`, select **Check reference**, then **Use this reference**. The app shows public title and creator metadata from TikTok's [oEmbed API](https://developers.tiktok.com/docs/en/embed-videos). Bare `tiktok.com` and `m.tiktok.com` video links are normalized to `www.tiktok.com`; tracking parameters are removed. Short links are not supported. Selection is held only in browser memory: refreshing the page clears it. Metadata availability depends on TikTok and does not imply permission to reuse media.

The API endpoint is `POST /api/references/inspect` with JSON `{"url":"https://www.tiktok.com/@scout2015/video/6718335390845095173"}`. Success returns provider, string video ID, canonical URL, nullable title and author, `metadata_status: "available"`, and `analysis_status: "not_started"`. Errors use `{"error":{"code":"...","message":"..."}}`. The backend contacts only `https://www.tiktok.com/oembed`; it does not fetch or store reference media.

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
