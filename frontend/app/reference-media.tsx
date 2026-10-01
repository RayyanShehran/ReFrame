"use client";

import { useMediaOperation } from "./use-media-operation";
import { StyleBlueprint } from "./style-blueprint";
import { PacingBlueprint } from "./pacing-blueprint";

type Operation = {
  operation_id: string | null; status: "idle" | "running" | "ready" | "failed";
  message: string | null; failure_code: string | null;
  media: null | { size_bytes: number; sha256: string; duration_seconds: number; width: number;
    height: number; video_codec: string; has_audio: boolean; audio_codec: string | null;
    retrieved_at: string; versions: Record<string, string> };
};
function parse(data: unknown): Operation {
  if (!data || typeof data !== "object" || !("status" in data) || !["idle", "running", "ready", "failed"].includes(String(data.status))) throw new Error("Invalid reference media response.");
  const operation = data as Operation;
  if (operation.status === "ready" && (!operation.media || typeof operation.media.duration_seconds !== "number" || typeof operation.media.sha256 !== "string")) throw new Error("Invalid reference media response.");
  return operation;
}

export function ReferenceMedia({ projectId }: { projectId: string }) {
  const { operation, error, starting, start } = useMediaOperation(projectId, "reference-media", parse);
  const media = operation?.media;
  return <section className="reference-section" aria-labelledby={`media-${projectId}`}>
    <h3 id={`media-${projectId}`}>Reference media</h3>
    <p className="hint">Experimental: TikTok access may fail or change. Retrieval does not grant permission to reuse content.</p>
    {!operation && !error && <p role="status">Loading reference status…</p>}
    {operation?.status === "idle" && <p>Reference media has not been retrieved.</p>}
    {operation?.status === "running" && <p role="status">Retrieving and validating reference media… You can leave this project; processing continues.</p>}
    {operation?.status === "failed" && <p role="alert">{operation.message || "Reference retrieval failed."} ({operation.failure_code})</p>}
    {error && <p role="alert" className="error">{error}</p>}
    {operation && ["idle", "failed"].includes(operation.status) && <button disabled={starting} onClick={() => void start()}>{starting ? "Starting…" : operation.status === "failed" ? "Retry reference retrieval" : "Retrieve reference"}</button>}
    {operation?.status === "ready" && media && <>
      <p role="status">Reference media is available for color and pacing analysis.</p>
      <dl className="clip-details">
        <dt>Duration</dt><dd>{media.duration_seconds} seconds</dd>
        <dt>Dimensions</dt><dd>{media.width} × {media.height}</dd>
        <dt>Size</dt><dd>{media.size_bytes} bytes</dd>
        <dt>Video</dt><dd>{media.video_codec}</dd>
        <dt>Audio</dt><dd>{media.has_audio ? media.audio_codec : "Absent"}</dd>
        <dt>Retrieved</dt><dd>{media.retrieved_at}</dd>
        <dt>SHA-256</dt><dd style={{ overflowWrap: "anywhere" }}>{media.sha256}</dd>
        <dt>Tools</dt><dd>{Object.entries(media.versions).map(([tool, version]) => `${tool}: ${version}`).join(" · ")}</dd>
      </dl>
      <StyleBlueprint key={projectId} projectId={projectId} />
      <PacingBlueprint key={`pacing-${projectId}`} projectId={projectId} />
    </>}
  </section>;
}
