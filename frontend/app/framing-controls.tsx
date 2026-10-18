"use client";

import Image from "next/image";
import type { GradingState } from "./grading-controls";
import { useWorkspaceNavigation, useWorkspaceReport } from "./guided-workspace";
import { useCallback, useEffect, useRef, useState } from "react";

export const formatLabels = { original: "Original", reference: "Reference aspect", portrait: "Portrait", square: "Square", landscape: "Landscape" };
type Format = keyof typeof formatLabels;
type SlotChoices = { fit: "automatic" | "fit" | "fill"; horizontal: number; vertical: number; zoom: number; bars: "auto" | "off"; insets?: [number, number, number, number] | null };
type Choices = { format: Format; fit: "fit" | "fill" | "automatic"; horizontal: number; vertical: number; bars?: "auto" | "off"; slots?: Record<string, SlotChoices> };
const automaticSlot: SlotChoices = { fit: "automatic", horizontal: .5, vertical: .5, zoom: 1, bars: "auto" };
type Picture = { width: number; height: number; png_base64: string };
type SlotPreview = { edited: Picture; framing_note?: string; framing_revision: number; grading_revision?: number; slot_id?: string; inspected?: string };
type Settings = Choices & { schema_version: 1; revision: number; canvas?: [number, number] | null; decisions?: Record<string, { insets: number[]; note: string }> };
type Result = { status: "default" | "ready"; settings: Settings };
type Geometry = { display_width: number; display_height: number; original_width: number; original_height: number };
export type FramingState = { ready: boolean; dirty: boolean; busy: boolean; revision: number | null; format?: Format; dimensions?: string; fit?: "fit" | "fill" | "automatic" };
const defaults: Choices = { format: "original", fit: "fit", horizontal: .5, vertical: .5 };
const sizes = { portrait: [720, 1280], square: [720, 720], landscape: [1280, 720] } as const;
const apiBase = (process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
function parse(data: unknown): Result {
  const r = data as Result, s = r?.settings;
  if (!r || !["default", "ready"].includes(r.status) || !s || s.schema_version !== 1 || !Number.isSafeInteger(s.revision) || s.revision < 0 || !Object.hasOwn(formatLabels, s.format) || !["fit", "fill", "automatic"].includes(s.fit) || ![s.horizontal, s.vertical].every(n => typeof n === "number" && Number.isFinite(n) && n >= 0 && n <= 1)) throw new Error("Invalid framing response.");
  return r;
}
async function request(projectId: string, signal: AbortSignal, body?: unknown, geometry = false, clipId?: string) {
  const controller = new AbortController(), abort = () => controller.abort();
  signal.addEventListener("abort", abort);
  if (signal.aborted) abort();
  const timer = setTimeout(abort, 125000);
  try {
    const response = await fetch(`${apiBase}/api/projects/${encodeURIComponent(projectId)}/framing${geometry ? `/source${clipId ? `?clip_id=${encodeURIComponent(clipId)}` : ""}` : ""}`, { method: body ? "POST" : "GET", signal: controller.signal, ...(body ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}) });
    const data = await response.json();
    if (!response.ok) throw new Error(data?.error?.message || "Framing request failed.");
    return data;
  } finally { clearTimeout(timer); signal.removeEventListener("abort", abort); }
}
function dimensions(choices: Choices, geometry: Geometry | null) {
  const size = choices.format === "original" ? geometry && [geometry.original_width, geometry.original_height] : choices.format === "reference" ? (choices as Settings).canvas : sizes[choices.format];
  return size ? `${size[0]} × ${size[1]}` : undefined;
}

const editable = (s: Settings): Choices => ({ format: s.format, fit: s.fit, horizontal: s.horizontal, vertical: s.vertical, bars: s.bars ?? "off", slots: s.slots ?? {} });

