"use client";

import { useEffect, useRef, useState } from "react";
import { parseClipDetails, type ClipDetails } from "./clip-upload";

export const apiBase = (process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
export type SourceClip = { id: string; name: string; primary: boolean; available: boolean; metadata: ClipDetails; sha256: string };
export type Library = { clips: SourceClip[]; total_bytes: number; max_clips: number; max_bytes: number };
export async function clipRequest(projectId: string, suffix: string, body?: unknown, signal?: AbortSignal) {
  const response = await fetch(`${apiBase}/api/projects/${encodeURIComponent(projectId)}${suffix}`, {
    method: body ? "POST" : "GET", signal: signal ?? AbortSignal.timeout(35000),
    ...(body instanceof FormData ? { body } : body ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data?.error?.message || "Project request failed.");
  return data;
}
export function readLibrary(data: unknown): Library {
  const value = data as Library;
  if (!value || !Array.isArray(value.clips) || value.clips.length > 10 || !Number.isSafeInteger(value.total_bytes) || value.total_bytes < 0) throw new Error("Invalid footage library response.");
  return { ...value, clips: value.clips.map(c => {
    if (!c || !/^[0-9a-f-]{36}$/.test(c.id) || typeof c.name !== "string" || typeof c.available !== "boolean" || typeof c.primary !== "boolean" || !/^[0-9a-f]{64}$/.test(c.sha256)) throw new Error("Invalid source clip response.");
    return { ...c, metadata: parseClipDetails(c.metadata) };
  }) };
}
export function ClipLibrary({ projectId, sourceKey, onSaved }: { projectId: string; sourceKey: string; onSaved: () => void }) {
  const [library, setLibrary] = useState<Library | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [names, setNames] = useState<Record<string, string>>({});
  const [remove, setRemove] = useState<string | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const action = useRef<AbortController | null>(null);
  function restore(data: unknown) {
    const value = readLibrary(data); setLibrary(value); setNames(Object.fromEntries(value.clips.map(c => [c.id, c.name])));
  }
  useEffect(() => {
    const controller = new AbortController();
    void clipRequest(projectId, "/clips", undefined, controller.signal).then(data => { if (!controller.signal.aborted && !action.current) restore(data); }).catch(e => { if (!controller.signal.aborted) setError(e.message); });
    return () => controller.abort();
  }, [projectId, sourceKey]);
  useEffect(() => () => action.current?.abort(), [projectId]);
  async function run(kind: "upload" | "rename" | "remove", id?: string) {
    if (action.current) return;
    if (kind === "upload" && (!file || file.size > 100 * 1024 * 1024)) { setError("Choose an MP4 or MOV, at most 100 MiB."); return; }
    const controller = new AbortController(); action.current = controller; setBusy(true); setError("");
    const timer = setTimeout(() => controller.abort(), 35000);
    try {
      let data;
      if (kind === "remove") {
        const response = await fetch(`${apiBase}/api/projects/${encodeURIComponent(projectId)}/clips/${id}`, { method: "DELETE", signal: controller.signal });
        data = await response.json(); if (!response.ok) throw new Error(data?.error?.message || "Clip removal failed.");
      } else {
        const body = new FormData(); if (file) body.append("file", file);
        data = await clipRequest(projectId, `/clips${kind === "rename" ? `/${id}` : ""}`, kind === "rename" ? { name: names[id!] } : body, controller.signal);
      }
      if (!controller.signal.aborted) { restore(data); setFile(null); setRemove(null); onSaved(); window.dispatchEvent(new CustomEvent("reframe-clips", { detail: projectId })); }
    } catch (e) { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "Clip request failed."); else setError("Request interrupted. Reload the library before retrying."); }
    finally { clearTimeout(timer); action.current = null; setBusy(false); }
  }
  return <section className="reference-section" aria-label="Footage library">
    <h3>Footage library</h3><p>Up to 10 clips · 100 MiB per file · 500 MiB total. The original clip remains the source for color measurements, whole-clip output and legacy cuts.</p>
    {library && <p role="status">{library.clips.length}/10 clips · {(library.total_bytes / 1024 / 1024).toFixed(1)}/500 MiB retained</p>}
    <label>Add footage <input type="file" accept="video/mp4,video/quicktime,.mp4,.mov" disabled={busy} onChange={e => setFile(e.target.files?.[0] ?? null)} /></label>
    <button disabled={busy || !file || (library?.clips.length ?? 10) >= 10} onClick={() => void run("upload")}>{busy ? "Working…" : "Upload additional clip"}</button>
    {library?.clips.map(c => <div className="clip-library-row" key={c.id}>
      <label>Clip name <input value={names[c.id] ?? c.name} maxLength={80} disabled={busy} onChange={e => setNames(v => ({ ...v, [c.id]: e.target.value }))} /></label>
      <p>{c.primary ? "Original · " : ""}{c.metadata.duration_seconds.toFixed(2)} s · {c.metadata.width} × {c.metadata.height} · {c.metadata.has_audio ? "Audio" : "Silent"}{!c.available ? " · Unavailable" : ""}</p>
      <button disabled={busy || !names[c.id]?.trim() || names[c.id] === c.name} onClick={() => void run("rename", c.id)}>Save clip name</button>
      <button disabled={busy} onClick={() => setRemove(c.id)}>Remove clip</button>
      {remove === c.id && <div role="group" aria-label="Confirm clip removal"><p>Remove {c.name} from this computer? Saved slot assignments must be removed or replaced first. Removing the original makes its measurements, recipe and legacy cut plan stale. Previous output remains playable.</p><button disabled={busy} onClick={() => void run("remove", c.id)}>Confirm removal</button><button onClick={() => setRemove(null)}>Keep clip</button></div>}
    </div>)}
    {error && <p role="alert">{error}</p>}
  </section>;
}
