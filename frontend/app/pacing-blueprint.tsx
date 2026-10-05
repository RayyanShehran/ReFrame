"use client";

import { useMediaOperation, type MediaOperation } from "./use-media-operation";

type Blueprint = {
  schema_version: 1; algorithm_version: string; analyzed_at: string;
  source: { media_sha256: string }; tool_versions: Record<string, string>;
  duration_seconds: number; processing_width: number; processing_height: number;
  threshold: 10; successful_frames: number; candidate_cut_timestamps: number[];
  estimated_cut_count: number; shot_count: number; mean_shot_seconds: number;
  median_shot_seconds: number; cuts_per_minute: number;
  shots: { start_seconds: number; end_seconds: number; duration_seconds: number }[];
  interpretation_limits: string[]; color_metadata: { warnings: string[] };
  other_categories: Record<string, "not_analyzed">;
};
type Operation = MediaOperation & { blueprint: Blueprint | null };
const finite = (n: unknown): n is number => typeof n === "number" && Number.isFinite(n);
function parse(data: unknown): Operation {
  if (!data || typeof data !== "object" || !("status" in data) || !["idle", "running", "ready", "failed"].includes(String(data.status))) throw new Error("Invalid pacing response.");
  const operation = data as Operation;
  if (operation.status === "ready") {
    const b = operation.blueprint;
    if (!b || b.schema_version !== 1 || b.algorithm_version !== "scdet-consecutive-v1" || b.threshold !== 10 ||
        !finite(b.duration_seconds) || b.duration_seconds <= 0 || b.duration_seconds > 120 ||
        ![b.mean_shot_seconds, b.median_shot_seconds].every(n => finite(n) && n > 0 && n <= 120) ||
        !finite(b.cuts_per_minute) || b.cuts_per_minute < 0 ||
        ![b.processing_width, b.processing_height].every(n => Number.isInteger(n) && n >= 2 && n <= 320) ||
        !Number.isInteger(b.successful_frames) || b.successful_frames < 1 ||
        !Array.isArray(b.candidate_cut_timestamps) || b.candidate_cut_timestamps.length > 1000 ||
        !b.candidate_cut_timestamps.every((t, i, times) => finite(t) && t > 0 && t < b.duration_seconds && (i === 0 || t > times[i-1])) ||
        b.estimated_cut_count !== b.candidate_cut_timestamps.length || b.shot_count !== b.estimated_cut_count + 1 ||
        !Array.isArray(b.shots) || b.shots.length !== b.shot_count ||
        !b.shots.every((s, i, shots) => s && [s.start_seconds, s.end_seconds, s.duration_seconds].every(finite) &&
          s.start_seconds === (i === 0 ? 0 : shots[i-1].end_seconds) && s.end_seconds > s.start_seconds &&
          s.end_seconds <= b.duration_seconds && Math.abs(s.duration_seconds - (s.end_seconds-s.start_seconds)) < 1e-8) ||
        b.shots[b.shots.length-1].end_seconds !== b.duration_seconds ||
        !b.source || !/^[0-9a-f]{64}$/.test(b.source.media_sha256) || !b.tool_versions ||
        !Array.isArray(b.interpretation_limits) || !b.interpretation_limits.every(s => typeof s === "string") ||
        !b.color_metadata || !Array.isArray(b.color_metadata.warnings) || !b.color_metadata.warnings.every(s => typeof s === "string") ||
        !b.other_categories || !["transitions", "captions", "audio"].every(k => b.other_categories[k] === "not_analyzed")) throw new Error("Invalid pacing blueprint response.");
  }
  return operation;
}

export function PacingBlueprint({ projectId }: { projectId: string }) {
  const { operation, error, starting, start } = useMediaOperation(projectId, "pacing", parse);
  const b = operation?.blueprint;
  return <section className="reference-section" aria-label="Style Blueprint — estimated cuts and pacing">
    <h3>Optional reference pacing</h3>
    <p className="hint">Optional for whole-clip export. Read-only estimates from the retained reference. Transitions, captions and audio remain not analyzed.</p>
    {!operation && !error && <p role="status">Loading pacing status…</p>}
    {operation?.status === "idle" && <p>Pacing has not been analyzed. Color results are independent.</p>}
    {operation?.status === "running" && <p role="status">Checking consecutive reference frames for likely cuts… Processing continues if you leave.</p>}
    {operation?.status === "failed" && <p role="alert">{operation.message || "Pacing analysis failed."} ({operation.failure_code})</p>}
    {error && <p role="alert" className="error">{error}</p>}
    {operation && ["idle", "failed"].includes(operation.status) && <button disabled={starting} onClick={() => void start()}>{starting ? "Starting…" : operation.status === "failed" ? "Retry pacing analysis" : "Analyze pacing"}</button>}
    {operation?.status === "ready" && b && <>
      <p role="status">Estimated cuts and pacing are ready and saved locally.</p>
      <details><summary>Estimated pacing measurements and method</summary><dl className="clip-details">
        <dt>Estimated cut count</dt><dd>{b.estimated_cut_count}</dd>
        <dt>Estimated shot count</dt><dd>{b.shot_count}</dd>
        <dt>Mean shot length</dt><dd>{b.mean_shot_seconds.toFixed(3)} seconds</dd>
        <dt>Median shot length</dt><dd>{b.median_shot_seconds.toFixed(3)} seconds</dd>
        <dt>Cuts per minute</dt><dd>{b.cuts_per_minute.toFixed(2)}</dd>
        <dt>Processing coverage</dt><dd>{b.successful_frames} consecutive decoded frames across {b.duration_seconds} seconds; {b.processing_width} × {b.processing_height}, threshold {b.threshold}.</dd>
        <dt>Version</dt><dd>Schema {b.schema_version} · {b.algorithm_version}</dd>
        <dt>Tools</dt><dd>{Object.entries(b.tool_versions).map(([name, version]) => `${name}: ${version}`).join(" · ")}</dd>
        <dt>Source SHA-256</dt><dd style={{ overflowWrap: "anywhere" }}>{b.source.media_sha256}</dd>
        <dt>Analyzed</dt><dd>{b.analyzed_at}</dd>
      </dl></details>
      <details><summary>Estimated shot durations ({b.shot_count} shots)</summary>
        <div style={{ maxHeight: "16rem", overflow: "auto" }}><table>
          <caption>Contiguous estimated shots, in seconds</caption>
          <thead><tr><th scope="col">Shot</th><th scope="col">Start</th><th scope="col">End</th><th scope="col">Length</th></tr></thead>
          <tbody>{b.shots.map((s, i) => <tr key={i}><th scope="row">{i+1}</th><td>{s.start_seconds.toFixed(3)}</td><td>{s.end_seconds.toFixed(3)}</td><td>{s.duration_seconds.toFixed(3)}</td></tr>)}</tbody>
        </table></div>
      </details>
      <ul className="hint">{b.interpretation_limits.map(s => <li key={s}>{s}</li>)}</ul>
      {b.color_metadata.warnings.map(s => <p role="note" key={s}>{s}</p>)}
    </>}
  </section>;
}
