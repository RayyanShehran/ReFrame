"use client";

import { useEffect } from "react";
import { useMediaOperation, type MediaOperation } from "./use-media-operation";

export type Blueprint = {
  schema_version: 1; algorithm_version: string; analyzed_at: string;
  source: { media_sha256: string; video_id?: string; clip_id?: string };
  tool_versions: Record<string, string>;
  sampling: { successful_samples: number; timestamps_seconds: number[]; duration_seconds: number; frame_width: number; frame_height: number; decoded_bytes: number };
  color_metadata: { warnings: string[]; assumption: string };
  color: { rgb_mean: number[]; brightness_p05: number; brightness_p50: number; brightness_p95: number; contrast_spread: number; mean_hsv_saturation: number; palette_coverage: number; palette: { hex: string; proportion: number }[] };
  interpretation_limits: string[];
  other_categories: Record<string, "not_analyzed">;
};
type Operation = MediaOperation & { blueprint: Blueprint | null };
const unit = (value: unknown) => typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1;

function parse(data: unknown): Operation {
  if (!data || typeof data !== "object" || !("status" in data) || !["idle", "running", "ready", "failed"].includes(String(data.status))) throw new Error("Invalid color analysis response.");
  const operation = data as Operation;
  if (operation.status === "ready") {
    const b = operation.blueprint;
    if (!b || b.schema_version !== 1 || !b.color || !b.sampling || !b.color_metadata ||
        !Array.isArray(b.color.rgb_mean) || b.color.rgb_mean.length !== 3 || !b.color.rgb_mean.every(unit) ||
        ![b.color.brightness_p05, b.color.brightness_p50, b.color.brightness_p95, b.color.contrast_spread, b.color.mean_hsv_saturation, b.color.palette_coverage].every(unit) ||
        !Array.isArray(b.color.palette) || b.color.palette.length < 1 || b.color.palette.length > 5 ||
        !b.color.palette.every(c => c && /^#[0-9a-f]{6}$/.test(c.hex) && unit(c.proportion)) ||
        b.sampling.successful_samples !== 12 || !Array.isArray(b.sampling.timestamps_seconds) || b.sampling.timestamps_seconds.length !== 12 ||
        !Array.isArray(b.color_metadata.warnings) || !Array.isArray(b.interpretation_limits) ||
        !b.source || typeof b.source.media_sha256 !== "string" || !b.tool_versions || !b.other_categories ||
        !["pacing", "transitions", "captions", "audio"].every(category => b.other_categories[category] === "not_analyzed")) throw new Error("Invalid color blueprint response.");
  }
  return operation;
}
const percent = (value: number) => `${(value * 100).toFixed(1)}%`;

export function StyleBlueprint({ projectId, footage = false, onChange }: { projectId: string; footage?: boolean; onChange?: (blueprint: Blueprint | null) => void }) {
  const { operation, error, starting, start } = useMediaOperation(projectId, footage ? "footage-color" : "style-blueprint", parse);
  const b = operation?.blueprint;
  useEffect(() => { onChange?.(!error && operation?.status === "ready" ? operation.blueprint : null); }, [operation, error, onChange]);
  const subject = footage ? "Your footage" : "Style Blueprint";
  return <section className="reference-section" aria-label={`${subject} — color analysis`}>
    <h3>{subject} — color analysis</h3>
    <p className="hint">{footage ? "Read-only color observations. Transitions, captions and audio remain not analyzed." : "Read-only color observations. Pacing has a separate analysis below; transitions, captions and audio remain not analyzed."}</p>
    {!operation && !error && <p role="status">Loading color analysis…</p>}
    {operation?.status === "idle" && <p>The saved media is ready for color analysis.</p>}
    {operation?.status === "running" && <p role="status">Sampling {footage ? "footage" : "reference"} frames and measuring color… Processing continues if you leave.</p>}
    {operation?.status === "failed" && <p role="alert">{operation.message || "Color analysis failed."} ({operation.failure_code})</p>}
    {error && <p role="alert" className="error">{error}</p>}
    {operation && ["idle", "failed"].includes(operation.status) && <button disabled={starting} onClick={() => void start()}>{starting ? "Starting…" : operation.status === "failed" ? footage ? "Retry footage analysis" : "Retry color analysis" : footage ? "Analyze my footage" : "Analyze reference"}</button>}
    {operation?.status === "ready" && b && <>
      <p role="status">Color analysis is ready and saved locally.</p>
      <details><summary>Color measurements and sampling details</summary><h4>Representative palette</h4>
      <ul className="palette">{b.color.palette.map(c => <li key={c.hex}>
        <span aria-hidden="true" className="palette-swatch" style={{ backgroundColor: c.hex }} />
        <span>{c.hex} · {percent(c.proportion)} of sampled pixels</span>
      </li>)}</ul>
      <p className="hint">Displayed colors cover {percent(b.color.palette_coverage)} of all sampled pixels. Other color bins are not displayed.</p>
      <dl className="clip-details">
        <dt>Mean RGB channels</dt><dd>R {percent(b.color.rgb_mean[0])} · G {percent(b.color.rgb_mean[1])} · B {percent(b.color.rgb_mean[2])}</dd>
        <dt>Encoded-pixel brightness</dt><dd>5th: {percent(b.color.brightness_p05)} · Median: {percent(b.color.brightness_p50)} · 95th: {percent(b.color.brightness_p95)}</dd>
        <dt>Contrast spread (95th − 5th)</dt><dd>{percent(b.color.contrast_spread)}</dd>
        <dt>Mean HSV saturation</dt><dd>{percent(b.color.mean_hsv_saturation)}</dd>
        <dt>Sampling coverage</dt><dd>{b.sampling.successful_samples} / 12 midpoint samples across {b.sampling.duration_seconds} seconds; {b.sampling.frame_width} × {b.sampling.frame_height} RGB frames, equal pixel and sample weighting.</dd>
        <dt>Target times</dt><dd>{b.sampling.timestamps_seconds.map(t => t.toFixed(3)).join(", ")} seconds</dd>
        <dt>Source SHA-256</dt><dd style={{ overflowWrap: "anywhere" }}>{b.source.media_sha256}</dd>
        <dt>Version</dt><dd>Schema {b.schema_version} · {b.algorithm_version}</dd>
        <dt>Analyzed</dt><dd>{b.analyzed_at}</dd>
        <dt>Tools</dt><dd>{Object.entries(b.tool_versions).map(([name, version]) => `${name}: ${version}`).join(" · ")}</dd>
      </dl></details>
      {b.color_metadata.warnings.map(w => <p role="note" className="error" key={w}>{w}</p>)}
      <p className="hint">{b.color_metadata.assumption}</p>
      <ul className="hint">{b.interpretation_limits.map(limit => <li key={limit}>{limit}</li>)}</ul>
      <p>These measurements are read-only. Saved color settings, cut planning and rendering are separate below. Semantic footage matching is not implemented.</p>
    </>}
  </section>;
}
