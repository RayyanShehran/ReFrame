# Architecture

## Implemented app flow

The Next.js frontend checks FastAPI `/health` and lets a user inspect, select, change, or clear one TikTok reference. Selection lives in browser component state and disappears on refresh. The backend validates full HTTPS TikTok video links, normalizes them, and calls only the official fixed oEmbed endpoint for public title and creator metadata. It returns a typed reference summary or a safe error envelope. The API uses a 10-second deadline, bounded per-operation timeouts, no redirects or retries, and a 256 KiB streamed response limit. It has no media processing or storage behavior.

## Planned processing flow

TikTok reference link → reference media access → reference analysis → Style Blueprint → user settings → Edit Plan → rendering. User-uploaded clips are a separate input to edit planning and rendering. Reference uploads are not a fallback.

Reference analysis will produce **observations** about source media. The Style Blueprint will hold those observations as structured, editable data. User settings will express **overrides** and selections. An Edit Plan will turn the selected style and user settings into **executable operations**. Rendering will execute that plan against the user's media. Keeping these boundaries separate prevents analysis data from becoming rendering instructions by accident.

A developer-only feasibility probe under `tools/` tests whether public TikTok links yield decodable media. It is separate from the metadata API. Future uploaded user clips should live under ignored runtime storage, outside `frontend/public`. A future worker boundary should handle long-running media work independently of API request handling. Neither storage nor workers are implemented in this milestone.
