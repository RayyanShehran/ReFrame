# Implementation status

## Milestone 1: development foundation

The responsive Next.js shell and FastAPI health endpoint remain in place. Frontend and backend foundation checks run in CI.

## Milestone 2: TikTok reference-access feasibility

Reframe's intended inputs are a TikTok link for the reference and, separately, uploaded user clips. Neither input is available in the app yet. A corrected developer-only probe under `tools/` retrieved one public TikTok documentation sample on 2026-09-29 and verified 12,288 decoded RGB frame bytes plus 16,000 decoded audio samples. The earlier probe observation remains documented, including its exit-code-only decode limitation. Another canonical sample failed at metadata extraction, and tested short-link fixtures resolved to TikTok's home page. See [the evidence and limits](TIKTOK_REFERENCE_FEASIBILITY.md).

The decision is **proceed experimentally**: local technical feasibility is demonstrated for one link. Production-host reliability, rights and platform permission, and a working fresh short link remain unresolved. The architect will choose any production ingestion approach after reviewing this evidence.

## Limitations

The app has no TikTok reference input, uploaded user-clip input, reference analysis, Style Blueprint schema, media storage, worker, edit planning, or rendering. The feasibility probe is not an API or production importer. Ordinary app startup and CI do not require FFmpeg.
