"use client";

import { useEffect, useRef, useState } from "react";
import { clipRequest, type SourceClip } from "./clip-library";
import { useMediaOperation, type MediaOperation } from "./use-media-operation";

export type Assignment = { id: string; clip_id: string | null; duration_frames: number; source_start_frame: number };
type Choice = Assignment & { explanation: string; warnings: string[] };
type Settings = { expected_sequence_revision: number; allow_reused_ranges: boolean; locked_slot_ids: string[] };
type Proposal = { id: string; settings: Settings; choices: Choice[]; warnings: string[]; cached_sources: number; sequence: { revision: number } };
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
  const action = useRef<AbortController | null>(null);
  const proposal = operation?.proposal;
  const running = starting || operation?.status === "running";
  const savedSettings = proposal?.settings.expected_sequence_revision === revision ? proposal.settings : null;
  const reuse = reuseDraft ?? savedSettings?.allow_reused_ranges ?? false;
  const locks = (locksDraft ?? savedSettings?.locked_slot_ids ?? []).filter(id => savedSlots.some(s => s.id === id && s.clip_id));
  useEffect(() => () => { action.current?.abort(); }, [projectId]);
  const settings: Settings = { expected_sequence_revision: revision, allow_reused_ranges: reuse, locked_slot_ids: [...locks].sort() };
  const changed = !!proposal && (proposal.settings.expected_sequence_revision !== revision || proposal.settings.allow_reused_ranges !== reuse || JSON.stringify([...proposal.settings.locked_slot_ids].sort()) !== JSON.stringify(settings.locked_slot_ids));
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
    <h4>Automatic assembly</h4><p>Automatic selection based on motion, scene boundaries and image measurements. No subject recognition or objective artistic quality judgment. Unchanged inputs produce the same ranges.</p>
    <fieldset disabled={running || applying || disabled}><legend>Selection options</legend>
      <label><input type="checkbox" checked={reuse} onChange={e => { setReuse(e.target.checked); setConfirm(false); }} /> Allow reused ranges</label>
      <p>Locked choices keep their saved clip and range. Save manual changes before suggesting.</p>
      {savedSlots.map((s, i) => <label key={s.id}><input type="checkbox" disabled={!s.clip_id} checked={locks.includes(s.id)} onChange={e => { setLocks(e.target.checked ? [...locks,s.id] : locks.filter(id => id !== s.id)); setConfirm(false); }} /> Lock slot {i+1}{!s.clip_id ? " (assign and save first)" : ""}</label>)}
    </fieldset>
    {dirty && <p role="status">Save the slot structure and manual assignments before suggesting. Applying a completed proposal can replace unsaved draft edits after confirmation.</p>}
    <button disabled={disabled || applying || running || dirty} onClick={() => { setMessage(""); void start(settings); }}>{operation?.status === "failed" ? "Retry assembly suggestion" : "Suggest an assembly"}</button>
    {running && <><p role="status">Measuring sources: {operation?.completed_sources ?? 0}/{operation?.total_sources ?? 0}. Processing is capped at 120 seconds.</p><button disabled={applying} onClick={() => void command("cancel")}>Cancel selection</button></>}
    {proposal && <><p>Saved proposal · sequence revision {proposal.sequence.revision} · {proposal.cached_sources} cached source measurements</p>
      {(operation?.proposal_stale || changed) && <p role="alert">Proposal is outdated or selection options changed. Save the current slots and suggest explicitly again.</p>}
      <ol>{proposal.choices.map((c,i) => <li key={c.id}><strong>Slot {i+1}: {clips.find(v => v.id === c.clip_id)?.name ?? "Unfilled"}</strong>{c.clip_id && <p>{(c.source_start_frame/30).toFixed(3)}–{((c.source_start_frame+c.duration_frames)/30).toFixed(3)} s · {c.duration_frames/30} s</p>}<p>{c.explanation}</p>{c.warnings.map(w => <p role="note" key={w}>{w}</p>)}<button disabled={!c.clip_id || disabled || running || operation?.proposal_stale || changed} onClick={() => onPreview(c)}>Review proposed range {i+1}</button></li>)}</ol>
      {proposal.warnings.map(w => <p className="hint" key={w}>{w}</p>)}
      <button disabled={disabled || running || applying || operation?.proposal_stale || changed} onClick={() => dirty ? setConfirm(true) : void command("apply")}>Apply proposal to draft</button>
      {confirm && <div role="group" aria-label="Confirm proposal replacement"><p>Replace unsaved sequence edits with this proposal? Save remains explicit.</p><button onClick={() => void command("apply")}>Confirm replace draft</button><button onClick={() => setConfirm(false)}>Keep draft</button></div>}
    </>}
    {(error || operation?.message) && <p role="alert">{error || operation?.message}</p>}{message && <p role="status">{message}</p>}
  </section>;
}
