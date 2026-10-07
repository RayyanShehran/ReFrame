"use client";

import { useEffect, useRef, useState } from "react";
import { clipRequest, readLibrary, type SourceClip } from "./clip-library";
import { useMediaOperation, type MediaOperation } from "./use-media-operation";
import { useWorkspaceNavigation, useWorkspaceReport } from "./guided-workspace";
import type { SequenceState } from "./sequence-editor";

export const colorModes = { original: "Original / no color adjustment", basic: "Basic adjustments", transfer: "Reference color transfer" };
export type ColorMode = keyof typeof colorModes;
export type Controls = { strength: number; shadows: number; midtones: number; highlights: number; shadow_red: number; shadow_blue: number; highlight_red: number; highlight_blue: number; saturation: number };
export const defaultControls: Controls = { strength: .5, shadows: 0, midtones: 0, highlights: 0, shadow_red: 0, shadow_blue: 0, highlight_red: 0, highlight_blue: 0, saturation: 1 };
type Settings = { schema_version: 1; revision: number; mode: ColorMode; controls: Record<string, Controls>; shot_slots: string[]; updated_at: string | null };
type Entry = { valid: boolean; message: string | null; match: { key: string; clip_id: string; slot_id: string | null; sequence_revision: number | null; source_hash: string; reference: { source: { canonical_url: string; media_sha256: string } }; model: { algorithm: string; warnings: string[] }; prepared_at: string } };
type Operation = MediaOperation & { settings: Settings; entries: Entry[]; completed: number; total: number };
type Slot = { id: string; clip_id: string | null; source_start_frame: number; source_end_frame: number; reference_start_frame: number | null; reference_end_frame: number | null };
export type PreviewChoice = { key: string; name: string; clipId: string; slotId?: string; start: number; end: number; version?: string };
export type GradingState = { revision: number; mode: ColorMode; ready: boolean; dirty: boolean; busy: boolean; sequenceReady: boolean; choices: PreviewChoice[]; sequenceRevision: number | null; version?: string };
export const initialGrading: GradingState = { revision: 0, mode: "basic", ready: false, dirty: false, busy: false, sequenceReady: false, choices: [], sequenceRevision: null };
const refinements = [
  ["shadows", "Shadow tone", -.1, .1], ["midtones", "Midtone tone", -.1, .1], ["highlights", "Highlight tone", -.1, .1],
  ["shadow_red", "Shadow red / cyan", -.08, .08], ["shadow_blue", "Shadow blue / yellow", -.08, .08],
  ["highlight_red", "Highlight red / cyan", -.08, .08], ["highlight_blue", "Highlight blue / yellow", -.08, .08],
  ["saturation", "Chroma refinement", .8, 1.2],
] as const;
function parse(data: unknown): Operation {
  const o = data as Operation, s = o?.settings;
  if (!o || !["idle", "running", "ready", "failed"].includes(o.status) || !s || s.schema_version !== 1 || !Object.hasOwn(colorModes, s.mode) || !Number.isSafeInteger(s.revision) || s.revision < 0 || !Array.isArray(s.shot_slots) || s.shot_slots.length > 60 || !s.controls || !Array.isArray(o.entries) || o.entries.length > 70) throw new Error("Invalid color match response.");
  for (const c of Object.values(s.controls)) if (!c || !Number.isFinite(c.strength) || c.strength < 0 || c.strength > 1 || refinements.some(([key, , min, max]) => !Number.isFinite(c[key]) || c[key] < min || c[key] > max)) throw new Error("Invalid saved color controls.");
  for (const e of o.entries) if (typeof e.valid !== "boolean" || !/^(clip|slot):[0-9a-f-]{36}$/.test(e.match?.key) || !/^[0-9a-f-]{36}$/.test(e.match.clip_id) || !/^[0-9a-f]{64}$/.test(e.match.source_hash) || !Array.isArray(e.match.model?.warnings)) throw new Error("Invalid prepared color match.");
  return o;
}
export function GradingControls({ projectId, sourceKey, analysesReady, sequence, onState }: { projectId: string; sourceKey: string; analysesReady: boolean; sequence: SequenceState; onState: (state: GradingState) => void }) {
  const { operation, error, starting, start, refresh } = useMediaOperation(projectId, "grading", parse, 360);
  const [draft, setDraft] = useState<Settings | null>(null), [selected, setSelected] = useState("");
  const [clips, setClips] = useState<SourceClip[]>([]), [slots, setSlots] = useState<Slot[]>([]);
  const [loadError, setLoadError] = useState(""), [confirm, setConfirm] = useState<"project" | "shot" | null>(null);
  const initialized = useRef(false);
  const navigation = useWorkspaceNavigation();
  const saved = operation?.settings;
  const dirty = !!saved && !!draft && (draft.mode !== saved.mode || JSON.stringify(draft.controls) !== JSON.stringify(saved.controls) || JSON.stringify(draft.shot_slots) !== JSON.stringify(saved.shot_slots));
  const busy = starting || operation?.status === "running";
  const primary = clips.find(c => c.primary);
  const validKeys = new Set(operation?.entries.filter(e => e.valid).map(e => e.match.key));
  const ready = !!saved && !!primary?.available && (saved.mode !== "transfer" || validKeys.has(`clip:${primary.id}`));
  useWorkspaceReport("grading", "style", busy ? "Working" : dirty || !ready ? "Needs input" : "Ready", busy ? "Preparing reference color matches…" : undefined);
  const sequenceReady = !!saved && (saved.mode !== "transfer" || slots.every(s => s.clip_id && validKeys.has(saved.shot_slots.includes(s.id) ? `slot:${s.id}` : `clip:${s.clip_id}`)));
  const choices: PreviewChoice[] = [...clips.map(c => ({ key: `clip:${c.id}`, name: c.name, clipId: c.id, start: 0, end: c.metadata.duration_seconds })),
    ...slots.filter(s => s.clip_id).map((s, i) => ({ key: `slot:${s.id}`, name: `Slot ${i + 1} · ${clips.find(c => c.id === s.clip_id)?.name ?? "footage"}`, clipId: s.clip_id!, slotId: s.id, start: s.source_start_frame / 30, end: s.source_end_frame / 30 }))];
  for (const c of choices) { const route = c.slotId && saved?.shot_slots.includes(c.slotId) ? c.key : `clip:${c.clipId}`; const e = operation?.entries.find(e => e.match.key === route); c.version = `${e?.valid}:${e?.match.prepared_at}:${clips.find(clip => clip.id === c.clipId)?.sha256}`; }
  const version = JSON.stringify(operation?.entries.map(e => [e.match.key, e.match.prepared_at, e.valid]));
  const choicesKey = JSON.stringify(choices);
  useEffect(() => { onState({ revision: saved?.revision ?? 0, mode: saved?.mode ?? "basic", ready, dirty, busy, sequenceReady, choices: JSON.parse(choicesKey), sequenceRevision: sequence.revision, version }); }, [onState, saved?.revision, saved?.mode, ready, dirty, busy, sequenceReady, choicesKey, sequence.revision, version]);
  useEffect(() => { if (saved && !initialized.current) { initialized.current = true; setDraft(saved); } }, [saved]);
  useEffect(() => {
    const controller = new AbortController();
    void Promise.all([clipRequest(projectId, "/clips", undefined, controller.signal), clipRequest(projectId, "/sequence", undefined, controller.signal)]).then(([library, seq]) => {
      if (!controller.signal.aborted) { setClips(readLibrary(library).clips); setSlots(seq.status === "ready" ? seq.sequence.slots : []); }
    }).catch(e => { if (!controller.signal.aborted) setLoadError(e.message); });
    refresh();
    return () => controller.abort();
  }, [projectId, sourceKey, sequence.revision, sequence.ready, refresh]);
  const key = selected || choices[0]?.key || "";
  const entry = operation?.entries.find(e => e.match.key === key);
  const chosenSlot = slots.find(s => `slot:${s.id}` === key);
  const controls = draft?.controls[key] ?? defaultControls;
  function adjust(name: keyof Controls, value: number) { if (draft) setDraft({ ...draft, controls: { ...draft.controls, [key]: { ...controls, [name]: value } } }); }
  async function save() { if (!draft || !saved || busy) return; const result = await start({ expected_revision: saved.revision, mode: draft.mode, controls: draft.controls, shot_slots: draft.shot_slots }); if (result) setDraft(result.settings); }
  async function prepare(replace: boolean, assigned = false) {
    if (!saved || busy) return;
    const result = await start({ expected_revision: saved.revision, replace, shot_slots: assigned && chosenSlot ? [chosenSlot.id] : [], ...(assigned ? { expected_sequence_revision: sequence.revision } : {}) }, "/prepare");
    if (result) { setConfirm(null); setDraft(previous => previous ? { ...previous, mode: "transfer" } : { ...result.settings, mode: "transfer" }); }
  }
  return <section className="reference-section" aria-label="Reference color matching"><h3>Match reference colors</h3>
    <p>Prepare the reference look, match each clip, save your choice, then preview and export. Analysis measurements alone do not activate a grade.</p>
    <button disabled={!analysesReady || busy || dirty} onClick={() => operation?.entries.some(e => e.valid) ? setConfirm("project") : void prepare(false)}>Match reference colors</button>
    {!analysesReady && <p>Prepare valid reference and original footage colors first. <button onClick={() => navigation?.open("reference", "prepare-workflow")}>Open preparation</button></p>}
    {confirm && <div role="group" aria-label="Confirm color rematching"><p>Re-estimate prepared transforms? Your saved per-clip strength and refinements remain. Review and save the mode explicitly.</p><button disabled={busy} onClick={() => void prepare(true, confirm === "shot")}>Re-estimate matches</button><button onClick={() => setConfirm(null)}>Keep matches</button></div>}
    {draft && <>
      <label>Color mode<select value={draft.mode} disabled={busy} onChange={e => setDraft({ ...draft, mode: e.target.value as ColorMode })}>{Object.entries(colorModes).map(([value, name]) => <option key={value} value={value}>{name}</option>)}</select></label>
      <p role="status">{dirty ? "Unsaved color changes" : "Saved color mode"}: {colorModes[dirty ? draft!.mode : saved?.mode ?? "basic"]} · {dirty ? "Saved revision" : "Revision"} {saved?.revision ?? 0}</p>
      <p className="hint">Original bypasses color adjustments. Basic uses the existing recipe below. Transfer uses a separate fixed match for each clip; Basic is not added on top.</p>
      {draft.mode === "transfer" && <>
        <label>Clip or slot to refine<select value={key} onChange={e => setSelected(e.target.value)}>{choices.map(c => <option key={c.key} value={c.key}>{c.name}</option>)}</select></label>
        <p role="status">{entry?.valid ? "Prepared match" : "Match required"}{entry?.message && ` · ${entry.message}`}</p>
        {chosenSlot && <>
          <label><input type="checkbox" checked={draft.shot_slots.includes(chosenSlot.id)} disabled={!entry?.valid || busy} onChange={e => setDraft({ ...draft, shot_slots: e.target.checked ? [...draft.shot_slots, chosenSlot.id] : draft.shot_slots.filter(id => id !== chosenSlot.id) })} />Use assigned reference shot for this slot</label>
          <button disabled={busy || dirty || !sequence.ready || chosenSlot.reference_start_frame === null} onClick={() => entry ? setConfirm("shot") : void prepare(false, true)}>Match assigned reference shot</button>
          {chosenSlot.reference_start_frame === null ? <p>This slot has no reference interval. Use its clip’s project reference look.</p> : <p className="hint">Source {chosenSlot.source_start_frame / 30}–{chosenSlot.source_end_frame / 30}s · Reference {chosenSlot.reference_start_frame / 30}–{chosenSlot.reference_end_frame! / 30}s. Enable after preparing, then save.</p>}
        </>}
        <fieldset className="recipe-controls" disabled={!entry?.valid || busy}>
          <legend>{choices.find(c => c.key === key)?.name ?? "Selected clip"} controls</legend>
          <label htmlFor={`grade-${projectId}-${key}-strength`}>Match strength: <span>{Math.round(controls.strength * 100)}%</span><input id={`grade-${projectId}-${key}-strength`} type="range" min="0" max="100" value={Math.round(controls.strength * 100)} onChange={e => adjust("strength", Number(e.target.value) / 100)} /></label>
          <details><summary>Advanced tone and color refinements</summary>{refinements.map(([name, label, min, max]) => <label key={name} htmlFor={`grade-${projectId}-${key}-${name}`}>{label}: <span>{controls[name].toFixed(2)}</span><input id={`grade-${projectId}-${key}-${name}`} type="range" min={min} max={max} step=".01" value={controls[name]} onChange={e => adjust(name, Number(e.target.value))} /></label>)}</details>
          <button onClick={() => setDraft({ ...draft, controls: { ...draft.controls, [key]: defaultControls } })}>Reset selected match</button>
        </fieldset>
        {entry && <><p className="hint">Look: {entry.match.reference.source.canonical_url} · Source {entry.match.source_hash.slice(0, 12)} · {entry.match.model.algorithm}</p>{entry.match.model.warnings.map(w => <p role="note" key={w}>{w}</p>)}</>}
        <ul aria-label="Prepared footage colors">{clips.map(c => <li key={c.id}>{c.name}: {validKeys.has(`clip:${c.id}`) ? "prepared project look" : "match required"}</li>)}</ul>
      </>}
      <button disabled={!dirty || busy || (draft.mode === "transfer" && !primary)} onClick={() => void save()}>Save color mode and matches</button>
      <button disabled={busy} onClick={() => setDraft(saved ?? null)}>Reset unsaved color changes</button>
      <button disabled={busy} onClick={() => { initialized.current = false; refresh(); }}>Reload saved color settings</button>
    </>}
    {busy && <p role="status">Preparing color matches {operation?.completed ?? 0}/{operation?.total ?? 0}… Processing is bounded to five minutes, one shared worker.</p>}
    {operation?.status === "failed" && <p role="alert">{operation.message} Previous matches and settings remain. Retry matching explicitly.</p>}
    {(error || loadError) && <p role="alert">{error || loadError}</p>}
    <p className="hint">Statistical SDR approximation, not a recovered creator LUT or AI grading. Different scenes can give misleading suggestions. Review frames and the exported video.</p>
  </section>;
}
