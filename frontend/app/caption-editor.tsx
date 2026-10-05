"use client";

import { useWorkspaceReport } from "./guided-workspace";
import { useEffect, useRef, useState } from "react";
import { Application, TranscriptionReview } from "./transcription-review";
import { PlanState } from "./edit-plan";

type Mode = "whole" | "cuts";
type Cue = { start: number; end: number; text: string };
type DraftCue = { start: string; end: string; text: string };
type Style = { color: "white" | "yellow"; size: "small" | "medium" | "large"; placement: "bottom-center" | "center" };
type Track = { schema_version: 1; revision: number; enabled: boolean; cues: Cue[]; style: Style; provenance: "manual" | "srt_import" | "automatic_transcription";
  automatic_proposal_id?: string | null; timeline: { mode: Mode; plan_revision: number | null; duration_seconds: number } | null };
type Result = { status: "default" | "ready" | "stale"; track: Track; message: string | null; whole_duration_seconds: number | null };
export type CaptionState = { revision: number | null; ready: boolean; dirty: boolean; busy: boolean; enabled: boolean; mode: Mode | null; planRevision: number | null };
const apiBase = (process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
const defaults: Style = { color: "white", size: "medium", placement: "bottom-center" };
const finite = (v: unknown) => typeof v === "number" && Number.isFinite(v) && v >= 0 && v <= 120;
function validCues(cues: Cue[]) {
  return Array.isArray(cues) && cues.length <= 200 && cues.every((c, i) => finite(c.start) && finite(c.end) && c.start < c.end &&
    typeof c.text === "string" && !!c.text.trim() && Array.from(c.text).length <= 200 && c.text.split("\n").length <= 2 &&
    (!i || c.start >= cues[i - 1].end));
}
function parse(data: unknown): Result {
  const r = data as Result, t = r?.track;
  if (!r || !["default", "ready", "stale"].includes(r.status) || !t || t.schema_version !== 1 ||
    !Number.isSafeInteger(t.revision) || t.revision < 0 || typeof t.enabled !== "boolean" || !validCues(t.cues) ||
    !["manual", "srt_import", "automatic_transcription"].includes(t.provenance) || (t.provenance === "automatic_transcription" && typeof t.automatic_proposal_id !== "string") || !t.style || !["white", "yellow"].includes(t.style.color) ||
    !["small", "medium", "large"].includes(t.style.size) || !["bottom-center", "center"].includes(t.style.placement) ||
    (t.revision > 0 && (!t.timeline || !["whole", "cuts"].includes(t.timeline.mode) || !finite(t.timeline.duration_seconds))) ||
    (r.whole_duration_seconds !== null && !finite(r.whole_duration_seconds))) throw new Error("Invalid caption response.");
  return r;
}
async function request(projectId: string, signal: AbortSignal, body?: unknown, file?: File): Promise<unknown> {
  const controller = new AbortController(), abort = () => controller.abort();
  signal.addEventListener("abort", abort, { once: true });
  if (signal.aborted) controller.abort();
  const timer = setTimeout(abort, 10000);
  try {
    const response = await fetch(`${apiBase}/api/projects/${encodeURIComponent(projectId)}/captions${file ? "/import" : ""}`, {
      method: body || file ? "POST" : "GET", signal: controller.signal,
      ...(file ? { headers: { "Content-Type": "text/plain; charset=utf-8" }, body: file } : body ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}),
    });
    const data = await response.json();
    if (!response.ok) throw Object.assign(new Error(data?.error?.message || "Caption request failed."), { code: data?.error?.code });
    return data;
  } finally { clearTimeout(timer); signal.removeEventListener("abort", abort); }
}
const drafts = (cues: Cue[]): DraftCue[] => cues.map(c => ({ ...c, start: String(c.start), end: String(c.end) }));
export function CaptionEditor({ projectId, planState, onState }: { projectId: string; planState: PlanState; onState: (state: CaptionState) => void }) {
  const [result, setResult] = useState<Result | null>(null);
  const [cues, setCues] = useState<DraftCue[]>([]);
  const [enabled, setEnabled] = useState(false);
  const [style, setStyle] = useState(defaults);
  const [mode, setMode] = useState<Mode>("whole");
  const [provenance, setProvenance] = useState<"manual" | "srt_import" | "automatic_transcription">("manual");
  const [automaticId, setAutomaticId] = useState<string | null>(null);
  const [proposalBusy, setProposalBusy] = useState(false);
  const [preview, setPreview] = useState<Cue[] | null>(null);
  const [confirmation, setConfirmation] = useState<string | null>(null);
  const confirmationTarget = `${mode}:${mode === "cuts" ? planState.revision : 0}`;
  const confirm = confirmation === confirmationTarget;
  function setConfirm(value: boolean) { setConfirmation(value ? confirmationTarget : null); }
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const action = useRef<AbortController | null>(null);
  const track = result?.track;
  const numeric = cues.map(c => ({ ...c, start: Number(c.start), end: Number(c.end) }));
  const dirty = !!track && (enabled !== track.enabled || mode !== (track.timeline?.mode ?? "whole") || provenance !== track.provenance ||
    JSON.stringify(style) !== JSON.stringify(track.style) || JSON.stringify(numeric) !== JSON.stringify(track.cues) || cues.some(c => !c.start.trim() || !c.end.trim()));
  const revision = track?.revision ?? null;
  const boundMode = track?.timeline?.mode ?? null, boundPlan = track?.timeline?.plan_revision ?? null;
  const ready = !!track && (!track.enabled || (result?.status !== "stale" && (boundMode !== "cuts" || (planState.ready && planState.revision === boundPlan))));
  const savedEnabled = track?.enabled ?? false;
  useEffect(() => { onState({ revision, ready, dirty, busy: busy || proposalBusy, enabled: savedEnabled, mode: boundMode, planRevision: boundPlan }); }, [onState, revision, ready, dirty, busy, proposalBusy, savedEnabled, boundMode, boundPlan]);
  const disablingAutomatic = provenance === "automatic_transcription" && !enabled && !!track?.revision;
  const duration = disablingAutomatic ? track?.timeline?.duration_seconds ?? null : mode === "cuts" ? planState.duration ?? null : result?.whole_duration_seconds ?? null;
  const needsRebind = !disablingAutomatic && !!track?.revision && (result?.status === "stale" || mode !== boundMode || (mode === "cuts" && boundPlan !== planState.revision));
  const invalidIndex = numeric.findIndex((c, i) => !cues[i].start.trim() || !cues[i].end.trim() || !validCues([c]) ||
    (i > 0 && c.start < numeric[i - 1].end) || (duration !== null && c.end > duration));
  const canSave = !!result && invalidIndex < 0 && (!enabled || cues.length > 0) && duration !== null &&
    (disablingAutomatic || mode !== "cuts" || (planState.ready && !planState.dirty && !planState.busy));
  function restore(value: Result) {
    setResult(value); setCues(drafts(value.track.cues)); setEnabled(value.track.enabled); setStyle(value.track.style);
    setMode(value.track.timeline?.mode ?? "whole"); setProvenance(value.track.provenance); setAutomaticId(value.track.automatic_proposal_id ?? null); setPreview(null); setConfirmation(null); setError("");
  }
  useEffect(() => {
    const controller = new AbortController();
    void request(projectId, controller.signal).then(data => { if (!controller.signal.aborted) restore(parse(data)); })
      .catch(cause => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "Captions could not be loaded."); });
    return () => { controller.abort(); action.current?.abort(); };
  }, [projectId]);
  async function run(kind: "save" | "reload" | "import", file?: File) {
    if (action.current) return;
    if (file && file.size > 128 * 1024) { setError("SRT import is limited to 128 KiB UTF-8."); return; }
    const controller = new AbortController(); action.current = controller; setBusy(true); setError("");
    try {
      const data = await request(projectId, controller.signal, kind === "save" ? {
        expected_revision: revision, mode, expected_plan_revision: mode === "cuts" ? planState.revision : null,
        confirm_rebind: confirm, enabled, style, provenance, cues: numeric,
        ...(provenance === "automatic_transcription" ? { automatic_proposal_id: automaticId } : {}),
      } : undefined, file);
      if (!controller.signal.aborted) {
        if (kind === "import") { const imported = data as { cues: Cue[] }; if (!validCues(imported.cues) || !imported.cues.length) throw new Error("Invalid SRT preview."); setPreview(imported.cues); }
        else restore(parse(data));
      }
    } catch (cause) {
      if (!controller.signal.aborted) {
        setError(cause instanceof Error && cause.name !== "AbortError" ? cause.message : "Request timed out. Reload explicitly; your draft is retained.");
        if (cause instanceof Error && "code" in cause && cause.code === "caption_rebind_required") setConfirm(true);
      }
    } finally { if (action.current === controller) { action.current = null; if (!controller.signal.aborted) setBusy(false); } }
  }
  function edit(index: number, key: keyof DraftCue, value: string) { setCues(all => all.map((c, i) => i === index ? { ...c, [key]: value } : c)); setConfirm(false); }
  useWorkspaceReport("caption-settings", "audio", busy || proposalBusy ? "Working" : error || (!!result && !ready) ? "Needs attention" : ready ? dirty ? "Needs input" : "Ready" : "Needs input", "Saving or loading caption…");
  return <section className="reference-section" aria-label="Captions">
    <h3>Captions</h3>
    <p className="hint">Manual text, imported SRT, or reviewed automatic transcription. No reference-caption extraction. Times use the final output timeline after cuts: start inclusive, end exclusive. Saving does not create a video preview.</p>
    {!result && !error && <p role="status">Loading captions…</p>}
    {error && <p role="alert">{error}</p>}
    {result && <>
      <p role="status">{!ready ? "Stale caption timeline" : dirty ? "Unsaved caption changes" : track?.revision ? "Captions saved" : "Captions disabled by default"} · Caption revision {revision} · {provenance === "srt_import" ? "Imported SRT (editable)" : provenance === "automatic_transcription" ? "Automatic transcription (reviewed/editable)" : "Manual text"}</p>
      {result.message && <p role="alert">{result.message}</p>}
      <TranscriptionReview key={projectId} projectId={projectId} revision={revision ?? 0} hasText={!!(cues.length || track?.cues.length || dirty)} draftToken={JSON.stringify(cues)} busy={busy}
        onBusy={setProposalBusy} onApply={(value: Application) => { setCues(drafts(value.cues)); setMode(value.timeline.mode); setProvenance(value.provenance); setAutomaticId(value.automatic_proposal_id); setEnabled(true); setPreview(null); setConfirmation(null); setError(""); }} />
      <fieldset className="recipe-controls" disabled={busy || proposalBusy}>
        <legend>Caption track</legend>
        <label><input type="checkbox" checked={enabled} onChange={e => { setEnabled(e.target.checked); setConfirm(false); }} /> Enable captions</label>
        <label>Caption timeline <select value={mode} onChange={e => { setMode(e.target.value as Mode); setConfirm(false); }}><option value="whole">Whole clip</option><option value="cuts" disabled={!planState.ready}>Saved cut plan</option></select></label>
        <p>{duration !== null ? `Selected output: ${duration.toFixed(3)} seconds${mode === "cuts" ? ` · Cut plan revision ${planState.revision}` : ""}.` : "Save footage and, for cuts, a valid cut plan first."} Select the matching render mode when captions are enabled.</p>
        <label>Caption color <select value={style.color} onChange={e => setStyle(v => ({ ...v, color: e.target.value as Style["color"] }))}><option value="white">White</option><option value="yellow">Yellow</option></select></label>
        <label>Caption size <select value={style.size} onChange={e => setStyle(v => ({ ...v, size: e.target.value as Style["size"] }))}>{["small", "medium", "large"].map(v => <option key={v} value={v}>{v}</option>)}</select></label>
        <label>Caption placement <select value={style.placement} onChange={e => setStyle(v => ({ ...v, placement: e.target.value as Style["placement"] }))}><option value="bottom-center">Bottom-center</option><option value="center">Center</option></select></label>
        <p className="hint">Dark outline · Size is relative to output height. Bundled DejaVu Sans supports English and Arabic; emoji, CJK and other unsupported glyphs may show missing symbols or vary with system fallback. Very short cues may fall between 30 fps frames; review the actual export.</p>
        <label>Import SRT<input type="file" accept=".srt,text/plain,application/x-subrip" onChange={e => { const file = e.target.files?.[0]; e.target.value = ""; if (file) void run("import", file); }} /></label>
        <p className="hint">UTF-8, at most 128 KiB, 200 cues, 200 characters and two explicit lines per cue. Formatting-looking text is shown literally.</p>
        {preview && <div role="group" aria-label="SRT import preview"><h4>SRT preview: {preview.length} cues</h4><ol>{preview.map((c, i) => <li key={i}>{c.start}–{c.end} seconds <span className="caption-text">{c.text}</span></li>)}</ol><p>Replacing changes only this draft. All cue times are revalidated when saved.</p><button onClick={() => { setCues(drafts(preview)); setProvenance("srt_import"); setAutomaticId(null); setPreview(null); setConfirm(false); }}>Replace draft with imported cues</button><button onClick={() => setPreview(null)}>Cancel import</button></div>}
        {cues.map((c, i) => <fieldset key={i} className="caption-cue"><legend>Cue {i + 1}</legend>
          <label>Start (seconds)<input aria-label={`Cue ${i + 1} start`} type="number" min="0" max={duration ?? 120} step="any" value={c.start} onChange={e => edit(i, "start", e.target.value)} /></label>
          <label>End (seconds)<input aria-label={`Cue ${i + 1} end`} type="number" min="0" max={duration ?? 120} step="any" value={c.end} onChange={e => edit(i, "end", e.target.value)} /></label>
          <label>Plain text<textarea aria-label={`Cue ${i + 1} text`} dir="auto" rows={2} value={c.text} onChange={e => edit(i, "text", e.target.value)} /></label>
          <span>{Array.from(c.text).length}/200 characters</span><button onClick={() => { setCues(all => all.filter((_, n) => n !== i)); setConfirm(false); }}>Delete cue {i + 1}</button>
        </fieldset>)}
        {invalidIndex >= 0 && <p role="alert">Cue {invalidIndex + 1}: use finite ordered nonoverlapping times inside the output, nonempty text, at most 200 characters and two lines.</p>}
        {enabled && !cues.length && <p>Add at least one cue before enabling captions.</p>}
        <button disabled={cues.length >= 200} onClick={() => { const start = cues.length ? Number(cues[cues.length - 1].end) : 0; setCues(all => [...all, { start: String(Number.isFinite(start) ? start : 0), end: String(Math.min(duration ?? 120, (Number.isFinite(start) ? start : 0) + 1)), text: "" }]); setConfirm(false); }}>Add cue</button>
        <button disabled={!canSave || (!dirty && !needsRebind)} onClick={() => { if (needsRebind) setConfirm(true); else void run("save"); }}>Save captions</button>
        {confirm && <div role="group" aria-label="Confirm caption timeline rebind"><p>Rebind to {mode === "cuts" ? `cut plan revision ${planState.revision}` : "the whole clip"}? Text and entered times are preserved and every cue is revalidated. No cue shifts or truncation.</p><button disabled={!canSave} onClick={() => void run("save")}>Confirm rebind and save</button><button onClick={() => setConfirm(false)}>Cancel rebind</button></div>}
      </fieldset>
    </>}
    <button disabled={busy || proposalBusy} onClick={() => void run("reload")}>{dirty ? "Discard caption changes and reload" : "Reload saved captions"}</button>
    {busy && <p role="status">Saving, importing or loading captions…</p>}
  </section>;
}
