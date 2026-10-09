"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";

type ReferenceDetails = {
  provider: "tiktok";
  video_id: string;
  canonical_url: string;
  title: string | null;
  author_name: string | null;
  metadata_status: "available";
  analysis_status: "not_started";
};

type Phase = "idle" | "checking" | "ready" | "selected";
const apiBase = (process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

async function fetchReference(url: string, signal: AbortSignal): Promise<ReferenceDetails> {
  const response = await fetch(`${apiBase}/api/references/inspect`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url }), signal,
  });
  const data: unknown = await response.json();
  if (!response.ok) {
    if (typeof data === "object" && data !== null && "error" in data &&
        typeof data.error === "object" && data.error !== null && "message" in data.error &&
        typeof data.error.message === "string") throw new Error(data.error.message);
    throw new Error("Reference details could not be loaded. Please try again.");
  }
  if (typeof data !== "object" || data === null || !("provider" in data) || data.provider !== "tiktok" ||
      !("video_id" in data) || typeof data.video_id !== "string" || !("canonical_url" in data) ||
      typeof data.canonical_url !== "string" || !("metadata_status" in data) || data.metadata_status !== "available" ||
      !("analysis_status" in data) || data.analysis_status !== "not_started" ||
      !("title" in data) || (data.title !== null && typeof data.title !== "string") ||
      !("author_name" in data) || (data.author_name !== null && typeof data.author_name !== "string")) {
    throw new Error("Reference details could not be loaded. Please try again.");
  }
  return data as ReferenceDetails;
}

export function ReferenceSelection({ onSelectedChange = () => {} }: { onSelectedChange?: (videoId: string | null, url?: string) => void }) {
  const [url, setUrl] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [reference, setReference] = useState<ReferenceDetails | null>(null);
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
    setReference(null);
    setPhase("idle");
    setError("");
    onSelectedChange(null);
  }

  async function check(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (active.current) return;
    if (!url.trim()) { setError("Enter a full TikTok video URL."); return; }
    const controller = new AbortController();
    active.current = controller;
    const current = ++version.current;
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; controller.abort(); }, 15000);
    timeoutRef.current = timer;
    setError("");
    setReference(null);
    setPhase("checking");
    try {
      const details = await fetchReference(url, controller.signal);
      if (version.current === current) { setReference(details); setPhase("ready"); }
    } catch (cause) {
      if (version.current === current) {
        setError(timedOut ? "Reference check took too long. Please try again." : cause instanceof Error && cause.name !== "AbortError" ? cause.message : "Reference details could not be loaded. Please try again.");
        setPhase("idle");
      }
    } finally {
      clearTimeout(timer);
      if (timeoutRef.current === timer) timeoutRef.current = null;
      if (active.current === controller) active.current = null;
    }
  }

  function changeReference() {
    invalidate();
    requestAnimationFrame(() => input.current?.focus());
  }

  return <section className="reference-section" aria-labelledby="reference-title">
    <p className="eyebrow">Start with a reference</p>
    <h2 id="reference-title">TikTok reference</h2>
    <p className="reference-help">Paste a full TikTok video URL to load its public details. Upload your own footage separately after selecting a reference.</p>
    {phase !== "selected" && <form onSubmit={check} noValidate>
      <label htmlFor="tiktok-url">TikTok video URL</label>
      <div className="reference-form-row">
        <input id="tiktok-url" ref={input} type="url" value={url} onChange={(event) => { invalidate(); setUrl(event.target.value); }}
          placeholder="https://www.tiktok.com/@creator/video/123456789" autoComplete="url" aria-describedby="reference-hint reference-error" />
        <button className="button-primary" type="submit" disabled={phase === "checking"}>{phase === "checking" ? "Checking…" : "Check reference"}</button>
      </div>
      <p id="reference-hint" className="hint">Full video links only. Short links are not supported yet.</p>
    </form>}
    {phase === "checking" && <p role="status" aria-live="polite">Loading reference details…</p>}
    {error && <p id="reference-error" className="error" role="alert">{error}</p>}
    {reference && <div className="reference-card" aria-label={phase === "selected" ? "Selected reference" : "Reference details"}>
      <p className="eyebrow">{phase === "selected" ? "Selected reference" : "Reference details"}</p>
      <h3>{reference.title || "Untitled TikTok video"}</h3>
      <p>Creator: {reference.author_name || "Unknown"}</p>
      <a href={reference.canonical_url} target="_blank" rel="noopener noreferrer">View on TikTok</a>
      <p className="analysis-note">Reference details loaded. Save a project and upload footage, then prepare its analyses.</p>
      <div className="reference-actions">
        {phase === "ready" && <button className="button-primary" type="button" onClick={() => { setPhase("selected"); onSelectedChange(reference.video_id, reference.canonical_url); }}>Use this reference</button>}
        {phase === "selected" && <button type="button" onClick={changeReference}>Change reference</button>}
        <button type="button" onClick={() => { invalidate(); setUrl(""); }}>Clear</button>
      </div>
    </div>}
  </section>;
}
