"use client";

import { useWorkspaceReport } from "./guided-workspace";
import { useCallback, useEffect, useRef, useState } from "react";

export const formatLabels = { original: "Original", portrait: "Portrait", square: "Square", landscape: "Landscape" };
type Format = keyof typeof formatLabels;
type Choices = { format: Format; fit: "fit" | "fill"; horizontal: number; vertical: number };
type Settings = Choices & { schema_version: 1; revision: number };
type Result = { status: "default" | "ready"; settings: Settings };
type Geometry = { display_width: number; display_height: number; original_width: number; original_height: number };
export type FramingState = { ready: boolean; dirty: boolean; busy: boolean; revision: number | null; format?: Format; dimensions?: string; fit?: "fit" | "fill" };
const defaults: Choices = { format: "original", fit: "fit", horizontal: .5, vertical: .5 };
const sizes = { portrait: [720, 1280], square: [720, 720], landscape: [1280, 720] } as const;
const apiBase = (process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
function parse(data: unknown): Result {
  const r = data as Result, s = r?.settings;
  if (!r || !["default", "ready"].includes(r.status) || !s || s.schema_version !== 1 || !Number.isSafeInteger(s.revision) || s.revision < 0 || !Object.hasOwn(formatLabels, s.format) || !["fit", "fill"].includes(s.fit) || ![s.horizontal, s.vertical].every(n => typeof n === "number" && Number.isFinite(n) && n >= 0 && n <= 1)) throw new Error("Invalid framing response.");
  return r;
}
async function request(projectId: string, signal: AbortSignal, body?: unknown, geometry = false) {
  const controller = new AbortController(), abort = () => controller.abort();
  signal.addEventListener("abort", abort);
  if (signal.aborted) abort();
  const timer = setTimeout(abort, 25000);
  try {
    const response = await fetch(`${apiBase}/api/projects/${encodeURIComponent(projectId)}/framing${geometry ? "/source" : ""}`, { method: body ? "POST" : "GET", signal: controller.signal, ...(body ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}) });
    const data = await response.json();
    if (!response.ok) throw new Error(data?.error?.message || "Framing request failed.");
    return data;
  } finally { clearTimeout(timer); signal.removeEventListener("abort", abort); }
}
function dimensions(choices: Choices, geometry: Geometry | null) {
  const size = choices.format === "original" ? geometry && [geometry.original_width, geometry.original_height] : sizes[choices.format];
  return size ? `${size[0]} × ${size[1]}` : undefined;
}

