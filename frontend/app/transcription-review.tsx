"use client";

import { useEffect, useRef, useState } from "react";
import { MediaOperation, useMediaOperation } from "./use-media-operation";

type Cue = { start: number; end: number; text: string };
type Draft = { start: string; end: string; text: string };
type Timeline = { mode: "whole" | "cuts" | "sequence"; plan_revision: number | null; sequence_revision?: number | null; duration_seconds: number };
export type Application = { cues: Cue[]; timeline: Timeline; automatic_proposal_id: string; provenance: "automatic_transcription" };
type Operation = MediaOperation & { operation_id: string | null; stale: boolean; proposal: null | {
  cues: Cue[]; requested_language: "auto" | "en" | "ar"; detected_language: string;
  binding: { timeline: Timeline }; model_revision: string;
} };
const apiBase = (process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
const finite = (v: unknown) => typeof v === "number" && Number.isFinite(v) && v >= 0 && v <= 120;
function valid(cues: Cue[], duration: number) {
  return Array.isArray(cues) && cues.length <= 200 && cues.every((c, i) => finite(c.start) && finite(c.end) && c.start < c.end && c.end <= duration &&
    typeof c.text === "string" && !!c.text.trim() && Array.from(c.text).length <= 200 && c.text.split("\n").length <= 2 && (!i || c.start >= cues[i - 1].end));
}
function parse(data: unknown): Operation {
  const r = data as Operation, p = r?.proposal;
  if (!r || !["idle", "running", "ready", "failed"].includes(r.status) || typeof r.stale !== "boolean" ||
    (r.operation_id !== null && typeof r.operation_id !== "string") ||
    (p && (!p.binding?.timeline || !finite(p.binding.timeline.duration_seconds) || p.binding.timeline.duration_seconds <= 0 ||
      !["whole", "cuts", "sequence"].includes(p.binding.timeline.mode) || !valid(p.cues, p.binding.timeline.duration_seconds) ||
      !["auto", "en", "ar"].includes(p.requested_language) || typeof p.detected_language !== "string" || typeof p.model_revision !== "string"))) {
    throw new Error("Invalid transcription response.");
  }
  return r;
}
export function TranscriptionReview({ projectId, revision, hasText, draftToken, busy, onApply, onBusy }: {
  projectId: string; revision: number; hasText: boolean; draftToken: string; busy: boolean;
  onApply: (value: Application) => void; onBusy: (value: boolean) => void;
}) {
  const { operation, error, starting, start } = useMediaOperation(projectId, "transcription", parse, 360);
  const [language, setLanguage] = useState<"auto" | "en" | "ar" | null>(null);
  const [draft, setDraft] = useState<{ id: string; cues: Draft[] } | null>(null);
  const [discarded, setDiscarded] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState<string | null>(null);
  const [acting, setActing] = useState(false), [actionError, setActionError] = useState("");
  const action = useRef<AbortController | null>(null);
  useEffect(() => () => action.current?.abort(), []);
  useEffect(() => { onBusy(acting); }, [acting, onBusy]);
  const proposal = operation?.operation_id !== discarded ? operation?.proposal : null;
  const id = operation?.operation_id ?? "";
  const cues = draft?.id === id ? draft.cues : (proposal?.cues ?? []).map(c => ({ ...c, start: String(c.start), end: String(c.end) }));
  const numeric = cues.map(c => ({ ...c, start: Number(c.start), end: Number(c.end) }));
  const invalid = !proposal || !numeric.length || cues.some(c => !c.start.trim() || !c.end.trim()) || !valid(numeric, proposal.binding.timeline.duration_seconds);
  const blocked = busy || acting || starting || operation?.status === "running";
  const target = `${id}:${revision}:${draftToken}`;
  async function act(kind: "apply" | "discard", replace = false) {
    if (action.current) return;
    const controller = new AbortController(); action.current = controller; setActing(true); setActionError("");
    const timer = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch(`${apiBase}/api/projects/${encodeURIComponent(projectId)}/transcription${kind === "apply" ? "/apply" : ""}`, {
        method: kind === "apply" ? "POST" : "DELETE", signal: controller.signal,
        ...(kind === "apply" ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify({ operation_id: id, expected_caption_revision: revision, replace, cues: numeric }) } : {}),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data?.error?.message || "Proposal request failed.");
      if (!controller.signal.aborted) {
        if (kind === "apply") {
          const value = data as Application;
          if (value.provenance !== "automatic_transcription" || value.automatic_proposal_id !== id || !value.timeline || !valid(value.cues, value.timeline.duration_seconds)) throw new Error("Invalid caption proposal.");
          onApply(value); setConfirmation(null);
        } else { setDiscarded(id); setDraft(null); setConfirmation(null); }
      }
    } catch (cause) { setActionError(cause instanceof Error && cause.name !== "AbortError" ? cause.message : "Request timed out. Reopen the project before retrying."); }
    finally { clearTimeout(timer); action.current = null; setActing(false); }
  }
  return <section aria-label="Automatic captions">
    <h4>Generate captions locally</h4>
    <p className="hint">Transcribes a completed render’s audio, including cuts and your saved audio choices. Review every word and time: speech mixed with music can be missed or misheard, and silence can produce invented text. No translation or reference-caption extraction.</p>
    <label>Speech language <select disabled={blocked} value={language ?? proposal?.requested_language ?? "auto"} onChange={e => setLanguage(e.target.value as "auto" | "en" | "ar")}><option value="auto">Auto-detect</option><option value="en">English</option><option value="ar">Arabic</option></select></label>
    <button disabled={blocked || !operation} onClick={() => { setActionError(""); void start({ language: language ?? proposal?.requested_language ?? "auto", replace: operation?.status === "ready" }); }}>
      {operation?.status === "failed" ? "Retry caption generation" : proposal ? "Regenerate caption proposal" : "Generate caption proposal"}
    </button>
    {operation?.status === "running" && <p role="status">Generating a caption proposal… Saved captions are preserved.</p>}
    {(error || actionError || operation?.message) && <p role="alert">{actionError || error || operation?.message}</p>}
    {proposal && <>
      <p role="status">{operation?.stale ? "Stale proposal: regenerate after rendering current audio and timeline." : `Review ${cues.length} proposed cues · Detected language: ${proposal.detected_language}`}</p>
      {!cues.length && <p>No speech detected. No captions were added.</p>}
      <fieldset disabled={blocked || operation?.stale}>
        <legend>Automatic proposal (not saved captions)</legend>
        {cues.map((c, i) => <fieldset className="caption-cue" key={i}><legend>Proposed cue {i + 1}</legend>
          {(["start", "end", "text"] as const).map(key => <label key={key}>{key === "text" ? "Text" : `${key} (seconds)`}
            {key === "text" ? <textarea dir="auto" aria-label={`Proposed cue ${i + 1} text`} value={c.text} onChange={e => setDraft({ id, cues: cues.map((v, n) => n === i ? { ...v, text: e.target.value } : v) })} /> :
              <input type="number" step="any" aria-label={`Proposed cue ${i + 1} ${key}`} value={c[key]} onChange={e => setDraft({ id, cues: cues.map((v, n) => n === i ? { ...v, [key]: e.target.value } : v) })} />}
          </label>)}
        </fieldset>)}
        {invalid && !!cues.length && <p role="alert">Use ordered nonoverlapping times inside the output, nonempty text, at most 200 characters and two lines per cue.</p>}
        <button disabled={invalid} onClick={() => { if (hasText) setConfirmation(target); else void act("apply"); }}>Use these captions</button>
        {confirmation === target && <div role="group" aria-label="Confirm caption draft replacement"><p>Replace the current caption draft? Style is preserved. This does not save captions.</p><button disabled={invalid} onClick={() => void act("apply", true)}>Replace draft with automatic captions</button><button onClick={() => setConfirmation(null)}>Cancel replacement</button></div>}
      </fieldset>
      <button disabled={blocked} onClick={() => void act("discard")}>Discard caption proposal</button>
      <p className="hint">Use these captions copies the reviewed text into the editor below. Save captions explicitly before rendering.</p>
    </>}
  </section>;
}
