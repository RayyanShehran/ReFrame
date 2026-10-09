"use client";

import { useEffect, useRef, useState } from "react";
import { ClipUpload, FootagePicker, parseClipDetails, type ClipDetails } from "./clip-upload";

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
type LibraryProps = { projectId: string; sourceKey: string; onSaved: () => void; initialDetails?: ClipDetails | null; showOriginal?: boolean };
type UploadItem = { id: string; file: File; status: "pending" | "uploading" | "succeeded" | "failed"; error?: string };
const MiB = 1024 * 1024;
function rejected(file: File, count: number, bytes: number) {
  if (!/\.(mp4|mov)$/i.test(file.name)) return "Choose an MP4 or MOV file.";
  if (!file.size) return "The file is empty.";
  if (file.size > 100 * MiB) return "The file exceeds 100 MiB.";
  if (count >= 10) return "This project allows 10 clips, including retained and pending files. Remove a clip or pending item first.";
  if (bytes + file.size > 500 * MiB) return "This file would exceed the 500 MiB project budget, including retained and pending files.";
  return "";
}
export function ClipLibrary(props: LibraryProps) {
  return <LibraryContents key={props.projectId} {...props} />;
}
function LibraryContents({ projectId, sourceKey, onSaved, initialDetails, showOriginal = true }: LibraryProps) {
  const [library, setLibrary] = useState<Library | null>(null);
  const [queue, setQueue] = useState<UploadItem[]>([]);
  const items = useRef<UploadItem[]>([]);
  const action = useRef<AbortController | null>(null);
  function updateQueue(value: UploadItem[]) { items.current = value; setQueue(value); }
  function mark(id: string, status: UploadItem["status"], error?: string) {
    updateQueue(items.current.map(item => item.id === id ? { ...item, status, error } : item));
  }
  function enqueue(files: File[]) {
    if (!library || action.current) return;
    let count = library.clips.length, bytes = library.total_bytes;
    for (const item of items.current) if (item.status === "pending") { count++; bytes += item.file.size; }
    const added = files.map(file => {
      const error = rejected(file, count, bytes);
      if (!error) { count++; bytes += file.size; }
      return { id: crypto.randomUUID(), file, status: error ? "failed" as const : "pending" as const, error };
    });
    updateQueue([...items.current, ...added]);
  }
  const [names, setNames] = useState<Record<string, string>>({});
  const [remove, setRemove] = useState<string | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  function restore(data: unknown) {
    const value = readLibrary(data); setLibrary(value); setNames(Object.fromEntries(value.clips.map(c => [c.id, c.name])));
  }
  useEffect(() => {
    const controller = new AbortController();
    void clipRequest(projectId, "/clips", undefined, controller.signal).then(data => { if (!controller.signal.aborted && !action.current) restore(data); }).catch(e => { if (!controller.signal.aborted) setError(e.message); });
    return () => controller.abort();
  }, [projectId, sourceKey]);
  useEffect(() => { const active = action; return () => { active.current?.abort(); active.current = null; }; }, [projectId]);
  function notifySaved() { onSaved(); window.dispatchEvent(new CustomEvent("reframe-clips", { detail: projectId })); }
  function saved(data: unknown) { restore(data); notifySaved(); }
  async function uploadQueue(retry?: string) {
    if (action.current) return;
    if (retry) mark(retry, "pending");
    const controller = new AbortController(); action.current = controller; setBusy(true); setError("");
    let timer: ReturnType<typeof setTimeout> | undefined;
    let changed = false;
    try {
      timer = setTimeout(() => controller.abort(), 35000);
      let current = readLibrary(await clipRequest(projectId, "/clips", undefined, controller.signal));
      clearTimeout(timer);
      if (controller.signal.aborted) return;
      restore(current);
      while (!controller.signal.aborted) {
        const item = items.current.find(item => item.status === "pending");
        if (!item) break;
        const invalid = rejected(item.file, current.clips.length, current.total_bytes);
        if (invalid) { mark(item.id, "failed", invalid); continue; }
        mark(item.id, "uploading");
        timer = setTimeout(() => controller.abort(), 35000);
        try {
          const body = new FormData(); body.append("file", item.file);
          const result = readLibrary(await clipRequest(projectId, "/clips", body, controller.signal));
          if (controller.signal.aborted) break;
          current = result; mark(item.id, "succeeded"); restore(result); changed = true;
        } catch (e) {
          if (controller.signal.aborted) {
            if (action.current === controller) mark(item.id, "failed", "Request interrupted. Check retained clips before retrying; the server may have saved this file.");
            break;
          }
          mark(item.id, "failed", e instanceof Error ? e.message : "Upload failed. Check retained clips before retrying.");
        } finally { clearTimeout(timer); }
      }
    } catch (e) {
      if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "Could not load the current library.");
      else if (action.current === controller) setError("Request interrupted. Reload the library before retrying.");
    } finally {
      clearTimeout(timer);
      if (action.current === controller) { action.current = null; setBusy(false); if (changed) notifySaved(); }
    }
  }
  async function run(kind: "rename" | "remove", id: string) {
    if (action.current) return;
    const controller = new AbortController(); action.current = controller; setBusy(true); setError("");
    const timer = setTimeout(() => controller.abort(), 35000);
    try {
      let data;
      if (kind === "remove") {
        const response = await fetch(`${apiBase}/api/projects/${encodeURIComponent(projectId)}/clips/${id}`, { method: "DELETE", signal: controller.signal });
        data = await response.json(); if (!response.ok) throw new Error(data?.error?.message || "Clip removal failed.");
      } else {
        data = await clipRequest(projectId, `/clips/${id}`, { name: names[id] }, controller.signal);
      }
      if (!controller.signal.aborted) { saved(data); setRemove(null); }
    } catch (e) { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "Clip request failed."); else setError("Request interrupted. Reload the library before retrying."); }
    finally { clearTimeout(timer); action.current = null; setBusy(false); }
  }
  const original = library?.clips.find(clip => clip.primary);
  return <>
    {showOriginal && <ClipUpload key={original?.id ?? "empty"} projectId={projectId} initialDetails={initialDetails ?? original?.metadata ?? null} onFilesSelected={enqueue} selectionDisabled={busy || !library} />}
    <section className="reference-section" aria-label="Footage library">
    <h3>Footage library</h3><p>Up to 10 clips · 100 MiB per file · 500 MiB total. The original clip remains the source for color measurements, whole-clip output and legacy cuts.</p>
    {library && <p role="status">{library.clips.length}/10 clips · {(library.total_bytes / 1024 / 1024).toFixed(1)}/500 MiB retained</p>}
    <FootagePicker label="Add footage" disabled={busy || !library} onFilesSelected={enqueue} />
    <button className="button-primary" disabled={busy || !library || !queue.some(item => item.status === "pending")} onClick={() => void uploadQueue()}>{busy ? "Working…" : "Upload queued files"}</button>
    <ul className="upload-queue" aria-label="Upload queue" aria-live="polite">
      {queue.map(item => <li key={item.id} data-state={item.status}>
        <span>{item.file.name} · {({ pending: "Pending", uploading: "Uploading", succeeded: "Succeeded", failed: "Failed" })[item.status]}{item.error && <span className="error"> — {item.error}</span>}</span>
        {item.status === "pending" && <button aria-label={`Remove pending ${item.file.name}`} onClick={() => updateQueue(items.current.filter(other => other.id !== item.id))}>Remove pending</button>}
        {item.status === "failed" && <button disabled={busy || !library} aria-label={`Retry ${item.file.name}`} onClick={() => void uploadQueue(item.id)}>Retry</button>}
      </li>)}
    </ul>
    <p className="hint">Successful files stay saved if another fails. Switching projects stops further submissions; an in-flight file may already have been saved. The queue is temporary and clears on refresh.</p>
    {library?.clips.map(c => <div className="clip-library-row" key={c.id}>
      <label>Clip name <input value={names[c.id] ?? c.name} maxLength={80} disabled={busy} onChange={e => setNames(v => ({ ...v, [c.id]: e.target.value }))} /></label>
      <p>{c.primary ? "Original · " : ""}{c.metadata.duration_seconds.toFixed(2)} s · {c.metadata.width} × {c.metadata.height} · {c.metadata.has_audio ? "Audio" : "Silent"}{!c.available ? " · Unavailable" : ""}</p>
      <button disabled={busy || !names[c.id]?.trim() || names[c.id] === c.name} onClick={() => void run("rename", c.id)}>Save clip name</button>
      <button disabled={busy} onClick={() => setRemove(c.id)}>Remove clip</button>
      {remove === c.id && <div role="group" aria-label="Confirm clip removal"><p>Remove {c.name} from this computer? Saved slot assignments must be removed or replaced first. Removing the original makes its measurements, recipe and legacy cut plan stale. Previous output remains playable.</p><button disabled={busy} onClick={() => void run("remove", c.id)}>Confirm removal</button><button onClick={() => setRemove(null)}>Keep clip</button></div>}
    </div>)}
    {error && <p role="alert">{error}</p>}
  </section></>;
}