export function FramingControls({ projectId, onState }: { projectId: string; onState: (state: FramingState) => void }) {
  const [result, setResult] = useState<Result | null>(null), [draft, setDraft] = useState(defaults);
  const [geometry, setGeometry] = useState<Geometry | null>(null), [geometryError, setGeometryError] = useState("");
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const action = useRef<AbortController | null>(null);
  const geometryAction = useRef<AbortController | null>(null);
  const [inspecting, setInspecting] = useState(false);
  const dirty = !!result && (Object.keys(defaults) as (keyof Choices)[]).some(key => draft[key] !== result.settings[key]);
  useEffect(() => { onState({ ready: !!result, dirty, busy, revision: result?.settings.revision ?? null, format: result?.settings.format, fit: result?.settings.fit, dimensions: result ? dimensions(result.settings, geometry) : undefined }); }, [result, dirty, busy, geometry, onState]);
  const loadGeometry = useCallback(async (signal: AbortSignal) => {
    try {
      const g = await request(projectId, signal, undefined, true) as Geometry;
      if (![g?.display_width, g?.display_height, g?.original_width, g?.original_height].every(n => Number.isFinite(n) && n > 0)) throw new Error("Invalid footage geometry.");
      if (!signal.aborted) { setGeometry(g); setGeometryError(""); }
    } catch (cause) { if (!signal.aborted) setGeometryError(cause instanceof Error ? cause.message : "Footage framing could not be inspected."); }
  }, [projectId]);
  useEffect(() => {
    const controller = new AbortController();
    void request(projectId, controller.signal).then(data => { if (!controller.signal.aborted) { const r = parse(data); setResult(r); setDraft({ format: r.settings.format, fit: r.settings.fit, horizontal: r.settings.horizontal, vertical: r.settings.vertical }); } }).catch(cause => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "Framing could not be loaded."); });
    void Promise.resolve().then(() => loadGeometry(controller.signal));
    return () => { controller.abort(); action.current?.abort(); geometryAction.current?.abort(); };
  }, [projectId, loadGeometry]);
  async function run(save: boolean) {
    if (action.current) return;
    const controller = new AbortController(); action.current = controller; setBusy(true); setError("");
    try {
      const r = parse(await request(projectId, controller.signal, save ? { ...draft, expected_revision: result?.settings.revision } : undefined));
      if (!controller.signal.aborted) { setResult(r); setDraft({ format: r.settings.format, fit: r.settings.fit, horizontal: r.settings.horizontal, vertical: r.settings.vertical }); }
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
  const canvas = fixed ? sizes[draft.format as keyof typeof sizes] : null;
  const factor = geometry && canvas ? Math.max(canvas[0] / geometry.display_width, canvas[1] / geometry.display_height) : 0;
  const movement = geometry && canvas ? [Math.ceil(geometry.display_width * factor / 2 - 1e-9) * 2 - canvas[0], Math.ceil(geometry.display_height * factor / 2 - 1e-9) * 2 - canvas[1]] : [0, 0];
  useWorkspaceReport("framing-settings", "export", busy ? "Working" : error ? "Needs attention" : !!result ? dirty ? "Needs input" : "Ready" : "Needs input", "Saving or loading framing…");
  return <section className="reference-section" aria-label="Output framing">
    <h3>Output framing</h3>
    <p className="hint">Fit keeps the whole image with black padding. Fill can remove content at the edges. Fixed formats may upscale small footage; Original keeps the existing size limit without upscaling. No live crop preview.</p>
    {!result && !error && <p role="status">Loading framing…</p>}
    {error && <p role="alert">{error}</p>}
    {result && <>
      <p role="status">{dirty ? "Unsaved framing changes" : "Framing saved"} · Revision {result.settings.revision} · {formatLabels[result.settings.format]}{dimensions(result.settings, geometry) ? ` · ${dimensions(result.settings, geometry)}` : ""}</p>
      <fieldset className="recipe-controls" disabled={busy}>
        <legend>Canvas and fit</legend>
        <label>Output format <select value={draft.format} onChange={e => setDraft({ ...draft, format: e.target.value as Format })}>{Object.entries(formatLabels).map(([value, label]) => <option key={value} value={value}>{label}{value in sizes ? ` · ${sizes[value as keyof typeof sizes].join(" × ")}` : ""}</option>)}</select></label>
        {fixed && <label>How footage fits <select value={draft.fit} onChange={e => setDraft({ ...draft, fit: e.target.value as Choices["fit"] })}><option value="fit">Fit (whole image)</option><option value="fill">Fill (crop edges)</option></select></label>}
        {fixed && draft.fit === "fill" && <>
          <label>Horizontal crop position: {Math.round(draft.horizontal * 100)}% (left to right)<input aria-label="Horizontal crop position" type="range" min="0" max="100" step="1" value={draft.horizontal * 100} disabled={movement[0] < 2} onChange={e => setDraft({ ...draft, horizontal: Number(e.target.value) / 100 })} /></label>
          <label>Vertical crop position: {Math.round(draft.vertical * 100)}% (top to bottom)<input aria-label="Vertical crop position" type="range" min="0" max="100" step="1" value={draft.vertical * 100} disabled={movement[1] < 2} onChange={e => setDraft({ ...draft, vertical: Number(e.target.value) / 100 })} /></label>
          <p className="hint">An axis is disabled when there is no crop movement, or footage geometry is unavailable.</p>
        </>}
        <p>Draft canvas: {dimensions(draft, geometry) || "Inspect footage to see Original dimensions"}</p>
        <button disabled={!dirty} onClick={() => void run(true)}>Save framing</button>
        <button onClick={() => setDraft({ ...defaults })}>Reset framing</button>
      </fieldset>
      <p className="hint">Reset changes the draft to Original. Save to persist it. Captions stay on the final canvas; audio and cut timing stay unchanged.</p>
    </>}
    {geometryError && <p role="alert">{geometryError}</p>}
    <button disabled={busy || inspecting} onClick={() => void inspect()}>Inspect footage framing</button>
    <button disabled={busy} onClick={() => void run(false)}>{dirty ? "Discard framing changes and reload" : "Reload saved framing"}</button>
  </section>;
}
