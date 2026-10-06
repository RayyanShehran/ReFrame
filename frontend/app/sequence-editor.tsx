"use client";

import { useEffect, useRef, useState } from "react";
import { apiBase, clipRequest, readLibrary, type SourceClip } from "./clip-library";
import { useWorkspaceReport } from "./guided-workspace";

type Slot = { id: string; clip_id: string | null; duration_frames: number; source_start_frame: number; source_end_frame: number; output_start_frame: number; output_end_frame: number; source_hash: string | null };
type Sequence = { schema_version: 1; revision: number; slots: Slot[]; output_frames: number };
type Result = { status: "empty" | "incomplete" | "ready" | "stale"; sequence: Sequence | null; message: string | null };
type Draft = { id: string; clip_id: string | null; start: string; duration: string };
export type SequenceState = { revision: number | null; ready: boolean; dirty: boolean; busy: boolean; duration?: number };
export const emptySequence: SequenceState = { revision: null, ready: false, dirty: false, busy: false };
const integer = (n: unknown, min: number, max: number) => Number.isSafeInteger(n) && Number(n) >= min && Number(n) <= max;
function parse(data: unknown): Result {
  const r = data as Result, s = r?.sequence;
  if (!r || !["empty", "incomplete", "ready", "stale"].includes(r.status) || (r.status !== "empty" && !s)) throw new Error("Invalid sequence response.");
  if (s && (s.schema_version !== 1 || !integer(s.revision, 1, Number.MAX_SAFE_INTEGER) || !integer(s.output_frames, 1, 3600) || !Array.isArray(s.slots) || !s.slots.length || s.slots.length > 60 || s.slots.some(v => !/^[0-9a-f-]{36}$/.test(v.id) || !integer(v.duration_frames, 1, 3600) || !integer(v.source_start_frame, 0, 3600)))) throw new Error("Invalid sequence response.");
  return r;
}
const drafts = (slots: Slot[]): Draft[] => slots.map(s => ({ id: s.id, clip_id: s.clip_id, start: String(s.source_start_frame / 30), duration: String(s.duration_frames / 30) }));
export function SequenceEditor({ projectId, onState }: { projectId: string; onState: (state: SequenceState) => void }) {
  const [result, setResult] = useState<Result | null>(null), [slots, setSlots] = useState<Draft[]>([]);
  const [clips, setClips] = useState<SourceClip[]>([]), [selected, setSelected] = useState("");
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [confirm, setConfirm] = useState(false);
  const [playingRange, setPlayingRange] = useState(false);
  const player = useRef<HTMLVideoElement | null>(null), action = useRef<AbortController | null>(null);
  const sequence = result?.sequence;
  const dirty = !!sequence && JSON.stringify(slots) !== JSON.stringify(drafts(sequence.slots));
  const revision = sequence?.revision ?? null;
  const ready = result?.status === "ready";
  useEffect(() => { onState({ revision, ready, dirty, busy, duration: sequence ? sequence.output_frames / 30 : undefined }); }, [onState, revision, ready, dirty, busy, sequence]);
  useWorkspaceReport("sequence", "style", busy ? "Working" : ready && !dirty ? "Ready" : error || result?.status === "stale" ? "Needs attention" : "Needs input");
  function restore(data: unknown) {
    const value = parse(data); setResult(value); setSlots(drafts(value.sequence?.slots ?? [])); setSelected(value.sequence?.slots[0]?.id ?? ""); setConfirm(false); setError("");
  }
  useEffect(() => {
    const controller = new AbortController();
    void Promise.all([clipRequest(projectId, "/sequence", undefined, controller.signal), clipRequest(projectId, "/clips", undefined, controller.signal)]).then(([s, c]) => { if (!controller.signal.aborted) { restore(s); setClips(readLibrary(c).clips); } }).catch(e => { if (!controller.signal.aborted) setError(e.message); });
    const refresh = (event: Event) => { if ((event as CustomEvent).detail === projectId) void clipRequest(projectId, "/clips", undefined, controller.signal).then(c => { if (!controller.signal.aborted) setClips(readLibrary(c).clips); }).catch(e => { if (!controller.signal.aborted) setError(e.message); }); };
    window.addEventListener("reframe-clips", refresh);
    return () => { controller.abort(); window.removeEventListener("reframe-clips", refresh); action.current?.abort(); };
  }, [projectId]);
  const slot = slots.find(s => s.id === selected), source = clips.find(c => c.id === slot?.clip_id);
  const start = Number(slot?.start), duration = Number(slot?.duration), end = start + duration;
  const available = source ? Math.floor(source.metadata.duration_seconds * 30) / 30 : 0;
  function problem(s: Draft) {
    const startFrame = Math.round(Number(s.start) * 30), length = Math.round(Number(s.duration) * 30);
    if (!s.start.trim() || !s.duration.trim() || !integer(startFrame, 0, 3600) || !integer(length, 1, 3600)) return "Enter a nonnegative start and a duration of at least one frame (1/30 s).";
    if (s.clip_id) { const clip = clips.find(c => c.id === s.clip_id); if (!clip?.available) return "Replace the unavailable source."; if (startFrame + length > Math.floor(clip.metadata.duration_seconds * 30)) return "This source is too short for the full slot. Change the start, duration or clip."; }
    return "";
  }
  const invalid = slots.findIndex(s => !!problem(s)), total = slots.reduce((n, s) => n + Math.round(Number(s.duration) * 30), 0);
  const bounded = total <= 3600 && slots.length >= 1 && slots.length <= 60;
  const rangeValid = !!slot && !!source?.available && !problem(slot);
  function patch(change: Partial<Draft>) { setPlayingRange(false); player.current?.pause(); setSlots(v => v.map(s => s.id === selected ? { ...s, ...change } : s)); }
  function select(id: string) { player.current?.pause(); setPlayingRange(false); setSelected(id); }
  function move(index: number, direction: number) { setSlots(v => { const next = [...v]; [next[index], next[index + direction]] = [next[index + direction], next[index]]; return next; }); }
  async function run(kind: "generate" | "save" | "reload") {
    if (action.current) return;
    const controller = new AbortController(); action.current = controller; setBusy(true); setError("");
    const timer = setTimeout(() => controller.abort(), 15000);
    try {
      const body = kind === "generate" ? { expected_revision: revision ?? 0, replace: !!sequence }
        : kind === "save" ? { expected_revision: revision, slots: slots.map(s => ({ id: s.id, clip_id: s.clip_id, duration_frames: Math.round(Number(s.duration) * 30), source_start_frame: Math.round(Number(s.start) * 30) })) } : undefined;
      const data = await clipRequest(projectId, `/sequence${kind === "generate" ? "/generate" : ""}`, body, controller.signal);
      if (!controller.signal.aborted) restore(data);
    } catch (e) { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "Sequence request failed."); else setError("Request interrupted. Reload saved slots before retrying."); }
    finally { clearTimeout(timer); action.current = null; setBusy(false); }
  }
  return <section className="reference-section" aria-label="Multi-clip sequence">
    <h3>Multi-clip sequence</h3><p>Create editable slots from the reference’s estimated shots, then choose the footage yourself. The saved project color recipe is applied uniformly to every clip; no per-clip grade or automatic camera matching.</p>
    <button disabled={busy || !result} onClick={() => sequence || dirty ? setConfirm(true) : void run("generate")}>{sequence ? "Regenerate slots from reference pacing" : "Create slots from reference pacing"}</button>
    {confirm && <div role="group" aria-label="Confirm slot replacement"><p>Replace the saved slots and unsaved edits? This clears assignments and can make captions and transcription stale.</p><button disabled={busy} onClick={() => void run("generate")}>Confirm replace slots</button><button onClick={() => setConfirm(false)}>Keep slots</button></div>}
    {sequence && <>
      <p>Saved revision {sequence.revision} · {sequence.output_frames / 30} seconds · 30 fps · at most 60 slots / 120 seconds</p>
      {result?.message && <p role="status">{result.message}</p>}
      <div className="sequence-editor">
        <div><ol className="sequence-slots">{slots.map((s, i) => <li key={s.id}>
          <button aria-pressed={selected === s.id} onClick={() => select(s.id)}>Slot {i + 1} · {Number(s.duration).toFixed(3)} s · {clips.find(c => c.id === s.clip_id)?.name ?? "Unassigned"}</button>
          <button aria-label={`Move slot ${i+1} earlier`} disabled={busy || i === 0} onClick={() => move(i, -1)}>↑ Earlier</button><button aria-label={`Move slot ${i+1} later`} disabled={busy || i === slots.length - 1} onClick={() => move(i, 1)}>↓ Later</button>
          <button aria-label={`Remove slot ${i+1}`} disabled={busy || slots.length === 1} onClick={() => { const next = slots.filter(v => v.id !== s.id); setSlots(next); if (selected === s.id) select(next[0].id); }}>Remove slot</button>
        </li>)}</ol><button disabled={busy || slots.length >= 60} onClick={() => { const id = crypto.randomUUID(); setSlots(v => [...v, { id, clip_id: null, start: "0", duration: "1" }]); select(id); }}>Add slot</button></div>
        {slot && <div className="sequence-trim" role="group" aria-label="Selected source range">
          <h4>Slot {slots.indexOf(slot) + 1}</h4>
          <label>Source clip <select value={slot.clip_id ?? ""} disabled={busy} onChange={e => patch({ clip_id: e.target.value || null, start: "0" })}><option value="">Choose footage</option>{clips.map(c => <option key={c.id} value={c.id} disabled={!c.available}>{c.name}{!c.available ? " (unavailable)" : ""}</option>)}</select></label>
          <label>Slot duration (seconds) <input type="number" min={1/30} max={120} step={1/30} value={slot.duration} disabled={busy} onChange={e => patch({ duration: e.target.value })} /></label>
          {source && <><video key={`${slot.id}-${source.id}`} ref={player} className="rendered-video" controls preload="metadata" src={`${apiBase}/api/projects/${encodeURIComponent(projectId)}/clips/${source.id}/video`} aria-label="Selected source video" onTimeUpdate={() => { const video = player.current; if (playingRange && video && video.currentTime >= end) { video.pause(); video.currentTime = end; setPlayingRange(false); } }} onEnded={() => setPlayingRange(false)} />
            <p>Target {duration.toFixed(3)} s · Available source {available.toFixed(3)} s · Selected {Number.isFinite(start) ? start.toFixed(3) : "?"}–{Number.isFinite(end) ? end.toFixed(3) : "?"} s</p>
            <div className="source-range" aria-label="Selected section" style={{ background: `linear-gradient(to right, #334155 ${Math.max(0, start/available*100)}%, #d0f75b ${Math.max(0, start/available*100)}%, #d0f75b ${Math.min(100, end/available*100)}%, #334155 ${Math.min(100, end/available*100)}%)` }} />
            <label>Range start <input type="range" min={0} max={Math.max(0, available-duration)} step={1/30} value={Number.isFinite(start) ? Math.max(0, start) : 0} disabled={busy || duration > available} onChange={e => patch({ start: e.target.value })} /></label>
            <label>Start (seconds) <input type="number" min={0} max={available} step={1/30} value={slot.start} disabled={busy} onChange={e => patch({ start: e.target.value })} /></label>
            <label>End (seconds) <input type="number" min={duration} max={available} step={1/30} value={Number.isFinite(end) ? end : ""} disabled={busy} onChange={e => patch({ start: e.target.value ? String(Number(e.target.value) - duration) : "" })} /></label>
            <p className="hint">Changing start or end keeps the slot duration. Saved times snap to the 30 fps grid; footage is never looped, stretched or shortened automatically.</p>
            <button disabled={busy} onClick={() => patch({ start: String(Math.round((player.current?.currentTime ?? 0) * 30) / 30) })}>Use current playback position as start</button>
            <button disabled={busy || !rangeValid} onClick={() => { const video = player.current; if (video) { video.currentTime = start; setPlayingRange(true); void video.play().catch(() => { setPlayingRange(false); setError("Playback could not start. Use the native player controls."); }); } }}>Preview selected range</button>
          </>}
          {problem(slot) && <p role="alert">{problem(slot)}</p>}
        </div>}
      </div>
      {!bounded && <p role="alert">Keep 1–60 slots totaling at most 120 seconds.</p>}
      {invalid >= 0 && <p role="alert">Slot {invalid + 1}: {problem(slots[invalid])}</p>}
      {slots.some(s => !s.clip_id) && <p role="status">Assign every slot before rendering. You can save unassigned slots as a draft.</p>}
      <button disabled={busy || !dirty || invalid >= 0 || !bounded} onClick={() => void run("save")}>Save sequence</button>
      <button disabled={busy} onClick={() => void run("reload")}>Reload saved sequence (discard draft)</button>
      <p role="status">{busy ? "Working…" : dirty ? "Unsaved sequence changes" : "Saved sequence restored"}</p>
    </>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
