"use client";

import { useEffect, useRef, useState } from "react";

type Operation = {
  operation_id: string | null; status: "idle" | "running" | "ready" | "failed";
  message: string | null; failure_code: string | null;
  media: null | { size_bytes: number; sha256: string; duration_seconds: number; width: number;
    height: number; video_codec: string; has_audio: boolean; audio_codec: string | null;
    retrieved_at: string; versions: Record<string, string> };
};
const apiBase = (process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

export function ReferenceMedia({ projectId }: { projectId: string }) {
  const [operation, setOperation] = useState<Operation | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [starting, setStarting] = useState(false);
  const action = useRef<AbortController | null>(null);
  const generation = useRef(0);

  async function request(method: string, controller: AbortController): Promise<Operation> {
    const timer = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch(`${apiBase}/api/projects/${encodeURIComponent(projectId)}/reference-media`, { method, signal: controller.signal });
      const data = await response.json();
      if (!response.ok) throw new Error(data?.error?.message || "Reference request failed.");
      if (!data || !["idle", "running", "ready", "failed"].includes(data.status) ||
          (data.status === "ready" && (!data.media || typeof data.media.duration_seconds !== "number" || typeof data.media.sha256 !== "string"))) throw new Error("Invalid reference media response.");
      return data;
    } finally { clearTimeout(timer); }
  }

  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController;
    const deadline = Date.now() + 180000;
    async function poll() {
      controller = new AbortController();
      try {
        const result = await request("GET", controller);
        if (!live) return;
        setOperation(result); setError("");
        if (result.status === "running") {
          if (Date.now() < deadline) timer = setTimeout(poll, 2000);
          else setError("Status polling stopped. Reopen this project to check; server processing continues.");
        }
      } catch (cause) {
        if (live) setError(cause instanceof Error && cause.name !== "AbortError" ? cause.message : "Status request timed out. Reopen the project to check.");
      }
    }
    void poll();
    return () => { live = false; clearTimeout(timer); controller?.abort(); };
    // Each request is scoped to this project; revision restarts bounded status polling.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, revision]);

  useEffect(() => {
    const version = generation;
    return () => { version.current++; action.current?.abort(); };
  }, [projectId]);

  async function start() {
    if (action.current) return;
    const controller = new AbortController();
    action.current = controller;
    const version = generation.current;
    setStarting(true); setError("");
    try {
      const result = await request("POST", controller);
      if (version === generation.current) { setOperation(result); setRevision(value => value + 1); }
    } catch (cause) {
      if (version === generation.current) setError(cause instanceof Error && cause.name !== "AbortError" ? cause.message : "Start response timed out. Reopen to check before retrying.");
    } finally {
      if (action.current === controller) action.current = null;
      if (version === generation.current) setStarting(false);
    }
  }

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
      <p role="status">Reference media is available. Style analysis is not implemented yet.</p>
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
    </>}
  </section>;
}
