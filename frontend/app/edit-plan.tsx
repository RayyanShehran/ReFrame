"use client";

import { useEffect, useRef, useState } from "react";

export type PlanState = { revision: number | null; ready: boolean; dirty: boolean; busy: boolean; duration?: number | null };
type Segment = { source_start_frame: number; source_end_frame: number; output_start_frame: number; output_end_frame: number };
type Plan = { schema_version: 1; fps: 30; revision: number; output_frames: number; footage_frames: number;
  requested_duration_seconds: number; segments: Segment[]; suggested_source_starts: number[]; merged_subframe_intervals: number };
type Result = { status: "empty" | "ready" | "stale"; plan: Plan | null; message: string | null; max_duration_seconds: number | null };
const apiBase = (process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
function parse(data: unknown): Result {
  const r = data as Result;
  if (!r || !["empty", "ready", "stale"].includes(r.status) ||
    (r.max_duration_seconds != null && (!Number.isFinite(r.max_duration_seconds) || r.max_duration_seconds <= 0))) throw new Error("Invalid cut plan response.");
  if (r.status !== "empty") {
    const p = r.plan;
    if (!p || p.schema_version !== 1 || p.fps !== 30 || !Number.isSafeInteger(p.revision) || p.revision < 1 ||
      !Number.isSafeInteger(p.output_frames) || p.output_frames < 1 || !Array.isArray(p.segments) || p.segments.length < 1 || p.segments.length > 60 ||
      !Array.isArray(p.suggested_source_starts) || p.suggested_source_starts.length !== p.segments.length ||
      !p.segments.every(s => [s.source_start_frame, s.source_end_frame, s.output_start_frame, s.output_end_frame].every(n => Number.isSafeInteger(n) && n >= 0) && s.source_end_frame > s.source_start_frame && s.output_end_frame > s.output_start_frame)) throw new Error("Invalid cut plan response.");
  }
  return r;
}
async function request(projectId: string, signal: AbortSignal, body?: unknown, generate = false): Promise<Result> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal.addEventListener("abort", abort, { once: true });
  if (signal.aborted) controller.abort();
  const timer = setTimeout(abort, 10000);
  try {
    const response = await fetch(`${apiBase}/api/projects/${encodeURIComponent(projectId)}/edit-plan${generate ? "/generate" : ""}`, {
      method: body ? "POST" : "GET", signal: controller.signal,
      ...(body ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data?.error?.message || "Cut plan request failed.");
    return parse(data);
  } finally { clearTimeout(timer); signal.removeEventListener("abort", abort); }
}

export function EditPlan({ projectId, recipeReady, onState }: { projectId: string; recipeReady: boolean; onState: (state: PlanState) => void }) {
  const [result, setResult] = useState<Result | null>(null);
  const [starts, setStarts] = useState<string[]>([]);
  const [duration, setDuration] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [confirm, setConfirm] = useState(false);
  const action = useRef<AbortController | null>(null);
  const plan = result?.plan;
  const dirty = !!plan && starts.some((s, i) => !s.trim() || Math.round(Number(s) * 30) !== plan.segments[i].source_start_frame);
  const valid = !!plan && starts.length === plan.segments.length && starts.every((s, i) => {
    const start = Math.round(Number(s) * 30), segment = plan.segments[i];
    const length = segment.output_end_frame - segment.output_start_frame;
    const previous = i ? Math.round(Number(starts[i - 1]) * 30) + plan.segments[i - 1].output_end_frame - plan.segments[i - 1].output_start_frame : 0;
    return !!s.trim() && Number.isFinite(start) && start >= previous && start + length <= plan.footage_frames;
  });
  const revision = plan?.revision ?? null, ready = result?.status === "ready";
  const outputDuration = plan ? plan.output_frames / 30 : null;
  useEffect(() => { onState({ revision, ready, dirty, busy, duration: outputDuration }); }, [onState, revision, ready, dirty, busy, outputDuration]);
  function restore(value: Result) {
    setResult(value); setError(""); setConfirm(false);
    setStarts(value.plan?.segments.map(s => String(s.source_start_frame / 30)) ?? []);
    setDuration(String(value.plan?.requested_duration_seconds ?? value.max_duration_seconds ?? ""));
  }
  useEffect(() => {
    const controller = new AbortController();
    void request(projectId, controller.signal).then(value => { if (!controller.signal.aborted) restore(value); })
      .catch(cause => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "Cut plan could not be loaded."); });
    return () => { controller.abort(); action.current?.abort(); };
  }, [projectId]);
  async function run(kind: "generate" | "save" | "reload") {
    if (action.current) return;
    const controller = new AbortController(); action.current = controller; setBusy(true); setError("");
    const body = kind === "generate" ? { expected_revision: revision ?? 0, replace: !!plan, ...(duration.trim() ? { output_duration_seconds: Number(duration) } : {}) }
      : kind === "save" ? { expected_revision: revision, source_starts_seconds: starts.map(Number) } : undefined;
    try {
      const value = await request(projectId, controller.signal, body, kind === "generate");
      if (!controller.signal.aborted) restore(value);
    } catch (cause) {
      if (!controller.signal.aborted) setError(cause instanceof Error && cause.name !== "AbortError" ? cause.message : "Request timed out. Reload explicitly to check saved state. Your edits are retained.");
    } finally { if (action.current === controller) { action.current = null; if (!controller.signal.aborted) setBusy(false); } }
  }
  const continuous = !!plan && starts.every((s, i) => !i || Math.round(Number(s) * 30) === Math.round(Number(starts[i - 1]) * 30) + plan.segments[i - 1].output_end_frame - plan.segments[i - 1].output_start_frame);
  return <section className="reference-section" aria-label="Cut plan">
    <h3>Reference-paced cut plan</h3>
    <p className="hint">An experimental timing suggestion from estimated reference shot lengths, not semantic matching. Review the footage ranges. No transitions are added. Audio and manual captions are saved separately below.</p>
    {!result && !error && <p role="status">Loading cut plan…</p>}
    {result?.message && <p role={ready ? "note" : "alert"}>{result.message}</p>}
    {!recipeReady && <p>Save a valid color recipe before generating or rendering cuts.</p>}
    {error && <p role="alert">{error}</p>}
    <label htmlFor={`duration-${projectId}`}>Requested output duration (seconds)
      <input id={`duration-${projectId}`} type="number" min={1 / 30} max={result?.max_duration_seconds ?? 120} step="any" value={duration} disabled={busy} onChange={e => setDuration(e.target.value)} />
    </label>
    <p className="hint">Default: the shorter of reference and footage. Total duration rounds down to 30 fps; boundaries round to nearest frame. Sub-frame intervals merge with a neighbor. At most 60 segments.</p>
    {result?.status === "empty" && <button disabled={busy || !recipeReady} onClick={() => void run("generate")}>Generate cut plan</button>}
    {plan && <>
      <p role="status">{ready ? dirty ? "Unsaved cut changes" : "Cut plan saved" : "Stale cut plan"} · Plan revision {plan.revision} · {plan.segments.length} segments · {(plan.output_frames / 30).toFixed(3)} seconds</p>
      {result?.status === "stale" && <p>Saved ranges remain available for inspection. Restore sources and regenerate explicitly before rendering cuts.</p>}
      {plan.merged_subframe_intervals > 0 && <p>{plan.merged_subframe_intervals} sub-frame intervals merged.</p>}
      <fieldset disabled={busy || !ready}>
        <legend>Source ranges in your footage</legend>
        <div className="cut-table"><table><thead><tr><th>Segment</th><th>Source start (s)</th><th>Source end (s)</th><th>Output length (s)</th></tr></thead>
          <tbody>{plan.segments.map((s, i) => <tr key={i}><td>{i + 1}</td><td><input type="number" aria-label={`Segment ${i + 1} source start`} min="0" max={plan.footage_frames / 30} step="any" value={starts[i] ?? ""}
            onChange={e => setStarts(previous => previous.map((value, j) => j === i ? e.target.value : value))} /></td>
            <td>{starts[i]?.trim() && Number.isFinite(Number(starts[i])) ? ((Math.round(Number(starts[i]) * 30) + s.output_end_frame - s.output_start_frame) / 30).toFixed(3) : "—"}</td>
            <td>{((s.output_end_frame - s.output_start_frame) / 30).toFixed(3)}</td></tr>)}</tbody></table></div>
        {dirty && !valid && <p role="alert">Ranges must fit your footage in chronological order without overlap.</p>}
        <button disabled={!dirty || !valid} onClick={() => void run("save")}>Save cut plan</button>
        <button onClick={() => setStarts(plan.suggested_source_starts.map(n => String(n / 30)))}>Reset cuts to suggestion</button>
      </fieldset>
      {continuous && <p role="note">These ranges are continuous. Segment boundaries alone do not create visible jumps; cuts become visible when footage is skipped.</p>}
      <button disabled={busy || !recipeReady} onClick={() => setConfirm(true)}>Regenerate cut plan</button>
      {confirm && <div role="group" aria-label="Confirm cut plan replacement"><p>This replaces the saved ranges and your edits using the requested duration above.</p>
        <button disabled={busy} onClick={() => void run("generate")}>Replace saved cut plan</button><button disabled={busy} onClick={() => setConfirm(false)}>Cancel cut regeneration</button></div>}
    </>}
    <button disabled={busy} onClick={() => void run("reload")}>{dirty ? "Discard cut changes and reload" : "Reload saved cut plan"}</button>
    {busy && <p role="status">Saving or loading cut plan…</p>}
  </section>;
}