export function FramingControls({ projectId, onState, grading }: { projectId: string; onState: (state: FramingState) => void; grading?: GradingState }) {
  const navigation = useWorkspaceNavigation();
  const [selection, setSelection] = useState({ key: "", slot: "" });
  const selectedId = navigation?.selection?.kind === "slot" ? navigation.selection.id : null;
  const navigationKey = selectedId ? `slot:${selectedId}` : "";
  const choiceKey = selection.slot === navigationKey ? selection.key : "";
  const choice = grading?.choices.find(c => c.key === (choiceKey || navigationKey)) ?? grading?.choices[0];
  const [preview, setPreview] = useState<SlotPreview | null>(null), [previewBusy, setPreviewBusy] = useState(false);
  const previewAction = useRef<AbortController | null>(null);
  const [result, setResult] = useState<Result | null>(null), [draft, setDraft] = useState(defaults);
  const [geometry, setGeometry] = useState<Geometry | null>(null), [geometryError, setGeometryError] = useState("");
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const action = useRef<AbortController | null>(null);
  const geometryAction = useRef<AbortController | null>(null);
  const [inspecting, setInspecting] = useState(false);
  const dirty = !!result && JSON.stringify(draft) !== JSON.stringify(editable(result.settings));
  const slot = choice?.slotId ? draft.slots?.[choice.slotId] ?? { ...automaticSlot, fit: draft.fit, bars: draft.bars ?? "off", horizontal: draft.horizontal, vertical: draft.vertical } : null;
  function changeSlot(changes: Partial<SlotChoices>) { if (choice?.slotId && slot) setDraft({ ...draft, slots: { ...draft.slots, [choice.slotId]: { ...slot, ...changes } } }); }
  async function updatePreview() {
    if (!grading || !choice || dirty || previewAction.current || !result) return;
    const controller = new AbortController(); previewAction.current = controller; setPreviewBusy(true); setError("");
    const timer = setTimeout(() => controller.abort(), 35000);
    try {
      const response = await fetch(`${apiBase}/api/projects/${encodeURIComponent(projectId)}/frame-preview`, { method: "POST", signal: controller.signal, headers: { "Content-Type": "application/json" }, body: JSON.stringify({ expected_recipe_revision: 0, expected_grading_revision: grading.revision, expected_framing_revision: result.settings.revision, timestamp_seconds: (choice.start + choice.end) / 2, clip_id: choice.clipId, slot_id: choice.slotId, expected_sequence_revision: grading.sequenceRevision }) });
      const data = await response.json(); if (!response.ok) throw new Error(data?.error?.message || "Framing preview failed.");
      const im = data.edited;
      if (data.framing_revision !== result.settings.revision || !im || ![im.width, im.height].every(n => Number.isInteger(n) && n >= 2 && n <= 960) || typeof im.png_base64 !== "string" || im.png_base64.length > 4194304 || !/^iVBORw0KGgo[A-Za-z0-9+/]*={0,2}$/.test(im.png_base64)) throw new Error("Invalid framing preview.");
      if (!controller.signal.aborted) setPreview({ ...data, inspected: choice.version });
    } catch (cause) { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "Preview failed."); }
    finally { clearTimeout(timer); if (previewAction.current === controller) { previewAction.current = null; setPreviewBusy(false); } }
  }
  useEffect(() => () => { previewAction.current?.abort(); previewAction.current = null; setPreviewBusy(false); setPreview(null); }, [projectId, choice?.key, choice?.version, result?.settings.revision, grading?.revision]);
  useEffect(() => { onState({ ready: !!result, dirty, busy, revision: result?.settings.revision ?? null, format: result?.settings.format, fit: result?.settings.fit, dimensions: result ? dimensions(result.settings, geometry) : undefined }); }, [result, dirty, busy, geometry, onState]);
  const selectedClipId = choice?.clipId;
  const loadGeometry = useCallback(async (signal: AbortSignal) => {
    try {
      const g = await request(projectId, signal, undefined, true, selectedClipId) as Geometry;
      if (![g?.display_width, g?.display_height, g?.original_width, g?.original_height].every(n => Number.isFinite(n) && n > 0)) throw new Error("Invalid footage geometry.");
      if (!signal.aborted) { setGeometry(g); setGeometryError(""); }
    } catch (cause) { if (!signal.aborted) setGeometryError(cause instanceof Error ? cause.message : "Footage framing could not be inspected."); }
  }, [projectId, selectedClipId]);
  useEffect(() => {
    const controller = new AbortController();
    void request(projectId, controller.signal).then(data => { if (!controller.signal.aborted) { const r = parse(data); setResult(r); setDraft(editable(r.settings)); } }).catch(cause => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "Framing could not be loaded."); });
    return () => { controller.abort(); action.current?.abort(); geometryAction.current?.abort(); };
  }, [projectId]);
  useEffect(() => { const controller = new AbortController(); void Promise.resolve().then(() => loadGeometry(controller.signal)); return () => controller.abort(); }, [loadGeometry]);
  async function run(save: boolean) {
    if (action.current) return;
    const controller = new AbortController(); action.current = controller; setBusy(true); setError("");
    try {
      const r = parse(await request(projectId, controller.signal, save ? { ...draft, expected_revision: result?.settings.revision } : undefined));
      if (!controller.signal.aborted) { setResult(r); setDraft(editable(r.settings)); }
    } catch (cause) { if (!controller.signal.aborted) setError(cause instanceof Error && cause.name !== "AbortError" ? cause.message : "Request timed out. Reload saved framing before retrying."); }
    finally { if (action.current === controller) { action.current = null; if (!controller.signal.aborted) setBusy(false); } }
  }
  async function inspect() {
    if (geometryAction.current) return;
    const controller = new AbortController(); geometryAction.current = controller; setInspecting(true);
    try { await loadGeometry(controller.signal); }
    finally { if (geometryAction.current === controller) { geometryAction.current = null; if (!controller.signal.aborted) setInspecting(false); } }
  }
  const fixed = draft.format !== "original";
  const canvas = draft.format === "reference" ? result?.settings.canvas : fixed ? sizes[draft.format as keyof typeof sizes] : null;
  const factor = geometry && canvas ? Math.max(canvas[0] / geometry.display_width, canvas[1] / geometry.display_height) : 0;
  const movement = geometry && canvas ? [Math.ceil(geometry.display_width * factor / 2 - 1e-9) * 2 - canvas[0], Math.ceil(geometry.display_height * factor / 2 - 1e-9) * 2 - canvas[1]] : [0, 0];
  const selectedInsets = slot?.insets ?? (slot?.bars === "auto" && choice?.slotId ? result?.settings.decisions?.[choice.slotId]?.insets : null) ?? [0,0,0,0];
  const activeSize = geometry ? [geometry.display_width * (1-selectedInsets[0]-selectedInsets[2]), geometry.display_height * (1-selectedInsets[1]-selectedInsets[3])] : null;
  const retained = canvas && activeSize ? Math.min((canvas[0]/canvas[1])/(activeSize[0]/activeSize[1]), (activeSize[0]/activeSize[1])/(canvas[0]/canvas[1])) : 0;
  const slotFill = slot?.fit === "fill" || (slot?.fit === "automatic" && retained >= .72);
  const slotFactor = canvas && activeSize ? Math.max(canvas[0]/activeSize[0],canvas[1]/activeSize[1]) * (slot?.zoom ?? 1) : 0;
  const slotMovement = canvas && activeSize ? activeSize.map((n,i) => Math.ceil(n*slotFactor/2-1e-9)*2-canvas[i]) : [0,0];
  useWorkspaceReport("framing-settings", "framing", busy ? "Working" : error ? "Needs attention" : !!result ? dirty ? "Needs input" : "Ready" : "Needs input", "Saving or loading framing…", dirty);
  return <section className="reference-section" aria-label="Output framing">
    <h3>Output framing</h3>
    <p className="hint">Fit keeps the whole image with black padding. Fill can remove content at the edges. Fixed formats may upscale small footage; Original keeps the existing size limit without upscaling. Preview uses the saved slot settings below. Automatic uses a stable center crop and chooses Fit when Fill would remove over 28% of the active image. Black padding is consistent; its thickness can differ.</p>
    {!result && !error && <p role="status">Loading framing…</p>}
    {error && <p role="alert">{error}</p>}
    {result && <>
      <p role="status">{dirty ? "Unsaved framing changes" : "Framing saved"} · Revision {result.settings.revision} · {formatLabels[result.settings.format]}{dimensions(result.settings, geometry) ? ` · ${dimensions(result.settings, geometry)}` : ""}</p>
      <fieldset className="recipe-controls" disabled={busy}>
        <legend>Whole project: canvas and default framing</legend>
        <label>Output format <select value={draft.format} onChange={e => setDraft({ ...draft, format: e.target.value as Format })}>{Object.entries(formatLabels).map(([value, label]) => <option key={value} value={value}>{label}{value in sizes ? ` · ${sizes[value as keyof typeof sizes].join(" × ")}` : ""}</option>)}</select></label>
        {fixed && <label>How footage fits <select value={draft.fit} onChange={e => setDraft({ ...draft, fit: e.target.value as Choices["fit"] })}><option value="automatic">Automatic (conservative)</option><option value="fit">Fit (whole image)</option><option value="fill">Fill (crop edges)</option></select></label>}
        {fixed && draft.fit === "fill" && <>
          <label>Horizontal crop position: {Math.round(draft.horizontal * 100)}% (left to right)<input aria-label="Horizontal crop position" type="range" min="0" max="100" step="1" value={draft.horizontal * 100} disabled={movement[0] < 2} onChange={e => setDraft({ ...draft, horizontal: Number(e.target.value) / 100 })} /></label>
          <label>Vertical crop position: {Math.round(draft.vertical * 100)}% (top to bottom)<input aria-label="Vertical crop position" type="range" min="0" max="100" step="1" value={draft.vertical * 100} disabled={movement[1] < 2} onChange={e => setDraft({ ...draft, vertical: Number(e.target.value) / 100 })} /></label>
          <p className="hint">An axis is disabled when there is no crop movement, or footage geometry is unavailable.</p>
        </>}
        <label>Default embedded-bar removal <select value={draft.bars ?? "off"} onChange={e => setDraft({ ...draft, bars: e.target.value as "auto" | "off" })}><option value="auto">Auto (confident borders only)</option><option value="off">Off</option></select></label>
        <p>Draft canvas: {dimensions({ ...draft, canvas: result.settings.canvas } as Settings, geometry) || (draft.format === "reference" ? "Save to resolve the retained reference aspect" : "Inspect footage to see Original dimensions")}</p>
        <button className="button-primary" disabled={!dirty} onClick={() => void run(true)}>Save framing</button>
        <button onClick={() => setDraft({ ...defaults, bars: "off", slots: {} })}>Reset framing</button>
      </fieldset>
      {grading?.choices.length ? <fieldset className="recipe-controls" disabled={busy || previewBusy}>
        <legend>Selected slot only</legend>
        <label>Slot to frame <select value={choice?.key ?? ""} onChange={e => setSelection({ key: e.target.value, slot: navigationKey })}>{grading.choices.map(c => <option key={c.key} value={c.key}>{c.name}</option>)}</select></label>
        {slot && <>
          <label>Slot framing <select value={slot.fit} onChange={e => changeSlot({ fit: e.target.value as SlotChoices["fit"] })}><option value="automatic">Automatic</option><option value="fill">Fill</option><option value="fit">Fit</option></select></label>
          <label>Embedded-bar removal <select value={slot.bars} onChange={e => changeSlot({ bars: e.target.value as SlotChoices["bars"] })}><option value="auto">Auto</option><option value="off">Off</option></select></label>
          {(["horizontal", "vertical"] as const).map((axis, i) => <label key={axis}>{axis === "horizontal" ? "Left / right" : "Top / bottom"} crop position: {Math.round(slot[axis]*100)}%<input aria-label={`Slot ${axis} position`} type="range" min="0" max="100" value={slot[axis]*100} disabled={!slotFill || slotMovement[i] < 2} onChange={e => changeSlot({ [axis]: Number(e.target.value)/100 })} /></label>)}
          <label>Slot zoom: {slot.zoom.toFixed(2)}×<input aria-label="Slot zoom" type="range" min="1" max="2" step="0.01" disabled={!slotFill} value={slot.zoom} onChange={e => changeSlot({ zoom: Number(e.target.value) })} /></label>
          <details><summary>Correct embedded borders manually</summary><p>Crop fractions apply after orientation. Inspect the whole image first; burned-in words cannot move independently.</p>{(["Left", "Top", "Right", "Bottom"] as const).map((label, i) => <label key={label}>{label} trim (%)<input aria-label={`${label} border trim`} type="number" min="0" max="45" step="0.5" value={(slot.insets?.[i] ?? 0)*100} onChange={e => { const insets = [...(slot.insets ?? [0,0,0,0])] as [number,number,number,number]; insets[i] = Number(e.target.value)/100; changeSlot({ insets }); }} /></label>)}</details>
          <button onClick={() => { changeSlot({ ...automaticSlot, insets: null }); }}>Reset selected slot to automatic</button>
          <p>{result.settings.decisions?.[choice!.slotId!]?.note}</p>
        </>}
        {dirty && <p role="status">Preview pending: save framing changes, then update preview. No old preview represents this draft.</p>}
        <button disabled={dirty || !grading.ready || grading.dirty || grading.busy || grading.mode === "basic"} onClick={() => void updatePreview()}>{previewBusy ? "Updating framing preview×" : "Update saved framing preview"}</button>
        {grading.mode === "basic" && <p>Use the existing before/after preview in Color for Basic recipe mode.</p>}
        {!dirty && preview && preview.framing_revision === result.settings.revision && preview.grading_revision === grading.revision && preview.slot_id === (choice?.slotId ?? null) && preview.inspected === choice?.version && <figure><Image unoptimized src={`data:image/png;base64,${preview.edited.png_base64}`} width={preview.edited.width} height={preview.edited.height} alt="Actual selected footage with saved framing and color, before final captions" style={{ maxWidth: "100%", height: "auto" }} /><figcaption>{preview.framing_note} Saved revision {preview.framing_revision}. Final captions remain on the canvas.</figcaption></figure>}
      </fieldset> : null}
      <p className="hint">Reset changes the draft to Original. Save to persist it. Captions stay on the final canvas; audio and cut timing stay unchanged.</p>
    </>}
    {geometryError && <><p role="note">Framing dimensions need an explicit inspection after upload or a busy operation. Saved Original/Fit settings can still render; inspect before positioning a Fill crop.</p><details><summary>Framing inspection details</summary><p>{geometryError}</p></details></>}
    <button disabled={busy || inspecting} onClick={() => void inspect()}>Inspect footage framing</button>
    <button disabled={busy} onClick={() => void run(false)}>{dirty ? "Discard framing changes and reload" : "Reload saved framing"}</button>
  </section>;
}
