# Reframe

Reframe is an early foundation for turning a reference edit into an editable style and, eventually, applying it to your own footage. Milestone 1 contains a frontend shell and a live API connectivity check only.

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
```

If the page says **API unavailable**, confirm the backend window is running and <http://127.0.0.1:8000/health> returns `{"status":"ok","service":"reframe-api"}`. Check that the frontend API URL points to that host and port and that the frontend origin is allowed by the backend. Then select **Retry**.

See [architecture](docs/ARCHITECTURE.md) and [status](docs/STATUS.md).
