# Implementation status

## Milestone 1: development foundation

The responsive Next.js shell and FastAPI health endpoint remain in place. Frontend and backend foundation checks run in CI.

## Milestone 2: TikTok reference-access feasibility

ReFrame's intended inputs are a TikTok link for the reference and, separately, uploaded user clips. A corrected developer-only probe under `tools/` retrieved one public TikTok documentation sample on 2026-09-29 and verified 12,288 decoded RGB frame bytes plus 16,000 decoded audio samples. The earlier probe observation remains documented, including its exit-code-only decode limitation. Another canonical sample failed at metadata extraction, and tested short-link fixtures resolved to TikTok's home page. See [the evidence and limits](TIKTOK_REFERENCE_FEASIBILITY.md).

The decision is **proceed experimentally**: local technical feasibility is demonstrated for one link. Production-host reliability, rights and platform permission, and a working fresh short link remain unresolved. The architect will choose any production ingestion approach after reviewing this evidence.

## Milestone 3: reference selection

The app accepts full TikTok video URLs, retrieves public title and creator metadata through TikTok oEmbed, and lets the user select or clear a reference. Short links receive guidance to paste the full URL. Selection is in memory and clears on refresh. This milestone does not retrieve, analyze, or reuse media; a metadata result does not establish access or rights.

## Milestone 4: temporary clip inspection

After reference selection, one user-owned MP4 or MOV clip can be uploaded for technical inspection. Limits are 100 MiB for the file, 101 MiB for the multipart request, 120 seconds, and 4096 pixels per dimension; audio is optional. The endpoint accepts only one file field, permits one inspection at a time per process, and has 30-second request and 15-second FFprobe limits. It returns size, duration, dimensions, codec and audio metadata, then deletes the staged clip. Selection and clip details are in memory; refresh clears them and the clip must be uploaded again when needed. FFprobe is required for clip inspection.

On 2026-09-30, a locally generated 74,889-byte MP4 was inspected through the browser with FFprobe 9.0.2. The app reported 2 seconds, 320 × 240 pixels, MPEG-4 video, AAC audio and 24 fps. The dedicated inspection directory was empty after the response. Generated MP4 and MOV integration fixtures and a corrupt file were also checked with the real tools.

## Milestone 5: persistent local projects

Saved projects now retain one fixed TikTok reference snapshot and one validated user clip. Names are trimmed to 1–80 characters; up to 10 projects are allowed. UUID project URLs restore state after refresh. The backend stores metadata in `data/reframe.sqlite3`, stages uploads separately in `data/project-staging/`, and retains generated media names under `data/projects/<UUID>/`. Runtime data remains ignored and outside the public frontend directory. SHA-256 is computed while streaming. The existing limits and temporary-only inspection API remain unchanged.

The persisted staging/validated/ready lifecycle makes interrupted file/database commits detectable. Startup reconciles owned staging and retained media, verifies digests and marks missing/corrupt media unavailable. Matching retained bytes from an interrupted validated commit can recover to ready. Deletion persists a deleting state and stays retryable after cleanup failure. Conflicting upload/deletion is serialized in this single process. Server workers own separate SQLite connections; cancellation joins worker work before ownership is released. The frontend makes saved metadata snapshots and local retention explicit and confirms deletion by project name.

On 2026-09-30, a browser demonstration created a project, retained the generated 74,889-byte MP4, refreshed, restarted the backend as a new normal process, reopened the same UUID URL and recovered the snapshot and metadata: 2 seconds, 320 × 240, MPEG-4/AAC, 24 fps, SHA-256 `9e8d1ae7ad80d0c8a3217dfea71e022aa8a6a443e8c27ef104613e7fcaee0531`. The retained file was still present after restart. Confirmed deletion returned 204; SQLite then had zero demo project/clip records, its project directory was absent and staging was empty.

Live oEmbed reference inspection succeeded, but the server-side reinspection at project creation was rate-limited (503). The persistence demonstration therefore used an explicitly labeled local reference fixture for creation, with the real upload, FFprobe 9.0.2, SQLite/filesystem, refresh, restart and deletion flow. Reopen after restart used the normal backend and no fixture/upstream call. This does not claim successful live reference creation. Automated tests mock TikTok, use isolated databases/directories and include generated real-media persistence checks.

## Limitations

The app is single-user and loopback-only, with one backend process. It has no cloud storage, authentication, clip replacement, multiple clips, reference analysis, Style Blueprint schema, worker, edit planning, or rendering. Digest validation happens at startup; ordinary reads check file presence/size. There is no media playback/serving route. Cancellation after a completed ready commit may leave a saved clip; reopen to check before retrying. The feasibility probe is not an API or production importer. Metadata inspection does not prove full decoding or playback compatibility. Reference media is not downloaded. Health and reference metadata do not require FFprobe.
