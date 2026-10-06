"use client";

import { useEffect, useRef, useState } from "react";
type Feature = { available: boolean; message: string; details: Record<string, string> };
type Readiness = Record<"captions" | "transcription" | "ocr", Feature>;
const labels = { captions: "Caption rendering and fonts", transcription: "Automatic transcription", ocr: "Reference OCR" };
const apiBase = (process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
function read(data: unknown): Readiness {
  const result = data as Readiness;
  if (!result || !Object.keys(labels).every(key => { const f = result[key as keyof Readiness]; return f && typeof f.available === "boolean" && typeof f.message === "string" && f.message.length <= 2000 && f.details && Object.keys(f.details).length <= 12 && Object.values(f.details).every(v => typeof v === "string" && v.length <= 2000); })) throw new Error("Invalid readiness response. Optional features remain separate from basic export.");
  return result;
}
export function OptionalFeatures({ projectId }: { projectId: string }) {
  const [value, setValue] = useState<Readiness | null>(null), [busy, setBusy] = useState(false), [error, setError] = useState("");
  const action = useRef<AbortController | null>(null);
  useEffect(() => () => action.current?.abort(), [projectId]);
  async function check() {
    if (action.current) return;
    const controller = new AbortController(); action.current = controller; setBusy(true); setError("");
    const timer = setTimeout(() => controller.abort(), 45000);
    try {
      const response = await fetch(`${apiBase}/api/projects/${encodeURIComponent(projectId)}/optional-features`, { signal: controller.signal });
      const data = await response.json(); if (!response.ok) throw new Error(data?.error?.message || "Check readiness after the current media job finishes.");
      if (!controller.signal.aborted) setValue(read(data));
    } catch (cause) { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "Readiness check failed."); else setError("Check timed out. Retry explicitly; manual controls remain available."); }
    finally { clearTimeout(timer); if (action.current === controller) { action.current = null; setBusy(false); } }
  }
  return <section className="reference-section" aria-label="Optional local features"><h3>Optional local features</h3>
    <p className="hint">A basic whole-clip color export needs no captions, OCR, transcription, font matching or pacing. Check setup explicitly; checks do not download assets or run recognition. Actual saved captions and chosen glyphs are checked separately.</p>
    <button disabled={busy} onClick={() => void check()}>{busy ? "Checking optional features…" : value ? "Recheck optional features" : "Check optional features"}</button>
    {!value && !busy && <p>Readiness not checked in this session.</p>}
    {error && <p role="alert">{error}</p>}
    {value && Object.entries(labels).map(([key, label]) => { const feature = value[key as keyof Readiness]; return <div key={key}><h4>{label}: {feature.available ? "Setup ready" : "Setup needed"}</h4><p>{feature.message}</p>{!!Object.keys(feature.details).length && <details><summary>{label} setup and technical details</summary><dl className="clip-details">{Object.entries(feature.details).map(([name, text]) => <div key={name}><dt>{name}</dt><dd>{text}</dd></div>)}</dl></details>}</div>; })}
  </section>;
}
