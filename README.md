# ReFrame

ReFrame takes a TikTok link as the reference edit and a separately uploaded user-owned clip as footage. Saved projects retain one validated user clip, a fixed reference metadata snapshot and, on request, experimental reference media. Offline color comparisons, estimated pacing, editable color recipes, cut plans and saved audio choices and reviewed captions drive local video rendering, playback and download. See [TikTok reference feasibility](docs/TIKTOK_REFERENCE_FEASIBILITY.md) for live evidence and limitations.

## Prerequisites

- Node.js 22 LTS and npm 10 (developed with Node 22.16.0, npm 10.9.2)
- Python 3.12 and uv 0.12.20 (developed with Python 3.12.14)
- FFprobe on `PATH` for clip inspection; FFmpeg and FFprobe for reference retrieval and generated-media tests. Install the [FFmpeg Windows build linked by FFmpeg.org](https://ffmpeg.org/download.html#build-windows) and add its `bin` directory to `PATH`, or use `winget install "FFmpeg (Essentials Build)"` where WinGet is available. On Ubuntu: `sudo apt-get update && sudo apt-get install -y ffmpeg`.
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

## Guided editing workspace

Open a saved project and use **Reference → Footage → Style & cuts → Audio & captions → Export**. The next-step button opens the relevant section without starting work. Section switches keep unsaved drafts and active-job status; refresh restores saved settings only. Cuts, captions and automatic proposals are optional for a whole-clip export.

Export summarizes saved revisions, recipe strength, audio, captions and framing. Blocked renders explain what to save or repair and link to that section. Detailed measurements are expandable. An outdated output stays playable/downloadable until a replacement succeeds; navigation does not save, regenerate or replace anything.

## Local storage and recovery

Run one backend process bound to `127.0.0.1`. This is a single-user development app with no authentication, suitable only for the local machine. Do not expose it to the network or run multiple workers.

- `data/reframe.sqlite3`: project snapshots, clips, reference media, independent analyses, recipes, plans, audio settings, captions and render records. Python's [sqlite3 module](https://docs.python.org/3.12/library/sqlite3.html) uses separate worker-owned connections, parameterized SQL, explicit transactions, foreign keys and schema version 11; startup migrates earlier supported versions while preserving saved projects and results.
- `data/project-staging/`: generated names for unfinished project uploads and scoped multipart spools.
- `data/projects/<UUID>/`: separate generated names for retained user clips and reference media. Original clip filenames are display data. API responses never include filesystem paths.
- `data/reference-staging/<operation UUID>/`: downloader fragments, media and bounded tool output for the current reference operation.
- `data/color-staging/<operation UUID>/`: bounded offline color-processing captures, removed before a blueprint becomes ready.
- `data/pacing-staging/<operation UUID>/`: bounded offline consecutive-frame detector captures, removed before pacing becomes ready.
- `data/clip-inspection/`: the existing temporary-only API remains separate and deletes its uploads after inspection. It is no longer the main UI flow.

Runtime data is ignored by Git and never served from `frontend/public`. Projects survive refresh and backend restart. Reopening reads the saved snapshot without contacting TikTok. It does not refresh reference metadata automatically.

Upload lifecycle: record **staging**, stream/validate and compute SHA-256, close multipart spools, record **validated** metadata and generated destination, move the file on the same filesystem, then commit **ready**. Success requires both the retained file and ready metadata. SQLite transactions do not make filesystem moves atomic. Move/database failures attempt compensation and return safe errors; cleanup failures remain visible. Cancellation joins owned worker operations before releasing locks or deleting staging. If cancellation arrives after a completed commit, the clip may already be ready: reopen the project to check before retrying.

Startup deletes only owned abandoned staging files and clears staging records. It checks retained size and SHA-256: an interrupted validated commit with matching retained bytes becomes ready, while missing/corrupt media or metadata becomes **unavailable**, with no ready clip returned. Reads also detect missing/changed-size files. SHA-256 is checked at startup, not on every read. Valid ready clips are never touched by temporary-inspection cleanup. Interrupted deletions remain **deleting** and can be retried; deletion succeeds only after project files and the database record are removed. A cleanup failure is reported/logged rather than claiming deletion. The schema version must be supported; an unknown version prevents startup. Back up the SQLite file and retained media together while the backend is stopped.

Project API: `POST /api/projects` with `{"name":"My project","reference_url":"<full TikTok URL>"}` re-inspects the reference server-side; `GET /api/projects` lists newest updated first; `GET /api/projects/{UUID}` reopens; `POST /api/projects/{UUID}/clip` accepts multipart `file`; `DELETE /api/projects/{UUID}` removes retained files and metadata. Upload/delete operations are serialized in this process. Saved clip metadata includes `storage_status: "retained"`, SHA-256 and a UTC save timestamp. Completed renders have separate playback/download routes described below.

The metadata endpoint is `POST /api/references/inspect` with JSON `{"url":"https://www.tiktok.com/@scout2015/video/6718335390845095173"}`. Success returns provider, string video ID, canonical URL, nullable title and author, `metadata_status: "available"`, and `analysis_status: "not_started"`. Errors use `{"error":{"code":"...","message":"..."}}`. Metadata inspection contacts only TikTok oEmbed; it does not retrieve media.

## Experimental reference media

In a saved project, select **Retrieve reference**. `POST /api/projects/{UUID}/reference-media` uses only that project's saved canonical URL and returns 202 with an operation ID/status. `GET` at the same route restores persisted idle/running/ready/failed status. One lifecycle-owned retrieval runs globally; duplicate starts reuse the same operation, other projects receive 503 busy, ready media is reused, and failed work needs an explicit retry with a new operation ID. No automatic download retry loop or queue is added; the reviewed downloader's bounded internal retries remain.

The pinned yt-dlp 2026.8.19, FFmpeg and FFprobe run through the same internal containment engine as the temporary-only developer CLI. Retrieval checks numeric identity before download, one nonempty file at most 50 MiB, a genuine video stream, finite positive duration at most 120 seconds, dimensions at most 4096 pixels, exactly 12,288 decoded RGB frame bytes and nonempty PCM samples when audio exists. Audio may be absent. Generated reference containers/codecs are inspected directly, independent of user-upload extension/MIME rules. Safe metadata records size, SHA-256, codecs, audio presence, dimensions, duration, UTC retrieval time, decoded counts and tool versions; raw extractor JSON, signed download URLs and logs are not retained in records.

The 150-second monotonic deadline caps stage limits. A 100 ms watchdog stops owned processing above 60 MiB aggregate staging; this is polling, not a filesystem quota, so writes/termination may overshoot. Tool stdout readback is bounded to 1 MiB, stderr to 64 KiB. Windows uses a private Job Object assigned before resuming the suspended process; POSIX retains its process group identity through parent exit. All owned descendants must stop before staging removal. Cleanup joins may extend beyond the processing deadline. Deliberately escaping descendants and forced termination of the backend itself are outside the graceful-shutdown guarantee.

Validated media is hashed, moved to its unique project destination, staging is removed, and SQLite marks it ready only if the project is active and the operation still matches. Move/commit failures compensate by removing that operation's file; failures remain explicit. Restart marks unfinished work interrupted without re-downloading, reconciles retained size/digest and cleans generated abandoned staging. Valid clips and references are preserved. If owned process termination is unconfirmed, staging is quarantined with `cleanup_failure`; retry/deletion stay blocked pending manual review, including after restart. Deletion and graceful shutdown signal and join active retrieval before removing files. Network processing does not hold the project operation lock.

Browser status requests have 10-second limits and poll every 2 seconds for at most 3 minutes. Navigation stops polling and rejects stale responses while server work continues; reopen to restore status. Reference media is distinct from source footage. Retrieval reliability, content rights and production isolation remain unresolved. Color-only playback and download are available through saved recipes. Reference uploads and public deployment are not implemented.

## Read-only reference color blueprint

Once reference media is ready, select **Analyze reference**. `POST /api/projects/{UUID}/style-blueprint` starts offline analysis and returns a typed operation with HTTP 202; `GET` at the same route restores idle/running/ready/failed status and the complete ready blueprint. No new TikTok request occurs. Retrieval and analysis share one global expensive worker; duplicate starts reuse the operation, failed attempts need explicit retry, and deletion/shutdown stop and join owned work. Restart marks unfinished analysis interrupted without restarting it. Unconfirmed process termination preserves quarantined staging and blocks new media jobs and deletion pending manual review.

The [version 1 JSON schema](docs/STYLE_BLUEPRINT.schema.json) records reference identity/SHA-256, algorithm `encoded-rgb-midpoints-v1`, tool versions, sampling, color metadata/assumptions, measurements and interpretation limits. Pacing, transitions, captions and audio explicitly remain `not_analyzed`. Ready results are reused only when the retained media hash and algorithm match; status/start/recovery verify the source and invalidate missing or changed media. Ordinary project reads do not hash it. Results become visible in one SQLite transaction after confirmed processing and staging cleanup; stale/deleted/canceled operations cannot publish.

Analysis samples 12 midpoint targets `(i + 0.5) * duration / 12` from the first genuine video stream, excluding attached artwork. FFmpeg normalizes timestamps, shifts by half an interval and applies nearest-rounding `fps=12/duration`, then area-resizes to 96 × 96 RGB24 without padding or autorotation. Short clips can repeat frames. Exactly **331,776 decoded bytes** are required. Every resized pixel and time sample has equal weight. RGB means are normalized to [0,1]; encoded brightness is `(0.2126R + 0.7152G + 0.0722B)/255`, with linearly interpolated 5th/50th/95th percentiles at sorted index `(N-1)*p`. Contrast is 95th minus 5th. Mean HSV saturation uses `(max-min)/max`, with black zero. Palette bins have RGB channel width 32; the five largest bins sort by count then lexicographic bin, with arithmetic mean RGB rounded half up. Proportions use **all sampled pixels**; displayed coverage can be below 100%.

This version accepts tagged BT.709/sRGB ordinary SDR only (primaries BT.709; transfer BT.709, sRGB or gamma 2.2; matrix BT.709 or RGB; limited/full range). Tagged HDR/wide gamut and other color spaces are rejected without tone mapping. Incomplete tags produce a visible ordinary-SDR assumption warning, with missing YCbCr matrix/range assumed BT.709/limited. Untagged HDR cannot reliably be identified. Brightness is an encoded-pixel observation, never physical exposure; no camera settings, LUT, color temperature or creative intent is inferred.

Bounds: retained source ≤50 MiB, duration ≤120 seconds, dimensions ≤4096; overall processing deadline 120 seconds, version commands 5 seconds each, probe 10 seconds, decode 60 seconds, all capped by remaining time. Ready-source hash verification has a 10-second limit. A 100 ms watchdog observes a 2 MiB staging budget with possible overshoot, not a strict filesystem quota. Stdout caps are 8 KiB for versions, 64 KiB for metadata and 331,776 bytes for RGB; stderr readback is 64 KiB. The existing Windows Job Object/POSIX process-group ownership machinery is reused. Termination/cleanup can extend beyond the processing deadline; forced backend termination or intentionally escaping POSIX groups remains outside the guarantee.

Twelve samples can miss brief events; resizing, variable frame timing and tool versions affect results. Blueprint editing, semantic footage matching and image mode remain future milestones; saved cut planning and rendering are described below. See [status](docs/STATUS.md) for generated-media and browser evidence.

## Estimated cuts and pacing

Select **Analyze pacing** when retained reference media is ready. Typed POST/GET `/api/projects/{UUID}/pacing` expose an independent operation and [pacing schema version 1](docs/PACING_BLUEPRINT.schema.json), algorithm `scdet-consecutive-v1`. Existing color JSON stays unchanged; its historical `pacing: not_analyzed` means that the color component does not analyze pacing. Pacing is added only by explicit action, and its failure/retry never clears a valid color result. Both components share the existing global worker, hash checks, atomic publication, bounded polling, stale-result rejection, graceful join and quarantine rules.

FFmpeg [scdet](https://ffmpeg.org/ffmpeg-filters.html#scdet) uses explicit threshold **10** on **every consecutive decoded video frame**, with normalized PTS and no frame subsampling. Area resizing keeps the whole image, preserving aspect ratio within even-pixel rounding, maximum dimension 320 (minimum 2); YUV420P conversion uses the same SDR metadata checks/assumptions as color. No cropping, autorotation or tone mapping occurs. The result records actual processing dimensions, threshold, frame count, source hash and FFmpeg/FFprobe versions. Complete per-frame score/MAFD metadata and successful decoding are required even for zero cuts; missing, malformed, nonfinite or capped output fails explicitly. Candidates are sorted, deduplicated and strictly within the duration. Contiguous shots run from zero through candidates to the video end; mean is the arithmetic mean, median averages the central two for even counts, and cuts/minute is `60 * cut_count / duration`. Zero cuts means one estimated shot.

Pacing bounds: overall 120 seconds; versions 5 seconds each, probe 10 and detector 90, capped by remaining time; stdout 8 KiB/version, 64 KiB/probe and 1 MiB/detector; stderr readback 64 KiB; 2 MiB staging watchdog polled every 100 ms (possible overshoot, not a quota); at most 1,000 candidates, including duplicates/endpoints before filtering. Exceeding any bound fails the entire operation rather than returning partial findings. High-frame-rate clips can hit the output cap. The UI shows estimated counts/statistics and an expandable shot-duration table, restored after refresh. Flashes/motion can cause false positives and similar-looking shots can hide cuts. **This is not transition-type recognition**; transitions, captions and audio remain `not_analyzed`.

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

## Uploaded-footage color and comparison

After saving one clip, select **Analyze my footage**. This offline analysis works independently of reference retrieval and restores after refresh. It reuses the reference's 12 midpoint SDR pixel measurements and warnings; footage retains its **100 MiB** limit and references retain **50 MiB**.

When both color results are valid, the read-only **Reference / Your footage** table shows palettes, median encoded brightness, contrast spread, mean HSV saturation and mean RGB. Signed differences are **reference minus footage**, in percentage points. These describe sampled pixels and can reflect different scene content; they are not exposure stops, white-balance corrections or a ready-to-apply grade. Missing/failed results show the required action instead. Saved creative recipes can drive color-only rendering; sampled differences are not automatic grades.

## Editable saved color recipe

With both color analyses ready, **Generate suggestion** creates a separate experimental recipe at 50% strength. Adjust brightness offset (−0.20…+0.20), contrast/saturation multipliers (0.5…1.5) and strength (0…100%), then **Save recipe**. Saved settings restore after refresh. Resets affect the unsaved draft: suggestion restores its suggested values/50% strength; neutral sets brightness 0, both multipliers 1 and strength 0. Regeneration explicitly replaces saved edits. Conflicting revisions require reloading.

Suggestions use measured median-brightness difference clamped to ±0.10 and contrast/saturation ratios clamped to 0.8…1.2; footage denominators below 0.02 produce a neutral multiplier with an explanation. Strength interpolates brightness from 0 and multipliers from 1. Measured blueprints remain unchanged. Stale recipes preserve settings for inspection and require valid analyses plus explicit regeneration. These are heuristic creative settings, not exposure stops, recovered LUTs, inferred white balance or a guarantee of matching appearance. Controls do not produce a live preview. Save the recipe, then render the actual video.


## Render, playback and download

Save a valid recipe, choose **Whole clip (color + saved audio)**, then select **Render video**. Unsaved recipe/audio/caption edits must be saved first. The app renders the whole uploaded clip using those saved revisions, restores status/output after refresh, and provides native playback/seeking and **Download MP4**. A prior output remains available until its replacement succeeds; older revisions or changed sources are labeled **outdated**. Retry is explicit. This mode does not apply cuts; both modes support saved captions, without transitions.

## Reference-paced saved cuts

With valid reference pacing, retained footage and a saved color recipe, select **Generate cut plan**. The default output duration is the shorter source; a custom duration cannot exceed either. Estimated reference shot lengths become fixed target segment lengths on a 30 fps grid, with a trimmed final shot. Sub-frame intervals merge explicitly; more than 60 segments fails rather than truncating the plan. Initial footage ranges are chronological with unused time distributed between segments; a single range is centered.

Adjust only **Source start**, review the computed end and fixed output length, then **Save cut plan**. Starts snap to the nearest frame and ranges must remain ordered, nonoverlapping and inside the footage. Reset restores the suggested draft; explicit regeneration replaces the saved plan. Conflicts retain unsaved edits. Plans and outputs restore after refresh. Source/pacing changes mark plans stale; recipe edits leave cut timing valid. Select **Saved cut plan (cuts + color)** to render both saved revisions. Original footage and measured blueprints remain unchanged.

This is a timing suggestion, not semantic footage matching. Continuous source ranges do not create visible jumps merely because segment boundaries exist. The renderer skips unselected footage, normalizes timestamps and applies color once. Original audio follows footage ranges; reference audio follows the continuous output timeline. Original mode leaves a silent source silent. See [method, frame rules and schema](docs/ARCHITECTURE.md#reference-paced-cut-planning-and-rendering).

Output is MP4/H.264 (CRF 20, veryfast), yuv420p, faststart and 30 fps, with first-stream AAC at 48 kHz/128 kbit/s when audio exists. Display rotation is applied, pixel aspect ratio normalized, and even dimensions fit landscape 1280 × 720 or portrait 720 × 1280 without enlarging the displayed image. Color processing uses the ordinary-SDR policy and effective brightness/contrast/saturation exactly once; settings are heuristic, not recovered LUTs or a match guarantee. See [renderer method and bounds](docs/ARCHITECTURE.md#color-only-rendering).

`GET/POST /api/projects/<id>/render` reads/starts work; POST accepts `{"expected_revision": <recipe revision>, "expected_audio_revision": <audio revision>, "expected_caption_revision": <caption revision>}` for a whole clip, with `"expected_plan_revision": <saved plan revision>` added for cuts. Older requests work only while audio and captions remain unchanged revision-zero defaults. Inline and attachment routes are `/api/projects/<id>/outputs/<output-id>/video` and `/download`, including framework byte-range support. IDs resolve only server-generated paths. The existing shared worker applies a 300-second deadline, 100 MiB output limit, 120 MiB polled staging budget and bounded logs. Full-file decoded frame/audio hashes, streams, dimensions, duration, size and SHA-256 are verified before publication.

## Saved audio choices

In **Audio**, choose Original footage audio (default), Reference audio, Mix or Mute; adjust relevant volumes (0–100%) and reference offset, then **Save audio** before rendering either mode. Single-source choices start at 100%; initially choosing Mix suggests 70% original / 30% reference. Choices and completed output audio revision restore after refresh. Edits make the prior video outdated without removing it. Stale source bindings require an explicit save with current sources; conflicts preserve the draft until reload.

Reference audio requires ready retained reference media with audio; Mix requires both streams. Original mode accepts silent footage, and Mute omits audio entirely. Reference audio can contain speech and sound effects. Retrieval does not grant permission to reuse content.

Reference offsets are seconds inside the normalized reference video timeline. Its track plays continuously from output time zero, even across footage cuts, retains genuine delayed starts, trims to output length and pads shorter tracks with silence. It is never automatically looped or stretched. Mix uses explicit gains, 48 kHz stereo, no automatic gain normalization and a latency-compensated limiter with 0.95 peak limit; limiting can reduce loud peaks. No music identification, source separation, beat analysis or audio uploads. See [audio schema/method/API](docs/ARCHITECTURE.md#saved-audio-settings-and-rendering).

## Editable captions and captioned export

In **Captions**, enable the track, add/edit/delete timed plain-text cues or import a UTF-8 SRT. Import shows a preview; **Replace draft with imported cues** changes the draft only. Choose white/yellow, small/medium/large and bottom-center/center with a dark outline, then **Save captions** before rendering. Text/timing/style, provenance and revision restore after refresh. Captions burn into the downloaded MP4 after color processing; they are not a live preview or automatically extracted/transcribed text.

Times are on the final output timeline, **start inclusive / end exclusive**, not footage source time. Select Whole clip or Saved cut plan to match the render mode. Changing the bound footage or cut-plan revision makes the saved track stale; switching/rebinding requires explicit confirmation, preserving text/times and revalidating every cue without shifts/truncation. Color/audio edits do not invalidate the track. Conflicts retain the draft until explicit reload. Caption edits outdate the previous export without removing it.

Limits: 200 ordered nonoverlapping cues, 200 Unicode characters and two explicit lines per cue, within output duration; SRT is at most 128 KiB UTF-8 with standard comma-millisecond timestamps. Markup-looking text is displayed literally. Bundled licensed DejaVu Sans supports English and Arabic; unsupported emoji/CJK glyphs can show missing symbols or vary with system fallback. Captions require FFmpeg's libass `ass` filter; disabled captions work without it. Very short cues can fall between 30 fps frames. See [caption schema, encoding and timing](docs/ARCHITECTURE.md#saved-captions-and-burned-in-export).

## Local automatic captions (optional)

After rendering a video with audio, select **Generate caption proposal**, choose Auto-detect, English or Arabic, and review/edit every proposed cue. **Use these captions** copies an unsaved draft into the existing editor, preserving style; replacement requires confirmation. Save captions, then render again. Refresh restores the proposal, saved captions and export. Mixed speech/music may reduce accuracy; speech detection does not prevent hallucinations. No translation or visible-reference-text extraction is performed.

One-time dependency/model setup (network required only here), from `backend`:

```powershell
uv sync --locked --extra transcription
uv run --locked --extra transcription python transcribe_local.py setup
```

The multilingual base model is pinned to `Systran/faster-whisper-base` revision `ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66` in ignored `data/transcription-models/`. Ordinary jobs load local files only. Start the API with the optional extra to keep these dependencies installed:

```powershell
uv run --locked --extra transcription uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

Separate real-inference smoke check, with an audio-bearing local video of at most 120 seconds:

```powershell
uv run --locked --extra transcription python transcribe_local.py smoke C:\path\speech.mp4 en
```

This prints actual cues, versions and elapsed time; it does not download a model or save a project. Ordinary CI excludes the optional extra, model downloads and real inference. CPU/int8 uses two inference threads, a 300-second deadline, bounded 16 kHz mono PCM/JSON, and the existing shared media worker/containment. If saved automatic captions are stale, disable and save them before rendering current audio/cuts; their text and original timing remain available for inspection. Footage, cut-plan or saved audio changes require rendering current settings and explicitly regenerating the proposal; color/style changes and a new MP4 hash alone do not. [Method and proposal schema](docs/ARCHITECTURE.md#local-automatic-caption-proposals) document limits and provenance.


## Saved output framing

Choose **Original**, **Portrait (720 × 1280)**, **Square (720 × 720)** or **Landscape (1280 × 720)**. Original is the existing-project default and keeps the previous size limit without upscaling. Fixed **Fit** preserves the image with black padding; **Fill** crops the edges proportionally, with left/right and top/bottom positioning where movement is available. Fixed formats may upscale. Reset changes only the draft to Original; **Save framing** persists it. Save before rendering. The saved format, dimensions and framing revision restore after refresh, together with playback/download of the last completed MP4.

Framing applies to whole clips and saved cuts. Captions are drawn on the final canvas; audio and timing are unchanged. Saving new framing marks the previous video outdated but keeps it available until replacement succeeds. No live crop preview or subject tracking. [Method and schema](docs/ARCHITECTURE.md#saved-output-framing) describe geometry, bounds and compatibility.

## Before / after frame previews

In **Style & cuts**, choose a footage time in seconds and select **Update preview**. Original shows the entire normalized source frame; Edited applies the saved color recipe and framing. Both use the same source moment and show its timestamp and saved revisions. **Review before/after frame** in Export opens this panel. Save relevant draft changes first to include them; no decoding happens on page load or slider changes.

Previous images remain visible while updating or after failure, and become outdated when saved settings change. Switching sections retains them; refresh clears this transient preview. Images stack on narrow screens and have a maximum edge of 960 pixels. Review motion, cuts, audio and captions through the actual video render, not these still images. Processing shares the media slot, has a 30-second deadline and explicit size/failure limits. [Method and response](docs/ARCHITECTURE.md#beforeafter-frame-preview-milestone-18) describe source validation, EOF selection and cleanup.
