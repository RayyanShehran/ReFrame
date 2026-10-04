"use client";

import { MediaOperation, useMediaOperation } from "./use-media-operation";
import { useState } from "react";
import { PlanState } from "./edit-plan";
import { AudioState, audioModeLabels } from "./audio-choices";

type Spec = { recipe_revision: number; edit_plan?: { revision: number } | null; audio?: { revision: number; mode: keyof typeof audioModeLabels } };
type Output = { output_id: string; spec: Spec; width: number; height: number; duration_seconds: number; size_bytes: number };
type Operation = MediaOperation & { output: Output | null; spec: Spec | null; outdated: boolean };
const apiBase = (process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
function parse(data: unknown): Operation {
  const value = data as Operation;
  if (!value || !["idle", "running", "ready", "failed"].includes(value.status) || typeof value.outdated !== "boolean") throw new Error("Invalid render response.");
  if (value.output) {
    const o = value.output;
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(o.output_id) ||
      !Number.isSafeInteger(o.spec?.recipe_revision) || o.spec.recipe_revision < 1 ||
      ![o.width, o.height, o.duration_seconds, o.size_bytes].every(n => Number.isFinite(n) && n > 0)) throw new Error("Invalid rendered video response.");
    if (o.spec.audio && (!Object.hasOwn(audioModeLabels, o.spec.audio.mode) || !Number.isSafeInteger(o.spec.audio.revision) || o.spec.audio.revision < 0)) throw new Error("Invalid rendered audio response.");
  }
  return value;
}

export function RenderVideo({ projectId, revision, recipeReady, dirty, busy, planState, audioState = { revision: 0, ready: true, dirty: false, busy: false } }: { projectId: string; revision: number | null; recipeReady: boolean; dirty: boolean; busy: boolean; planState?: PlanState; audioState?: AudioState }) {
  const [mode, setMode] = useState("whole");
  const cuts = mode === "cuts";
  const { operation, error, starting, start } = useMediaOperation(projectId, "render", parse, 360);
  const output = operation?.output;
  const outdated = !!output && (operation?.outdated || !recipeReady || output.spec.recipe_revision !== revision ||
    !audioState.ready || (output.spec.audio?.revision ?? 0) !== audioState.revision ||
    (output.spec.edit_plan && (!planState?.ready || output.spec.edit_plan.revision !== planState.revision)));
  const running = operation?.status === "running";
  const current = operation?.status === "ready" && !!output && !outdated && !!output.spec.edit_plan === cuts;
  const url = output ? `${apiBase}/api/projects/${encodeURIComponent(projectId)}/outputs/${output.output_id}` : "";
  return <section className="reference-section" aria-label="Rendered video">
    <h3>Rendered video</h3>
    <p className="hint">Uses the saved color recipe and audio choices, with optional saved cuts. No captions or transitions. These controls have no live preview; render to see the actual result.</p>
    <label>Render mode <select value={mode} onChange={e => setMode(e.target.value)} disabled={starting || running}>
      <option value="whole">Whole clip (color only)</option><option value="cuts">Saved cut plan (cuts + color)</option></select></label>
    {cuts && !planState?.ready && <p>Generate or regenerate a valid saved cut plan before rendering cuts.</p>}
    {cuts && planState?.dirty && <p role="status">Save your unsaved cut changes before rendering.</p>}
    {dirty && <p role="status">Save your unsaved recipe changes before rendering.</p>}
    {audioState.dirty && <p role="status">Save your unsaved audio changes before rendering.</p>}
    {!audioState.ready && <p>Load and save valid audio choices before rendering.</p>}
    {!recipeReady && <p>Generate or regenerate a valid color recipe before rendering.</p>}
    <button disabled={!revision || !recipeReady || dirty || busy || !audioState.ready || audioState.dirty || audioState.busy || starting || running || current || (cuts && (!planState?.ready || planState.dirty || planState.busy))}
      onClick={() => void start({ expected_revision: revision, expected_audio_revision: audioState.revision, ...(cuts ? { expected_plan_revision: planState?.revision } : {}) })}>{operation?.status === "failed" ? "Retry render" : "Render video"}</button>
    {running && <p role="status">Rendering saved recipe revision {operation.spec?.recipe_revision}{operation.spec?.edit_plan ? ` and cut plan revision ${operation.spec.edit_plan.revision}` : ""}… This can take up to five minutes.</p>}
    {error && <p role="alert">{error}</p>}
    {operation?.status === "failed" && <p role="alert">{operation.message || "Rendering failed. Retry explicitly."}</p>}
    {output && <>
      <p role="status">{outdated ? "Outdated output" : "Rendered output"} · Recipe revision {output.spec.recipe_revision} · {output.width} × {output.height} · {output.duration_seconds.toFixed(2)} seconds</p>
      <p>{output.spec.edit_plan ? `Cut plan revision ${output.spec.edit_plan.revision}` : "Whole clip (color only)"}</p>
      <p>{audioModeLabels[output.spec.audio?.mode ?? "original"]} · Audio revision {output.spec.audio?.revision ?? 0}</p>
      {outdated && <p className="hint">This video uses an older saved recipe, cut plan, audio choices or source. Save valid settings and render again to replace it.</p>}
      <video key={output.output_id} className="rendered-video" controls preload="metadata" src={`${url}/video`} aria-label={`Rendered video, recipe revision ${output.spec.recipe_revision}`} />
      <p><a className="download-link" href={`${url}/download`}>Download MP4</a> · {(output.size_bytes / 1024 / 1024).toFixed(2)} MiB</p>
    </>}
    {!operation && !error && <p role="status">Loading render status…</p>}
  </section>;
}
