"use client";

import { useEffect, useRef, useState } from "react";
import Image from "next/image";
import { apiBase, clipRequest, type SourceClip } from "./clip-library";
import { useMediaOperation, type MediaOperation } from "./use-media-operation";

export type Assignment = { id: string; clip_id: string | null; duration_frames: number; source_start_frame: number; reference_start_frame?: number | null; reference_end_frame?: number | null };
type Choice = Assignment & { explanation: string; warnings: string[]; visual_similarity?: number | null; reference_image?: string | null; footage_image?: string | null; reference_sample_frames?: number[]; footage_sample_frames?: number[] };
type Mode = "measurements" | "subject_aware";
type Settings = { matching_mode?: Mode; expected_sequence_revision: number; allow_reused_ranges: boolean; locked_slot_ids: string[] };
type Proposal = { id: string; settings: Settings; choices: Choice[]; warnings: string[]; cached_sources: number; model_repository?: string | null; model_revision?: string | null; sequence: { revision: number } };
type Operation = MediaOperation & { proposal: Proposal | null; proposal_stale: boolean; completed_sources: number; total_sources: number };
function parse(data: unknown): Operation {
  const r = data as Operation;
  if (!r || !["idle", "running", "ready", "failed"].includes(r.status) || !Number.isSafeInteger(r.completed_sources) || !Number.isSafeInteger(r.total_sources) || r.completed_sources < 0 || r.total_sources > 11) throw new Error("Invalid assembly status.");
  if (r.proposal && (!Array.isArray(r.proposal.choices) || !r.proposal.choices.length || r.proposal.choices.length > 60 || r.proposal.choices.some(c => !Number.isSafeInteger(c.duration_frames) || c.duration_frames < 1 || c.duration_frames > 3600 || !Number.isSafeInteger(c.source_start_frame) || c.source_start_frame < 0 || typeof c.explanation !== "string"))) throw new Error("Invalid assembly proposal.");
  return r;
}
export function AssemblyReview({ projectId, revision, savedSlots, clips, dirty, disabled, onApply, onPreview }: { projectId: string; revision: number; savedSlots: Assignment[]; clips: SourceClip[]; dirty: boolean; disabled: boolean; onApply: (slots: Assignment[]) => void; onPreview: (slot: Assignment) => void }) {
  const { operation, error, starting, start, refresh } = useMediaOperation(projectId, "assembly", parse);
  const [reuseDraft, setReuse] = useState<boolean | null>(null), [locksDraft, setLocks] = useState<string[] | null>(null), [applying, setApplying] = useState(false), [message, setMessage] = useState("");
  const [confirm, setConfirm] = useState(false);
  const [modeDraft, setMode] = useState<Mode | null>(null);
  const [model, setModel] = useState<{ ready: boolean; setup: string } | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    void fetch(`${apiBase}/api/visual-model`, { method: "GET", signal: controller.signal }).then(r => { if (!r.ok) throw new Error("Model readiness unavailable."); return r.json(); }).then(r => { if (!controller.signal.aborted) setModel({ ready: r.ready === true, setup: typeof r.setup === "string" ? r.setup : "In backend: uv sync --locked --extra visual; uv run --locked --extra visual python visual_matching.py setup" }); }).catch(() => { if (!controller.signal.aborted) setModel({ ready: false, setup: "Model readiness unavailable. Reload after explicit local model setup." }); });
    return () => controller.abort();
  }, [projectId]);
  const action = useRef<AbortController | null>(null);
  const proposal = operation?.proposal;
  const running = starting || operation?.status === "running";
  const savedSettings = proposal?.settings.expected_sequence_revision === revision ? proposal.settings : null;
  const mode = modeDraft ?? savedSettings?.matching_mode ?? "measurements";
  const reuse = reuseDraft ?? savedSettings?.allow_reused_ranges ?? false;
  const locks = (locksDraft ?? savedSettings?.locked_slot_ids ?? []).filter(id => savedSlots.some(s => s.id === id && s.clip_id));
  useEffect(() => () => { action.current?.abort(); }, [projectId]);
  const settings: Settings = { matching_mode: mode, expected_sequence_revision: revision, allow_reused_ranges: reuse, locked_slot_ids: [...locks].sort() };
  const changed = !!proposal && ((proposal.settings.matching_mode ?? "measurements") !== mode || proposal.settings.expected_sequence_revision !== revision || proposal.settings.allow_reused_ranges !== reuse || JSON.stringify([...proposal.settings.locked_slot_ids].sort()) !== JSON.stringify(settings.locked_slot_ids));
  async function command(kind: "apply" | "cancel") {
    if (action.current) return;
    const controller = new AbortController(); action.current = controller; setApplying(true); setMessage("");
    const timer = setTimeout(() => controller.abort(), 35000);
    try {
      const data = await clipRequest(projectId, `/assembly/${kind}`, kind === "apply" ? { ...settings, proposal_id: proposal?.id } : {}, controller.signal);
      if (!controller.signal.aborted) {
        if (kind === "apply") { const r = data as { expected_revision: number; slots: Assignment[] }; if (r.expected_revision !== revision || !Array.isArray(r.slots)) throw new Error("Sequence changed; reload and suggest again."); onApply(r.slots); setConfirm(false); setMessage("Proposal applied to draft. Review or adjust it, then Save sequence."); }
        else { refresh(); setMessage("Selection stopped. Saved sequence and previous proposal are retained."); }
      }
    } catch (e) { if (!controller.signal.aborted) setMessage(e instanceof Error ? e.message : "Assembly request failed."); }
    finally { clearTimeout(timer); if (action.current === controller) { action.current = null; setApplying(false); } }
  }
  return <section className="reference-section" aria-label="Automatic assembly">
    <h4>Automatic assembly</h4><p>Choose measured ranges or experimental subject-aware visual similarity. Frame embeddings cannot establish actions, story, identity or artistic intent. Review the images and ranges before applying.</p>
    <fieldset disabled={running || applying || disabled}><legend>Selection options</legend>
      <label>Matching mode <select value={mode} onChange={e => { setMode(e.target.value as Mode); setConfirm(false); }}><option value="measurements">Measurements</option><option value="subject_aware" disabled={!model?.ready}>Subject-aware visual matching{!model?.ready ? " (setup required)" : ""}</option></select></label>
      {!model?.ready && <p className="hint">Measurements remain available. {model?.setup ?? "Checking local model readiness…"}</p>}
      {mode === "subject_aware" && <p className="hint">Local CPU model · no network inference. At most 512 samples across the project, 2 fps. Brief intervals without two samples use measurements and are labeled. Reference association stays with each saved slot.</p>}
      <label><input type="checkbox" checked={reuse} onChange={e => { setReuse(e.target.checked); setConfirm(false); }} /> Allow reused ranges</label>
      <p>Locked choices keep their saved clip and range. Save manual changes before suggesting.</p>
      {savedSlots.map((s, i) => <label key={s.id}><input type="checkbox" disabled={!s.clip_id} checked={locks.includes(s.id)} onChange={e => { setLocks(e.target.checked ? [...locks,s.id] : locks.filter(id => id !== s.id)); setConfirm(false); }} /> Lock slot {i+1}{!s.clip_id ? " (assign and save first)" : ""}</label>)}
    </fieldset>
    {dirty && <p role="status">Save the slot structure and manual assignments before suggesting. Applying a completed proposal can replace unsaved draft edits after confirmation.</p>}
    <button disabled={disabled || applying || running || dirty || (mode === "subject_aware" && !model?.ready)} onClick={() => { setMessage(""); void start(settings); }}>{operation?.status === "failed" ? "Retry assembly suggestion" : "Suggest an assembly"}</button>
    {running && <><p role="status">Analyzing sources: {operation?.completed_sources ?? 0}/{operation?.total_sources ?? 0}. Processing is capped at 120 seconds.</p><button disabled={applying} onClick={() => void command("cancel")}>Cancel selection</button></>}
    {proposal && <><p>Saved proposal · sequence revision {proposal.sequence.revision} · {proposal.cached_sources} cached source measurements · {proposal.settings.matching_mode === "subject_aware" ? "Subject-aware visual matching" : "Measurements"}</p>
      {proposal.model_repository && <p className="hint">{proposal.model_repository} · model revision {proposal.model_revision}</p>}
      {(operation?.proposal_stale || changed) && <p role="alert">Proposal is outdated or selection options changed. Save the current slots and suggest explicitly again.</p>}
      <ol>{proposal.choices.map((c,i) => <li key={c.id}><strong>Slot {i+1}: {clips.find(v => v.id === c.clip_id)?.name ?? "Unfilled"}</strong>{c.clip_id && <p>{(c.source_start_frame/30).toFixed(3)}–{((c.source_start_frame+c.duration_frames)/30).toFixed(3)} s · {c.duration_frames/30} s</p>}<p>{c.explanation}</p>
        {c.reference_image && c.footage_image && <div className="frame-preview-images"><figure><figcaption>Reference sample · {((c.reference_sample_frames?.[Math.floor((c.reference_sample_frames?.length ?? 0)/2)] ?? 0)/30).toFixed(3)}s</figcaption><Image unoptimized src={`data:image/jpeg;base64,${c.reference_image}`} width={128} height={128} alt={`Reference sample for slot ${i+1}`} /></figure><figure><figcaption>Selected footage sample · {((c.footage_sample_frames?.[Math.floor((c.footage_sample_frames?.length ?? 0)/2)] ?? 0)/30).toFixed(3)}s</figcaption><Image unoptimized src={`data:image/jpeg;base64,${c.footage_image}`} width={128} height={128} alt={`Selected footage sample for slot ${i+1}`} /></figure></div>}
        {c.warnings.map(w => <p role="note" key={w}>{w}</p>)}<button disabled={!c.clip_id || disabled || running || operation?.proposal_stale || changed} onClick={() => onPreview(c)}>Review proposed range {i+1}</button></li>)}</ol>
      {proposal.warnings.map(w => <p className="hint" key={w}>{w}</p>)}
      <button disabled={disabled || running || applying || operation?.proposal_stale || changed} onClick={() => dirty ? setConfirm(true) : void command("apply")}>Apply proposal to draft</button>
      {confirm && <div role="group" aria-label="Confirm proposal replacement"><p>Replace unsaved sequence edits with this proposal? Save remains explicit.</p><button onClick={() => void command("apply")}>Confirm replace draft</button><button onClick={() => setConfirm(false)}>Keep draft</button></div>}
    </>}
    {(error || operation?.message) && <p role="alert">{error || operation?.message}</p>}{message && <p role="status">{message}</p>}
  </section>;
}
