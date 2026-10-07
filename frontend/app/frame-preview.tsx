"use client";

import Image from "next/image";
import { useEffect, useRef, useState } from "react";
import { useWorkspaceNavigation, useWorkspaceReport } from "./guided-workspace";
import type { GradingState } from "./grading-controls";
import type { FramingState } from "./framing-controls";

type Picture = { width: number; height: number; png_base64: string };
type Preview = { inspectedVersion?: string; schema_version: 1; source: { project_id: string; clip_id: string; media_sha256: string }; requested_timestamp_seconds: number; timestamp_seconds: number; recipe_revision: number; framing_revision: number; grading_revision?: number; clip_id?: string; slot_id?: string | null; reference?: Picture | null; original: Picture; edited: Picture; warnings: string[]; ffmpeg_version: string };
const apiBase = (process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
function parse(data: unknown, projectId: string, revision: number, framingRevision: number, timestamp: number): Preview {
  const p = data as Preview;
  const validImage = (image: Picture) => image && [image.width, image.height].every(n => Number.isInteger(n) && n >= 2 && n <= 960) && typeof image.png_base64 === "string" && image.png_base64.length <= 4194304 && /^iVBORw0KGgo[A-Za-z0-9+/]*={0,2}$/.test(image.png_base64);
  if (!p || p.schema_version !== 1 || p.source?.project_id !== projectId || !/^clip-[0-9a-f]{32}\.(mp4|mov)$/.test(p.source.clip_id) || !/^[0-9a-f]{64}$/.test(p.source.media_sha256) || p.recipe_revision !== revision || p.framing_revision !== framingRevision || p.requested_timestamp_seconds !== timestamp || !Number.isFinite(p.timestamp_seconds) || p.timestamp_seconds < 0 || p.timestamp_seconds > timestamp || (p.reference && !validImage(p.reference)) || !validImage(p.original) || !validImage(p.edited) || !Array.isArray(p.warnings) || p.warnings.length > 4 || !p.warnings.every(w => typeof w === "string") || typeof p.ffmpeg_version !== "string") throw new Error("Invalid preview response. Retry explicitly.");
  return p;
}

export function FramePreview({ projectId, duration, revision, recipeReady, recipeDirty, recipeBusy, framing, grading }: { projectId: string; duration: number | null; revision: number | null; recipeReady: boolean; recipeDirty: boolean; recipeBusy: boolean; framing: FramingState; grading?: GradingState }) {
  const [choiceKey, setChoiceKey] = useState("");
  const choice = grading?.choices.find(c => c.key === choiceKey) ?? grading?.choices[0];
  const rangeStart = choice?.start ?? 0, rangeEnd = choice?.end ?? duration;
  const [draftTime, setTime] = useState<string | null>(null);
  const time = draftTime ?? (rangeEnd !== null ? String((rangeStart + rangeEnd) / 2) : "");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const action = useRef<AbortController | null>(null), generation = useRef(0);
  const navigation = useWorkspaceNavigation();
  const validTime = duration !== null && !!time.trim() && Number.isFinite(Number(time)) && Number(time) >= rangeStart && Number(time) <= (rangeEnd ?? 0) && (!choice?.slotId || Number(time) < (rangeEnd ?? 0));
  const ready = recipeReady && revision !== null && framing.ready && framing.revision !== null && !recipeBusy && !framing.busy;
  const outdated = !!preview && (!recipeReady || preview.recipe_revision !== revision || preview.framing_revision !== framing.revision || (grading && (preview.grading_revision !== grading.revision || preview.clip_id !== choice?.clipId || preview.slot_id !== (choice?.slotId ?? null) || preview.inspectedVersion !== choice?.version)));
  useWorkspaceReport("frame-preview", "style", busy ? "Working" : error ? "Needs attention" : "Ready", "Updating before/after preview…");
  useEffect(() => () => { generation.current++; action.current?.abort(); action.current = null; setBusy(false); }, [projectId, revision, framing.revision, recipeReady, grading?.revision, choice?.key]);
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
      const response = await fetch(`${apiBase}/api/projects/${encodeURIComponent(projectId)}/frame-preview`, { method: "POST", signal: controller.signal, headers: { "Content-Type": "application/json" }, body: JSON.stringify({ expected_recipe_revision: recipeRevision, expected_framing_revision: framingRevision, timestamp_seconds: timestamp, ...(grading ? { expected_grading_revision: grading.revision, clip_id: choice?.clipId, slot_id: choice?.slotId, expected_sequence_revision: choice?.slotId ? grading.sequenceRevision : undefined } : {}) }) });
      const data = await response.json();
      if (!response.ok) throw new Error(data?.error?.message || "Preview failed. Retry explicitly.");
      const result = parse(data, projectId, recipeRevision, framingRevision, timestamp);
      if (grading && (result.grading_revision !== grading.revision || result.clip_id !== choice?.clipId || result.slot_id !== (choice?.slotId ?? null))) throw new Error("Color selection changed. Update explicitly.");
      if (generation.current === version && !controller.signal.aborted) setPreview({ ...result, inspectedVersion: choice?.version });
    } catch (cause) {
      if (generation.current === version) setError(cause instanceof Error && cause.name !== "AbortError" ? cause.message : "Preview request timed out. Previous images are retained; retry explicitly.");
    } finally {
      clearTimeout(timer);
      if (action.current === controller) { action.current = null; setBusy(false); }
    }
  }
  return <section className="reference-section" aria-label="Before and after frame preview">
    <h3 id={`preview-${projectId}`} tabIndex={-1}>Reference / original / graded frame</h3>
    <p className="hint">A still frame using saved color and framing. Review cuts, audio and captions through the video render. Images may downscale to a maximum edge of 960 pixels; this is not an encoded-video quality preview.</p>
    {grading && <label>Clip or slot to inspect<select value={choice?.key ?? ""} onChange={e => { setChoiceKey(e.target.value); changeTime(""); setTime(null); }}>{grading.choices.map(c => <option key={c.key} value={c.key}>{c.name}</option>)}</select></label>}
    <label>Source time (seconds)<input type="number" min={rangeStart} max={rangeEnd ?? 0} step="any" value={time} disabled={duration === null} onChange={e => changeTime(e.target.value)} /></label>
    {duration === null ? <p>Save footage to choose a source frame.</p> : <p className="hint">Selected source range: {rangeStart.toFixed(3)}–{(rangeEnd ?? duration).toFixed(3)} seconds. The clip end selects its last available frame.</p>}
    {duration !== null && !validTime && <p role="alert">Choose a finite time from {rangeStart} to {rangeEnd ?? duration} seconds.</p>}
    {!recipeReady && <p>Save a valid color mode and its required matches before previewing.</p>}
    {(recipeDirty || framing.dirty) && <p role="status">Preview excludes unsaved edits and uses saved settings. {recipeDirty && <button onClick={() => navigation?.open("style", `recipe-${projectId}-brightness`)}>{grading ? "Review and save color" : "Review and save recipe"}</button>} {framing.dirty && <button onClick={() => navigation?.open("export")}>Review and save framing</button>}</p>}
    <button disabled={!ready || !validTime || busy} onClick={() => void update()}>{busy ? "Updating preview…" : error ? "Retry preview" : "Update preview"}</button>
    {!preview && !busy && !error && <p>No preview yet. Select a time and update explicitly.</p>}
    {busy && <p role="status">Decoding one footage frame… Previous images remain visible. Processing is capped at 30 seconds.</p>}
    {error && <p role="alert">{error}</p>}
    {preview && <>
      <p role="status">{outdated ? "Outdated preview" : "Frame preview"} · Source {preview.timestamp_seconds.toFixed(3)} seconds (requested {preview.requested_timestamp_seconds.toFixed(3)}) · {grading ? `Color revision ${preview.grading_revision}` : `Recipe revision ${preview.recipe_revision}`} · Framing revision {preview.framing_revision}</p>
      {outdated && <p>Saved color/framing changed or a required match became unavailable. Update explicitly; previous images remain available.</p>}
      <div className="frame-preview-images">{preview.reference && <figure><figcaption>Reference look</figcaption><Image unoptimized src={`data:image/png;base64,${preview.reference.png_base64}`} width={preview.reference.width} height={preview.reference.height} alt="Reference color look" /></figure>}{(["original", "edited"] as const).map(key => <figure key={key}><figcaption>{key === "original" ? "Original" : "Graded"}</figcaption><Image unoptimized src={`data:image/png;base64,${preview[key].png_base64}`} width={preview[key].width} height={preview[key].height} alt={`${key === "original" ? "Original footage, entire normalized image without creative adjustments" : "Graded footage with saved color settings and framing"}, source ${preview.timestamp_seconds.toFixed(3)} seconds, recipe revision ${preview.recipe_revision}, framing revision ${preview.framing_revision}`} /></figure>)}</div>
      {preview.warnings.map(warning => <p role="note" key={warning}>{warning}</p>)}
    </>}
  </section>;
}
