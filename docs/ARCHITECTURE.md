# Architecture

## Implemented app flow

The Next.js frontend checks FastAPI `/health` and lets a user inspect/select a full TikTok reference, create a named local project, upload one validated user clip, and reopen/delete saved projects. The project UUID in the query string restores server state after refresh; navigation aborts and invalidates pending client work. Reference metadata is a saved snapshot. The backend revalidates and inspects the reference at project creation through the existing official oEmbed service; reopen never contacts TikTok. The reference API keeps its 10-second deadline and 256 KiB streamed response limit.

The shared clip staging context bounds the raw multipart body before parsing (101 MiB), enforces the separate 100 MiB file limit, 120-second duration and 4096-pixel dimensions, and permits one active upload/inspection per process. It validates MP4/MOV extension, MIME/container and a genuine video stream. Audio is optional. The 30-second operation and 15-second FFprobe deadlines, bounded output, file-only protocol restriction, cancellation/reaping, spool closure and safe error envelope remain. Metadata is not proof of complete decoding or browser playback compatibility.

## Project persistence and consistency

Python 3.12 `sqlite3` stores metadata in ignored `data/reframe.sqlite3`, with schema version 1, foreign keys, UUID IDs, parameter binding and explicit transactions. Every database connection is created, used and closed on one worker thread. Filesystem/database work runs off the async event loop; cancellation waits for the owned worker before releasing ownership. This app supports one process on loopback, one user, at most 10 projects and one clip per project. A process lock serializes conflicting project upload/delete work; a separate shared inspection lock covers both upload endpoints.

Projects store a fixed canonical TikTok URL/string ID, metadata snapshot and UTC inspection timestamp. Clips store display metadata, generated media name, SHA-256, UTC save timestamp and state. Storage names and project IDs are validated before filesystem use; paths are never returned. Runtime data is outside `frontend/public`, with no media-serving route.

Project uploads use `data/project-staging/`. The lifecycle is staging row, validated metadata/digest row, same-filesystem move to `data/projects/<UUID>/`, then ready transaction. Multipart spools are closed before persistence. The file is flushed before validation. Filesystem moves and SQLite commits are separate operations, so success is returned only after both finish. Failures attempt removal and metadata rollback; unresolved cleanup stays visible. Cancellation after a completed ready commit can leave a valid retained clip, which is discoverable on reopen.

Startup removes generated abandoned staging files, clears staging records, and verifies retained size/digest and metadata. Matching retained bytes from an interrupted validated commit can become ready. Missing/corrupt records become unavailable and expose no clip as ready. Ordinary reads also check file existence/size; digest checking is a startup operation. Valid ready clips are preserved. Unknown schema versions prevent startup. Deletion first persists deleting, removes the project's directory, then deletes its metadata with foreign-key cascade. Failed/interrupted deletion remains visible and retryable; it is never reported successful with retained files remaining. All reconciliation is scoped to ReFrame-owned storage.

The old `POST /api/clips/inspect` remains temporary-only under `data/clip-inspection/`, deletes staged media before returning, and never touches retained projects. The main frontend path uses saved projects.

## Planned processing flow

TikTok reference link → reference media access → reference analysis → Style Blueprint → user settings → Edit Plan → rendering. User-uploaded clips are a separate input to edit planning and rendering. Reference uploads are not a fallback.

Reference analysis will produce **observations** about source media. The Style Blueprint will hold those observations as structured, editable data. User settings will express **overrides** and selections. An Edit Plan will turn the selected style and user settings into **executable operations**. Rendering will execute that plan against the user's media. Keeping these boundaries separate prevents analysis data from becoming rendering instructions by accident.

A developer-only feasibility probe under `tools/` tests whether public TikTok links yield decodable media. It is separate from the metadata API. Retained user clips live under ignored local project storage, outside `frontend/public`. A future worker boundary should handle long-running media work independently of API request handling. Local project storage is implemented; workers remain future work.
