"use client";

import Image from "next/image";
import { useEffect, useRef, useState } from "react";
import type { FramingState } from "./framing-controls";
import type { PlanState } from "./edit-plan";
import { fontLabel, validFont, type FontBinding } from "./caption-style-controls";
import { useWorkspaceNavigation, useWorkspaceReport } from "./guided-workspace";

type Picture = { width: number; height: number; png_base64: string };
type Reference = { operation_id: string; status: string; media: { duration_seconds: number; sha256: string } | null };
type ReferenceFrame = { schema_version: 1; project_id: string; reference_operation_id: string; source: { media_sha256: string }; requested_timestamp_seconds: number; timestamp_seconds: number; image: Picture; warnings: string[] };
type CaptionFrame = { schema_version: 1; source: { project_id: string; media_sha256: string }; recipe_revision: number; framing_revision: number; caption_revision: number; font: FontBinding; cue_index: number; cue_start_seconds: number; cue_end_seconds: number; output_timestamp_seconds: number; source_timestamp_seconds: number; mode: "whole" | "cuts"; plan_revision: number | null; image: Picture; warnings: string[] };
export type PreviewTrack = { revision: number; enabled: boolean; cues: { start: number; end: number; text: string }[]; font_binding?: FontBinding; timeline: { mode: "whole" | "cuts"; plan_revision: number | null } | null };
export type AppearanceContext = { recipeRevision: number | null; recipeReady: boolean; recipeDirty: boolean; recipeBusy: boolean; framing: FramingState };
const apiBase = (process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
const number = (v: unknown) => typeof v === "number" && Number.isFinite(v) && v >= 0 && v <= 120.1;
const image = (p: Picture) => p && [p.width, p.height].every(n => Number.isInteger(n) && n >= 2 && n <= 960) && typeof p.png_base64 === "string" && p.png_base64.length <= 4194304 && /^iVBORw0KGgo[A-Za-z0-9+/]*={0,2}$/.test(p.png_base64);
const warnings = (v: unknown): v is string[] => Array.isArray(v) && v.length <= 12 && v.every(w => typeof w === "string" && w.length <= 2000);
export function CaptionAppearancePreview({ projectId, track, ready, dirty, disabled, context, plan }: { projectId: string; track: PreviewTrack; ready: boolean; dirty: boolean; disabled: boolean; context: AppearanceContext; plan: PlanState }) {
  const [reference, setReference] = useState<Reference | null>(null), [referenceFrame, setReferenceFrame] = useState<ReferenceFrame | null>(null), [captionFrame, setCaptionFrame] = useState<CaptionFrame | null>(null);
  const [time, setTime] = useState("0"), [cue, setCue] = useState(0), [busy, setBusy] = useState(false), [error, setError] = useState("");
  const action = useRef<AbortController | null>(null), generation = useRef(0);
  const navigation = useWorkspaceNavigation();
  const token = `${projectId}:${track.revision}:${track.font_binding?.sha256}:${context.recipeRevision}:${context.framing.revision}:${ready}:${context.recipeReady}:${plan.revision}`;
  const validTime = !!reference?.media && !!time.trim() && number(Number(time)) && Number(time) <= reference.media.duration_seconds;
  const unsaved = dirty || context.recipeDirty || context.framing.dirty || (track.timeline?.mode === "cuts" && plan.dirty);
  const canPreview = ready && track.enabled && !!track.cues[cue] && context.recipeReady && context.recipeRevision !== null && context.framing.ready && context.framing.revision !== null && !context.recipeBusy && !context.framing.busy && !disabled && !unsaved;
  const outdated = !!captionFrame && (!ready || !context.recipeReady || captionFrame.caption_revision !== track.revision || captionFrame.font.sha256 !== track.font_binding?.sha256 || captionFrame.recipe_revision !== context.recipeRevision || captionFrame.framing_revision !== context.framing.revision || captionFrame.plan_revision !== track.timeline?.plan_revision);
  function cancel() { generation.current++; action.current?.abort(); action.current = null; setBusy(false); }
  useEffect(() => () => cancel(), [token]);
  useEffect(() => { void run("status"); return () => cancel(); }, [projectId]); // eslint-disable-line react-hooks/exhaustive-deps
  async function run(kind: "status" | "reference" | "caption") {
    if (action.current || (kind === "reference" && (!validTime || reference?.status !== "ready")) || (kind === "caption" && !canPreview)) return;
    const controller = new AbortController(); action.current = controller; const current = ++generation.current;
    setBusy(true); setError(""); const timer = setTimeout(() => controller.abort(), 35000);
    try {
      const path = kind === "status" ? "reference-media" : kind === "reference" ? "reference-frame" : "caption-preview";
      const body = kind === "reference" ? { timestamp_seconds: Number(time), expected_reference_operation_id: reference!.operation_id } : { cue_index: cue, expected_recipe_revision: context.recipeRevision, expected_framing_revision: context.framing.revision, expected_caption_revision: track.revision, expected_plan_revision: track.timeline?.mode === "cuts" ? track.timeline.plan_revision : null };
      const response = await fetch(`${apiBase}/api/projects/${encodeURIComponent(projectId)}/${path}`, { signal: controller.signal, method: kind === "status" ? "GET" : "POST", ...(kind !== "status" ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}) });
      const data = await response.json();
      if (!response.ok) throw new Error(data?.error?.message || "Frame request failed. Retry explicitly.");
      if (kind === "status") {
        const r = data as Reference;
        if (!r || !["idle", "running", "ready", "failed"].includes(r.status) || (r.status === "ready" && (!r.media || !number(r.media.duration_seconds) || !/^[0-9a-f]{64}$/.test(r.media.sha256) || !/^[0-9a-f-]{36}$/.test(r.operation_id)))) throw new Error("Invalid reference status.");
        if (generation.current === current && !controller.signal.aborted) setReference(r);
      } else if (kind === "reference") {
        const r = data as ReferenceFrame;
        if (!r || r.schema_version !== 1 || r.project_id !== projectId || r.reference_operation_id !== reference!.operation_id || r.source?.media_sha256 !== reference!.media?.sha256 || r.requested_timestamp_seconds !== Number(time) || !number(r.timestamp_seconds) || r.timestamp_seconds > Number(time) || !image(r.image) || !warnings(r.warnings)) throw new Error("Invalid reference frame response.");
        if (generation.current === current && !controller.signal.aborted) setReferenceFrame(r);
      } else {
        const r = data as CaptionFrame, savedCue = track.cues[cue];
        if (!r || r.schema_version !== 1 || r.source?.project_id !== projectId || !/^[0-9a-f]{64}$/.test(r.source.media_sha256) || r.recipe_revision !== context.recipeRevision || r.framing_revision !== context.framing.revision || r.caption_revision !== track.revision || r.cue_index !== cue || r.cue_start_seconds !== savedCue.start || r.cue_end_seconds !== savedCue.end || !number(r.output_timestamp_seconds) || r.output_timestamp_seconds < savedCue.start || r.output_timestamp_seconds >= savedCue.end || !number(r.source_timestamp_seconds) || r.mode !== track.timeline?.mode || r.plan_revision !== track.timeline?.plan_revision || !validFont(r.font) || (track.font_binding && r.font.sha256 !== track.font_binding.sha256) || !image(r.image) || !warnings(r.warnings)) throw new Error("Invalid caption preview response.");
        if (generation.current === current && !controller.signal.aborted) setCaptionFrame(r);
      }
    } catch (cause) { if (generation.current === current) setError(cause instanceof Error && cause.name !== "AbortError" ? cause.message : "Frame request timed out. Previous images remain available; retry explicitly."); }
    finally { clearTimeout(timer); if (action.current === controller) { action.current = null; setBusy(false); } }
  }
  useWorkspaceReport("caption-preview", "audio", busy ? "Working" : error ? "Needs attention" : "Ready", "Updating caption comparison…");
  return <section className="reference-section" aria-label="Caption appearance comparison"><h4>Reference / caption appearance</h4>
    <p className="hint">Reference appearance not automatically verified. Inspect a caption-bearing source frame, choose the actual font and compare manually. No reference scanning or font identification.</p>
    <label>Reference source time (seconds)<input type="number" min="0" max={reference?.media?.duration_seconds ?? 0} step="any" value={time} disabled={!reference?.media} onChange={e => { cancel(); setTime(e.target.value); setError(""); }} /></label>
    {reference?.media && <p>Retained reference: {reference.media.duration_seconds.toFixed(3)} seconds.</p>}
    {reference?.status !== "ready" && <p>Retrieve a ready reference in Reference to inspect its frames. Saved-caption preview can still work independently.</p>}
    <button disabled={busy || disabled || !validTime || reference?.status !== "ready"} onClick={() => void run("reference")}>Inspect reference frame</button><button disabled={busy} onClick={() => void run("status")}>Reload reference status</button>
    <label>Saved cue <select value={cue} disabled={!track.cues.length} onChange={e => { cancel(); setCue(Number(e.target.value)); setError(""); }}>{track.cues.map((c, i) => <option key={i} value={i}>Cue {i + 1}: {c.start}–{c.end}s · {c.text.slice(0, 40)}</option>)}</select></label>
    <p>{fontLabel(track.font_binding)}</p>
    {!ready || !track.enabled || !track.cues.length ? <p>Enable and save valid captions before previewing.</p> : !context.recipeReady ? <p>Generate or regenerate a saved color recipe before previewing captions.</p> : null}
    {unsaved && <p role="status">Save your unsaved caption, color, framing or cut-plan changes before updating the caption preview. {context.recipeDirty && <button onClick={() => navigation?.open("style")}>Open Style & cuts</button>} {context.framing.dirty && <button onClick={() => navigation?.open("export")}>Open Export</button>}</p>}
    <button disabled={!canPreview || busy} onClick={() => void run("caption")}>Update caption preview</button>
    <p className="hint">Saved cue at a visible 30 fps output frame, using actual export font, color, framing and subtitle rendering. Stills may downscale to a 960-pixel edge; they do not preview encoded-video quality.</p>
    {busy && <p role="status">Updating comparison… Previous images remain visible. Frame processing is capped at 30 seconds.</p>}{error && <p role="alert">{error}</p>}
    <div className="frame-preview-images">
      <figure><figcaption>Retained reference frame</figcaption>{referenceFrame ? <><Image unoptimized src={`data:image/png;base64,${referenceFrame.image.png_base64}`} width={referenceFrame.image.width} height={referenceFrame.image.height} alt={`Retained reference frame at ${referenceFrame.timestamp_seconds.toFixed(3)} seconds`} /><p>{referenceFrame.reference_operation_id !== reference?.operation_id || referenceFrame.source.media_sha256 !== reference?.media?.sha256 ? "Outdated reference frame · " : ""}Source {referenceFrame.timestamp_seconds.toFixed(3)}s</p></> : <p>Inspect a frame explicitly.</p>}</figure>
      <figure><figcaption>Saved caption preview</figcaption>{captionFrame ? <><Image unoptimized src={`data:image/png;base64,${captionFrame.image.png_base64}`} width={captionFrame.image.width} height={captionFrame.image.height} alt={`Actual saved caption preview, cue ${captionFrame.cue_index + 1}, output ${captionFrame.output_timestamp_seconds.toFixed(3)} seconds`} /><p role="status">{outdated ? "Outdated caption preview" : "Caption preview"} · Cue {captionFrame.cue_index + 1} · Output {captionFrame.output_timestamp_seconds.toFixed(3)}s · Footage source {captionFrame.source_timestamp_seconds.toFixed(3)}s</p><p>Caption revision {captionFrame.caption_revision} · Recipe {captionFrame.recipe_revision} · Framing {captionFrame.framing_revision}{captionFrame.plan_revision !== null ? ` · Cut plan ${captionFrame.plan_revision}` : " · Whole clip"}</p><p>{fontLabel(captionFrame.font)} · Font hash {captionFrame.font.sha256.slice(0, 12)}</p></> : <p>Save a cue and update explicitly.</p>}</figure>
    </div>
    {[...new Set([...(referenceFrame?.warnings ?? []), ...(captionFrame?.warnings ?? [])])].map(w => <p role="note" key={w}>{w}</p>)}
  </section>;
}
