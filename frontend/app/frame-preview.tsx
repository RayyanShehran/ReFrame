"use client";

import Image from "next/image";
import { useEffect, useRef, useState } from "react";
import { useWorkspaceNavigation, useWorkspaceReport } from "./guided-workspace";
import type { FramingState } from "./framing-controls";

type Picture = { width: number; height: number; png_base64: string };
type Preview = { schema_version: 1; source: { project_id: string; clip_id: string; media_sha256: string }; requested_timestamp_seconds: number; timestamp_seconds: number; recipe_revision: number; framing_revision: number; original: Picture; edited: Picture; warnings: string[]; ffmpeg_version: string };
const apiBase = (process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
function parse(data: unknown, projectId: string, revision: number, framingRevision: number, timestamp: number): Preview {
  const p = data as Preview;
  const validImage = (image: Picture) => image && [image.width, image.height].every(n => Number.isInteger(n) && n >= 2 && n <= 960) && typeof image.png_base64 === "string" && image.png_base64.length <= 4194304 && /^iVBORw0KGgo[A-Za-z0-9+/]*={0,2}$/.test(image.png_base64);
  if (!p || p.schema_version !== 1 || p.source?.project_id !== projectId || !/^clip-[0-9a-f]{32}\.(mp4|mov)$/.test(p.source.clip_id) || !/^[0-9a-f]{64}$/.test(p.source.media_sha256) || p.recipe_revision !== revision || p.framing_revision !== framingRevision || p.requested_timestamp_seconds !== timestamp || !Number.isFinite(p.timestamp_seconds) || p.timestamp_seconds < 0 || p.timestamp_seconds > timestamp || !validImage(p.original) || !validImage(p.edited) || !Array.isArray(p.warnings) || p.warnings.length > 4 || !p.warnings.every(w => typeof w === "string") || typeof p.ffmpeg_version !== "string") throw new Error("Invalid preview response. Retry explicitly.");
  return p;
}

export function FramePreview({ projectId, duration, revision, recipeReady, recipeDirty, recipeBusy, framing }: { projectId: string; duration: number | null; revision: number | null; recipeReady: boolean; recipeDirty: boolean; recipeBusy: boolean; framing: FramingState }) {
  const [draftTime, setTime] = useState<string | null>(null);
  const time = draftTime ?? (duration !== null ? String(duration / 2) : "");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const action = useRef<AbortController | null>(null), generation = useRef(0);
  const navigation = useWorkspaceNavigation();
  const validTime = duration !== null && !!time.trim() && Number.isFinite(Number(time)) && Number(time) >= 0 && Number(time) <= duration;
  const ready = recipeReady && revision !== null && framing.ready && framing.revision !== null && !recipeBusy && !framing.busy;
  const outdated = !!preview && (!recipeReady || preview.recipe_revision !== revision || preview.framing_revision !== framing.revision);
  useWorkspaceReport("frame-preview", "style", busy ? "Working" : error ? "Needs attention" : "Ready", "Updating before/after preview…");
  useEffect(() => () => { generation.current++; action.current?.abort(); action.current = null; setBusy(false); }, [projectId, revision, framing.revision, recipeReady]);
  function changeTime(value: string) {
    generation.current++; action.current?.abort(); action.current = null; setBusy(false); setError(""); setTime(value);
  }
  async function update() {
    if (!ready || !validTime || action.current) return;
    const controller = new AbortController(); action.current = controller;
    const version = ++generation.current, timestamp = Number(time);
    const recipeRevision = revision!, framingRevision = framing.revision!;
    const timer = setTimeout(() => controller.abort(), 35000);
    setBusy(true); setError("");
    try {
      const response = await fetch(`${apiBase}/api/projects/${encodeURIComponent(projectId)}/frame-preview`, { method: "POST", signal: controller.signal, headers: { "Content-Type": "application/json" }, body: JSON.stringify({ expected_recipe_revision: recipeRevision, expected_framing_revision: framingRevision, timestamp_seconds: timestamp }) });
      const data = await response.json();
      if (!response.ok) throw new Error(data?.error?.message || "Preview failed. Retry explicitly.");
      const result = parse(data, projectId, recipeRevision, framingRevision, timestamp);
      if (generation.current === version && !controller.signal.aborted) setPreview(result);
    } catch (cause) {
      if (generation.current === version) setError(cause instanceof Error && cause.name !== "AbortError" ? cause.message : "Preview request timed out. Previous images are retained; retry explicitly.");
    } finally {
      clearTimeout(timer);
      if (action.current === controller) { action.current = null; setBusy(false); }
    }
  }
  return <section className="reference-section" aria-label="Before and after frame preview">
    <h3 id={`preview-${projectId}`} tabIndex={-1}>Before / after frame preview</h3>
    <p className="hint">A still frame using saved color and framing. Review cuts, audio and captions through the video render. Images may downscale to a maximum edge of 960 pixels; this is not an encoded-video quality preview.</p>
    <label>Source time (seconds)<input type="number" min="0" max={duration ?? 0} step="any" value={time} disabled={duration === null} onChange={e => changeTime(e.target.value)} /></label>
    {duration === null ? <p>Save footage to choose a source frame.</p> : <p className="hint">Footage: {duration.toFixed(3)} seconds. The clip end selects its last available frame.</p>}
    {duration !== null && !validTime && <p role="alert">Choose a finite time from 0 to {duration} seconds.</p>}
    {!recipeReady && <p>Generate or regenerate a valid saved color recipe before previewing.</p>}
    {(recipeDirty || framing.dirty) && <p role="status">Preview excludes unsaved edits and uses saved settings. {recipeDirty && <button onClick={() => navigation?.open("style", `recipe-${projectId}-brightness`)}>Review and save recipe</button>} {framing.dirty && <button onClick={() => navigation?.open("export")}>Review and save framing</button>}</p>}
    <button disabled={!ready || !validTime || busy} onClick={() => void update()}>{busy ? "Updating preview…" : error ? "Retry preview" : "Update preview"}</button>
    {!preview && !busy && !error && <p>No preview yet. Select a time and update explicitly.</p>}
    {busy && <p role="status">Decoding one footage frame… Previous images remain visible. Processing is capped at 30 seconds.</p>}
    {error && <p role="alert">{error}</p>}
    {preview && <>
      <p role="status">{outdated ? "Outdated preview" : "Frame preview"} · Source {preview.timestamp_seconds.toFixed(3)} seconds (requested {preview.requested_timestamp_seconds.toFixed(3)}) · Recipe revision {preview.recipe_revision} · Framing revision {preview.framing_revision}</p>
      {outdated && <p>Saved color/framing changed or the recipe became unavailable. Update explicitly; previous images remain available.</p>}
      <div className="frame-preview-images">{(["original", "edited"] as const).map(key => <figure key={key}><figcaption>{key === "original" ? "Original" : "Edited"}</figcaption><Image unoptimized src={`data:image/png;base64,${preview[key].png_base64}`} width={preview[key].width} height={preview[key].height} alt={`${key === "original" ? "Original footage, entire normalized image without creative adjustments" : "Edited footage with saved color recipe and framing"}, source ${preview.timestamp_seconds.toFixed(3)} seconds, recipe revision ${preview.recipe_revision}, framing revision ${preview.framing_revision}`} /></figure>)}</div>
      {preview.warnings.map(warning => <p role="note" key={warning}>{warning}</p>)}
    </>}
  </section>;
}
