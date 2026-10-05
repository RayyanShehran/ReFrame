"use client";

import { MediaOperation, useMediaOperation } from "./use-media-operation";
import { useEffect, useState } from "react";
import { sections, useWorkspaceNavigation, type Section } from "./guided-workspace";
import { PlanState } from "./edit-plan";
import { AudioState, audioModeLabels } from "./audio-choices";
import { CaptionState } from "./caption-editor";
import { FramingState, formatLabels } from "./framing-controls";

type Spec = { recipe_revision: number; edit_plan?: { revision: number } | null; audio?: { revision: number; mode: keyof typeof audioModeLabels }; captions?: { revision: number; enabled: boolean }; framing?: { revision: number; format: keyof typeof formatLabels } };
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
    if (o.spec.framing && (!Object.hasOwn(formatLabels, o.spec.framing.format) || !Number.isSafeInteger(o.spec.framing.revision) || o.spec.framing.revision < 0)) throw new Error("Invalid rendered framing response.");
    if (o.spec.captions && (!Number.isSafeInteger(o.spec.captions.revision) || o.spec.captions.revision < 0 || typeof o.spec.captions.enabled !== "boolean")) throw new Error("Invalid rendered captions response.");
  }
  return value;
}

export function RenderVideo({ savedStrength, savedValues, projectId, revision, recipeReady, dirty, busy, planState, audioState = { revision: 0, ready: true, dirty: false, busy: false }, framingState = { revision: 0, ready: true, dirty: false, busy: false }, captionState = { revision: 0, ready: true, dirty: false, busy: false, enabled: false, mode: null, planRevision: null } }: { savedStrength?: number; savedValues?: { brightness: number; contrast: number; saturation: number }; projectId: string; revision: number | null; recipeReady: boolean; dirty: boolean; busy: boolean; planState?: PlanState; audioState?: AudioState; captionState?: CaptionState; framingState?: FramingState }) {
  const [mode, setMode] = useState("whole");
  const cuts = mode === "cuts";
  const captionMatches = !captionState.enabled || (captionState.mode === mode && (!cuts || captionState.planRevision === planState?.revision));
  const { operation, error, starting, start } = useMediaOperation(projectId, "render", parse, 360);
  const output = operation?.output;
  const outdated = !!output && (operation?.outdated || !recipeReady || output.spec.recipe_revision !== revision ||
    !framingState.ready || (output.spec.framing?.revision ?? 0) !== framingState.revision ||
    !captionState.ready || (output.spec.captions?.revision ?? 0) !== captionState.revision ||
    !audioState.ready || (output.spec.audio?.revision ?? 0) !== audioState.revision ||
    (output.spec.edit_plan && (!planState?.ready || output.spec.edit_plan.revision !== planState.revision)));
  const running = operation?.status === "running";
  const current = operation?.status === "ready" && !!output && !outdated && !!output.spec.edit_plan === cuts;
  const navigation = useWorkspaceNavigation();
  const next = navigation?.next;
  const blockers: { section: Section; message: string }[] = [];
  const block = (condition: unknown, section: Section, message: string) => { if (condition) blockers.push({ section, message }); };
  block(!recipeReady || !revision, "style", "Generate or regenerate a valid color recipe before rendering.");
  block(dirty, "style", "Save your unsaved recipe changes before rendering.");
  block(busy, "style", "Wait for the recipe operation to finish.");
  block(cuts && !planState?.ready, "style", "Generate or regenerate a valid saved cut plan before rendering cuts.");
  block(cuts && planState?.dirty, "style", "Save your unsaved cut changes before rendering.");
  block(cuts && planState?.busy, "style", "Wait for the cut plan operation to finish.");
  block(!audioState.ready, "audio", "Load and save valid audio choices before rendering.");
  block(audioState.dirty, "audio", "Save your unsaved audio changes before rendering.");
  block(audioState.busy, "audio", "Wait for the audio operation to finish.");
  block(!captionState.ready, "audio", "Load and save valid captions, or disable them, before rendering.");
  block(captionState.dirty, "audio", "Save your unsaved caption changes before rendering.");
  block(captionState.busy, "audio", "Wait for the caption operation to finish.");
  block(!captionMatches, "audio", "Explicitly rebind and save captions for this render mode in the Captions panel.");
  block(!framingState.ready, "export", "Load valid saved framing before rendering.");
  block(framingState.dirty, "export", "Save your unsaved framing changes before rendering.");
  block(framingState.busy, "export", "Wait for the framing operation to finish.");
  const actionSection = blockers[0]?.section ?? "export";
  const actionLabel = blockers[0]?.message ?? (running || starting ? "View rendering progress" : current ? "Play or download your video" : "Review settings and render");
  useEffect(() => { next?.({ section: actionSection, label: actionLabel }); }, [next, actionSection, actionLabel]);
  const changed = output ? [
    output.spec.recipe_revision !== revision && revision !== null && "color recipe",
    (output.spec.audio?.revision ?? 0) !== audioState.revision && audioState.revision !== null && "audio",
    (output.spec.captions?.revision ?? 0) !== captionState.revision && captionState.revision !== null && "captions",
    (output.spec.framing?.revision ?? 0) !== framingState.revision && framingState.revision !== null && "framing",
    output.spec.edit_plan && planState?.revision != null && output.spec.edit_plan.revision !== planState.revision && "cut plan",
  ].filter(Boolean) : [];
  const url = output ? `${apiBase}/api/projects/${encodeURIComponent(projectId)}/outputs/${output.output_id}` : "";
  return <section className="reference-section" aria-label="Rendered video">
    <h3>Rendered video</h3>
    <p className="hint">Uses the saved color recipe, framing, audio choices and optional captions, with optional saved cuts. No transitions. These controls have no live preview; render to see the actual result.</p>
    <label>Render mode <select value={mode} onChange={e => setMode(e.target.value)} disabled={starting || running}>
      <option value="whole">Whole clip (color + saved audio)</option><option value="cuts">Saved cut plan (cuts + color)</option></select></label>
    <div className="export-summary" aria-label="Saved export settings">
      <h4>Saved settings for this export</h4>
      <p>{cuts ? `Saved cut plan · Revision ${planState?.revision ?? "not saved"}` : "Whole clip · Cuts optional"}</p>
      <p>Color recipe: {revision ? `revision ${revision}` : "not saved"}{savedStrength !== undefined ? ` · Strength ${Math.round(savedStrength * 100)}%` : ""}</p>
      {savedValues && <p className="hint">Brightness {savedValues.brightness.toFixed(2)} · Contrast {savedValues.contrast.toFixed(2)} · Saturation {savedValues.saturation.toFixed(2)}</p>}
      <p>Saved audio: {audioState.ready ? audioModeLabels[audioState.mode ?? "original"] : "unavailable"} · Revision {audioState.revision ?? "loading"}</p>
      <p>Saved captions: {captionState.enabled ? "enabled" : "disabled"} · Revision {captionState.revision ?? "loading"}</p>
      <p>Saved format: {framingState.ready ? formatLabels[framingState.format ?? "original"] : "loading"}{framingState.dimensions ? ` · ${framingState.dimensions}` : ""}</p>
      {framingState.format && framingState.format !== "original" && <p>Saved fit: {framingState.fit === "fill" ? "Fill (crop edges)" : "Fit (whole image)"}</p>}
      {(dirty || audioState.dirty || captionState.dirty || framingState.dirty || (cuts && planState?.dirty)) && <p role="status">Unsaved drafts are not included. Save the changes below before rendering.</p>}
    </div>
    {blockers.map(({ section, message }) => <p key={message} role="status">{message}{navigation && <> <button onClick={() => navigation.open(section)}>Open {sections[section]}</button></>}</p>)}
    <button disabled={!!blockers.length || starting || running || current}
      onClick={() => void start({ expected_revision: revision, expected_audio_revision: audioState.revision, expected_caption_revision: captionState.revision, expected_framing_revision: framingState.revision, ...(cuts ? { expected_plan_revision: planState?.revision } : {}) })}>{operation?.status === "failed" ? "Retry render" : "Render video"}</button>
    {running && <p role="status">Rendering saved recipe revision {operation.spec?.recipe_revision}{operation.spec?.edit_plan ? ` and cut plan revision ${operation.spec.edit_plan.revision}` : ""}… This can take up to five minutes.</p>}
    {error && <p role="alert">{error}</p>}
    {operation?.status === "failed" && <p role="alert">{operation.message || "Rendering failed. Retry explicitly."}</p>}
    {output && <>
      <p role="status">{outdated ? "Outdated output" : "Rendered output"} · Recipe revision {output.spec.recipe_revision} · {output.width} × {output.height} · {output.duration_seconds.toFixed(2)} seconds</p>
      <p>{output.spec.edit_plan ? `Cut plan revision ${output.spec.edit_plan.revision}` : "Whole clip"}</p>
      <p>{audioModeLabels[output.spec.audio?.mode ?? "original"]} · Audio revision {output.spec.audio?.revision ?? 0}</p>
      <p>{formatLabels[output.spec.framing?.format ?? "original"]} · Framing revision {output.spec.framing?.revision ?? 0}</p>
      <p>Captions {output.spec.captions?.enabled ? "enabled" : "disabled"} · Caption revision {output.spec.captions?.revision ?? 0}</p>
      {outdated && <p className="hint">{changed.length ? `Saved ${changed.join(", ")} changed since this output.` : "The saved settings or source no longer match this output."} This output stays playable and downloadable until replacement succeeds.</p>}
      <video key={output.output_id} className="rendered-video" controls preload="metadata" src={`${url}/video`} aria-label={`Rendered video, recipe revision ${output.spec.recipe_revision}`} />
      <p><a className="download-link" href={`${url}/download`}>Download MP4</a> · {(output.size_bytes / 1024 / 1024).toFixed(2)} MiB</p>
    </>}
    {!operation && !error && <p role="status">Loading render status…</p>}
  </section>;
}
