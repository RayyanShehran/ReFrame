"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";

export type ClipDetails = {
  filename: string;
  size_bytes: number;
  duration_seconds: number;
  width: number;
  height: number;
  video_codec: string;
  has_audio: boolean;
  audio_codec: string | null;
  frame_rate: number | null;
  validation_status: "accepted";
  storage_status: "not_retained" | "retained";
};

const apiBase = (process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

async function inspectClip(file: File, signal: AbortSignal, projectId?: string): Promise<ClipDetails> {
  const body = new FormData();
  body.append("file", file);
  const response = await fetch(`${apiBase}${projectId ? `/api/projects/${projectId}/clip` : "/api/clips/inspect"}`, { method: "POST", body, signal });
  let data: unknown = await response.json();
  if (!response.ok) {
    if (typeof data === "object" && data !== null && "error" in data &&
        typeof data.error === "object" && data.error !== null && "message" in data.error &&
        typeof data.error.message === "string") throw new Error(data.error.message);
    throw new Error("Clip inspection failed. Please try again.");
  }
  if (projectId && typeof data === "object" && data !== null && "clip" in data) data = data.clip;
  return parseClipDetails(data, Boolean(projectId));
}

export function parseClipDetails(data: unknown, retained = false): ClipDetails {
  if (typeof data !== "object" || data === null || !("validation_status" in data) || data.validation_status !== "accepted" ||
      !("storage_status" in data) || data.storage_status !== (retained ? "retained" : "not_retained") ||
      !("filename" in data) || typeof data.filename !== "string" ||
      !("size_bytes" in data) || typeof data.size_bytes !== "number" ||
      !("duration_seconds" in data) || typeof data.duration_seconds !== "number" ||
      !("width" in data) || typeof data.width !== "number" ||
      !("height" in data) || typeof data.height !== "number" ||
      !("video_codec" in data) || typeof data.video_codec !== "string" ||
      !("has_audio" in data) || typeof data.has_audio !== "boolean" ||
      !("audio_codec" in data) || (data.audio_codec !== null && typeof data.audio_codec !== "string") ||
      !("frame_rate" in data) || (data.frame_rate !== null && typeof data.frame_rate !== "number")) {
    throw new Error("Clip inspection returned invalid details.");
  }
  return data as ClipDetails;
}

export function ClipUpload({ projectId, initialDetails = null, onSaved }: { projectId?: string; initialDetails?: ClipDetails | null; onSaved?: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [details, setDetails] = useState<ClipDetails | null>(initialDetails);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState("");
  const active = useRef<AbortController | null>(null);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const version = useRef(0);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => () => { version.current++; active.current?.abort(); if (timeoutRef.current) clearTimeout(timeoutRef.current); }, []);

  function invalidate() {
    version.current++;
    active.current?.abort();
    active.current = null;
    if (timeoutRef.current) clearTimeout(timeoutRef.current);
    timeoutRef.current = null;
    setDetails(null);
    setChecking(false);
    setError("");
  }

  async function check(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (active.current || !file) return;
    if (file.size > 100 * 1024 * 1024) { setError("The clip must be 100 MiB or smaller."); return; }
    const controller = new AbortController();
    active.current = controller;
    const current = ++version.current;
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; controller.abort(); }, 45000);
    timeoutRef.current = timer;
    setError("");
    setDetails(null);
    setChecking(true);
    try {
      const result = await inspectClip(file, controller.signal, projectId);
      if (version.current === current) { setDetails(result); onSaved?.(); }
    } catch (cause) {
      if (version.current === current) setError(timedOut ? "Clip inspection took too long. Please retry." : cause instanceof Error && cause.name !== "AbortError" ? cause.message : "Clip inspection failed. Please retry.");
    } finally {
      clearTimeout(timer);
      if (timeoutRef.current === timer) timeoutRef.current = null;
      if (active.current === controller) active.current = null;
      if (version.current === current) setChecking(false);
    }
  }

  return <section className="reference-section footage-section" aria-labelledby="footage-title">
    <p className="eyebrow">Your footage</p>
    <h2 id="footage-title">{projectId ? "Save one clip" : "Inspect one clip"}</h2>
    <p className="reference-help">MP4 or MOV, up to 100 MiB, 120 seconds, and 4096 pixels in either dimension. Audio is optional.</p>
    {!(projectId && details) && <form onSubmit={check}>
      <label htmlFor="clip-file">Video clip</label>
      <div className="reference-form-row">
        <input id="clip-file" ref={input} type="file" accept=".mp4,.mov,video/mp4,video/quicktime"
          onChange={(event) => { invalidate(); setFile(event.target.files?.[0] || null); }} />
        <button type="submit" disabled={!file || checking}>{checking ? "Inspecting…" : error ? "Retry inspection" : projectId ? "Save clip" : "Inspect clip"}</button>
      </div>
      {file && <p className="hint">Selected: {file.name}</p>}
    </form>}
    {checking && <p role="status" aria-live="polite">Uploading and inspecting clip…</p>}
    {error && <p className="error" role="alert">{error}</p>}
    {details && <div className="reference-card" aria-label="Accepted clip details">
      <p className="eyebrow">Clip accepted</p>
      <h3>{details.filename}</h3>
      <p>Size: {(details.size_bytes / (1024 * 1024)).toFixed(2)} MiB · Duration: {details.duration_seconds} seconds</p>
      <p>Dimensions: {details.width} × {details.height} · Video codec: {details.video_codec}</p>
      <p>Audio: {details.has_audio ? details.audio_codec || "Present" : "None"} · Frame rate: {details.frame_rate === null ? "Unknown" : `${details.frame_rate} fps`}</p>
    </div>}
    {file && !(projectId && details) && <button className="clear-clip" type="button" onClick={() => { invalidate(); setFile(null); if (input.current) input.current.value = ""; }}>Clear clip</button>}
    <p className="hint retention-note">{projectId ? details ? "Your clip is saved locally for this project. It is not edited. One clip per project; replacement comes later." : "Choose a clip to validate and save locally for this project. One clip per project; replacement comes later." : "This clip is inspected, then deleted. It is not retained or edited. Upload it again after refresh or when persistent editing becomes available."}</p>
  </section>;
}
