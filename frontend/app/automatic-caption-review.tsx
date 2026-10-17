"use client";
/* eslint-disable @next/next/no-img-element -- bounded server-owned PNG diagnostic, no optimizer/network source */

import { createPortal } from "react-dom";
import { useEffect, useState, type ReactNode } from "react";
import { apiBase } from "./clip-library";

export type ReviewCue = { start: number; end: number; text: string; appearance?: unknown };
export type CaptionReview = { cues: ReviewCue[]; unreadable_samples: number; verified_cues?: ReviewCue[]; corrected_cues?: ReviewCue[]; evidence?: { reference_operation_id: string } };
const same = (a: ReviewCue, b: ReviewCue) => a.start === b.start && a.end === b.end && a.text === b.text;
export function AutomaticCaptionReview({ projectId, revision, review, disabled, onSaved, onDirty, portalTarget = null, children }: { projectId: string; revision: number; review: CaptionReview; disabled: boolean; onSaved: () => void; onDirty: (dirty: boolean) => void; portalTarget?: HTMLDivElement | null; children?: ReactNode }) {
  const [issuesOnly, setIssuesOnly] = useState(false);
  const [cues, setCues] = useState(review.cues), [selected, setSelected] = useState(0);
  const [verified, setVerified] = useState(review.cues.map(c => !!review.verified_cues?.some(v => same(c, v))));
  const [image, setImage] = useState(""), [error, setError] = useState(""), [busy, setBusy] = useState(false), [dirty, setDirty] = useState(false);
  const [frameRevision, setFrameRevision] = useState(0);
  const cue = cues[selected];
  useEffect(() => {
    if (!cue || !review.evidence || disabled) return;
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 15000);
    void fetch(`${apiBase}/api/projects/${projectId}/reference-frame`, { method: "POST", signal: controller.signal, headers: { "Content-Type": "application/json" }, body: JSON.stringify({ timestamp_seconds: (cue.start + cue.end) / 2, expected_reference_operation_id: review.evidence.reference_operation_id }) }).then(async response => {
      const value = await response.json();
      if (!response.ok) throw new Error(value?.error?.message || "Reference frame unavailable.");
      if (value.reference_operation_id !== review.evidence?.reference_operation_id || typeof value.image?.png_base64 !== "string" || value.image.png_base64.length > 4 * 1024 * 1024) throw new Error("Reference frame changed or exceeded its limit.");
      if (!controller.signal.aborted) { setImage(`data:image/png;base64,${value.image.png_base64}`); setError(""); }
    }).catch(cause => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "Frame unavailable."); }).finally(() => clearTimeout(timer));
    return () => { controller.abort(); clearTimeout(timer); };
  }, [projectId, selected, review.evidence, disabled, frameRevision]); // eslint-disable-line react-hooks/exhaustive-deps
  function change(next: ReviewCue[], checked = verified) { setCues(next); setVerified(checked); setDirty(true); onDirty(true); setError(""); }
  async function save() {
    setBusy(true); setError("");
    try {
      const response = await fetch(`${apiBase}/api/projects/${projectId}/generation/caption-review`, { method: "POST", signal: AbortSignal.timeout(10000), headers: { "Content-Type": "application/json" }, body: JSON.stringify({ expected_revision: revision, cues, verified_indices: verified.flatMap((v, i) => v ? [i] : []), corrected_indices: cues.flatMap((c, i) => review.corrected_cues?.some(v => same(c,v)) || !review.cues.some(v => same(c,v)) ? [i] : []) }) });
      const value = await response.json();
      if (!response.ok) throw new Error(value?.error?.message || "Corrections could not be saved.");
      setDirty(false); onDirty(false); onSaved();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Save failed. Your draft is retained."); }
    finally { setBusy(false); }
  }
  const editor = <details className="automatic-caption-review"><summary>Correct uncertain words beside the reference</summary><p>These are incomplete readings. Correct only words you can see; keep unknown wording for review. Saved corrections survive retries, including words still awaiting verification.</p>
    {review.unreadable_samples > 0 && <p role="alert">Caption reading remains incomplete: {review.unreadable_samples} regions need review. These are not complete export captions.</p>}
    <label><input type="checkbox" checked={issuesOnly} onChange={e => setIssuesOnly(e.target.checked)} /> Show unverified proposal cues</label>
    <ol className="cue-list" aria-label="Recognition proposal cues">{cues.map((c, i) => (!issuesOnly || !verified[i]) && <li key={i}><button disabled={disabled || busy} aria-pressed={selected === i} onClick={() => { setSelected(i); setImage(""); }}><span>{c.start.toFixed(2)}–{c.end.toFixed(2)} s · {verified[i] ? "Verified" : "Needs review"}</span><span dir="auto">{c.text}</span></button></li>)}</ol>
    <div className="caption-correction-grid"><div>{image ? <img src={image} alt="Retained reference at this cue’s midpoint" /> : <p>Reference frame will appear here.</p>}<button disabled={disabled || busy || !review.evidence} onClick={() => { setImage(""); setError(""); setFrameRevision(v => v + 1); }}>Reload reference frame</button></div>{cue && <fieldset disabled={disabled || busy}><legend>Cue {selected + 1}</legend><label>Start (seconds)<input type="number" min="0" max="120" step="0.033333" value={cue.start} onChange={e => change(cues.map((c, i) => i === selected ? { ...c, start: Number(e.target.value) } : c), verified.map((v, i) => i === selected ? false : v))} /></label><label>End (seconds)<input type="number" min="0" max="120" step="0.033333" value={cue.end} onChange={e => change(cues.map((c, i) => i === selected ? { ...c, end: Number(e.target.value) } : c), verified.map((v, i) => i === selected ? false : v))} /></label><label>Visible words<textarea maxLength={200} rows={3} value={cue.text} onChange={e => change(cues.map((c, i) => i === selected ? { ...c, text: e.target.value } : c), verified.map((v, i) => i === selected ? false : v))} /></label><label><input type="checkbox" checked={verified[selected] ?? false} onChange={e => change(cues, verified.map((v, i) => i === selected ? e.target.checked : v))} /> I verified these words and timing against the reference</label><button onClick={() => { change(cues.filter((_, i) => i !== selected), verified.filter((_, i) => i !== selected)); setSelected(Math.max(0, selected - 1)); setImage(""); }}>Delete cue</button></fieldset>}</div>
    <button disabled={disabled || busy || cues.length >= 200} onClick={() => { const start = cues.at(-1)?.end ?? 0; change([...cues, { start, end: Math.min(120, start + 1), text: "" }], [...verified, false]); setSelected(cues.length); setImage(""); }}>Add missing cue</button><button disabled={disabled || busy || !dirty} onClick={() => void save()}>Save corrections</button><p role="status">{dirty ? "Unsaved corrections — save before retrying or exporting." : "Saved proposal; incomplete export still needs explicit confirmation."}</p>{error && <p role="alert">{error}</p>}
    {children}
  </details>;
  return portalTarget ? createPortal(editor, portalTarget) : editor;
}
