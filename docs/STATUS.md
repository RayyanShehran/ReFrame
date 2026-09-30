# Implementation status

## Milestone 1: development foundation

The responsive Next.js shell and FastAPI health endpoint remain in place. Frontend and backend foundation checks run in CI.

## Milestone 2: TikTok reference-access feasibility

Reframe's intended inputs are a TikTok link for the reference and, separately, uploaded user clips. A corrected developer-only probe under `tools/` retrieved one public TikTok documentation sample on 2026-09-29 and verified 12,288 decoded RGB frame bytes plus 16,000 decoded audio samples. The earlier probe observation remains documented, including its exit-code-only decode limitation. Another canonical sample failed at metadata extraction, and tested short-link fixtures resolved to TikTok's home page. See [the evidence and limits](TIKTOK_REFERENCE_FEASIBILITY.md).

The decision is **proceed experimentally**: local technical feasibility is demonstrated for one link. Production-host reliability, rights and platform permission, and a working fresh short link remain unresolved. The architect will choose any production ingestion approach after reviewing this evidence.

## Milestone 3: reference selection

The app accepts full TikTok video URLs, retrieves public title and creator metadata through TikTok oEmbed, and lets the user select or clear a reference. Short links receive guidance to paste the full URL. Selection is in memory and clears on refresh. This milestone does not retrieve, analyze, or reuse media; a metadata result does not establish access or rights.

## Milestone 4: temporary clip inspection

After reference selection, one user-owned MP4 or MOV clip can be uploaded for technical inspection. Limits are 100 MiB for the file, 101 MiB for the multipart request, 120 seconds, and 4096 pixels per dimension; audio is optional. The endpoint accepts only one file field, permits one inspection at a time per process, and has 30-second request and 15-second FFprobe limits. It returns size, duration, dimensions, codec and audio metadata, then deletes the staged clip. Selection and clip details are in memory; refresh clears them and the clip must be uploaded again when needed. FFprobe is required for clip inspection.

On 2026-09-30, a locally generated 74,889-byte MP4 was inspected through the browser with FFprobe 9.0.2. The app reported 2 seconds, 320 × 240 pixels, MPEG-4 video, AAC audio and 24 fps. The dedicated inspection directory was empty after the response. Generated MP4 and MOV integration fixtures and a corrupt file were also checked with the real tools.

## Limitations

The app has no persistent project storage, reference analysis, Style Blueprint schema, worker, edit planning, or rendering. The feasibility probe is not an API or production importer. Metadata inspection does not prove full decoding or playback compatibility. Reference media is not downloaded. Health and reference metadata do not require FFprobe.
