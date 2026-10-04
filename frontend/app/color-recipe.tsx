"use client";

import { useEffect, useRef, useState } from "react";
import { RenderVideo } from "./render-video";
import { EditPlan, PlanState } from "./edit-plan";
import { AudioChoices, AudioState } from "./audio-choices";

type Values = { brightness: number; contrast: number; saturation: number };
type Recipe = { schema_version: 1; suggestion_algorithm_version: string; suggested: Values;
  selected: Values; strength: number; revision: number; updated_at: string; explanations: string[] };
type Result = { status: "empty" | "ready" | "stale"; recipe: Recipe | null; message: string | null; effective: Values | null };
const neutral: Values = { brightness: 0, contrast: 1, saturation: 1 };
const fields = [
  { name: "brightness", label: "Brightness offset", min: -.2, max: .2, step: .01 },
  { name: "contrast", label: "Contrast multiplier", min: .5, max: 1.5, step: .01 },
  { name: "saturation", label: "Saturation multiplier", min: .5, max: 1.5, step: .01 },
] as const;
const apiBase = (process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
const validNumber = (n: unknown, min: number, max: number) => typeof n === "number" && Number.isFinite(n) && n >= min && n <= max;
function validValues(v: Values | null) { return !!v && fields.every(f => validNumber(v[f.name], f.min, f.max)); }
function parse(data: unknown): Result {
  const result = data as Result;
  if (!result || !["empty", "ready", "stale"].includes(result.status)) throw new Error("Invalid recipe response.");
  if (result.status !== "empty") {
    const r = result.recipe;
    if (!r || r.schema_version !== 1 || typeof r.suggestion_algorithm_version !== "string" ||
      !validValues(r.selected) || !validValues(r.suggested) || !validNumber(r.strength, 0, 1) ||
      !Number.isSafeInteger(r.revision) || r.revision < 1 || typeof r.updated_at !== "string" ||
      !Array.isArray(r.explanations) || !r.explanations.every(x => typeof x === "string") ||
      (result.status === "ready" && !validValues(result.effective))) throw new Error("Invalid recipe response.");
  }
  return result;
}
async function request(projectId: string, signal: AbortSignal, body?: unknown, generate = false): Promise<Result> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal.addEventListener("abort", abort, { once: true });
  if (signal.aborted) controller.abort();
  const timer = setTimeout(abort, 10000);
  try {
    const response = await fetch(`${apiBase}/api/projects/${encodeURIComponent(projectId)}/color-recipe${generate ? "/generate" : ""}`, {
      method: body ? "POST" : "GET", signal: controller.signal,
      ...(body ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}),
    });
    const data = await response.json();
    if (!response.ok) throw Object.assign(new Error(data?.error?.message || "Recipe request failed."), { code: data?.error?.code });
    return parse(data);
  } finally { clearTimeout(timer); signal.removeEventListener("abort", abort); }
}

export function ColorRecipe({ projectId, analysesReady }: { projectId: string; analysesReady: boolean }) {
  const [result, setResult] = useState<Result | null>(null);
  const [draft, setDraft] = useState({ selected: neutral, strength: .5 });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [confirmRegenerate, setConfirmRegenerate] = useState(false);
  const [planState, setPlanState] = useState<PlanState>({ revision: null, ready: false, dirty: false, busy: false });
  const [audioState, setAudioState] = useState<AudioState>({ revision: null, ready: false, dirty: false, busy: false });
  const action = useRef<AbortController | null>(null);
  const recipe = result?.recipe;
  const dirty = !!recipe && (fields.some(f => draft.selected[f.name] !== recipe.selected[f.name]) || draft.strength !== recipe.strength);
  function restore(value: Result) {
    setResult(value); setError(""); setConfirmRegenerate(false);
    if (value.recipe) setDraft({ selected: value.recipe.selected, strength: value.recipe.strength });
  }
  useEffect(() => {
    const controller = new AbortController();
    void request(projectId, controller.signal).then(value => {
      if (!controller.signal.aborted) {
        setResult(value);
        if (value.recipe) setDraft({ selected: value.recipe.selected, strength: value.recipe.strength });
      }
    }).catch(cause => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "Recipe could not be loaded."); });
    return () => { controller.abort(); action.current?.abort(); };
  }, [projectId]);

  async function run(kind: "generate" | "save" | "reload") {
    if (action.current) return;
    const controller = new AbortController(); action.current = controller;
    setBusy(true); setError("");
    const revision = recipe?.revision || 0;
    const body = kind === "generate" ? { expected_revision: revision, replace: !!recipe }
      : kind === "save" ? { expected_revision: revision, ...draft } : undefined;
    try {
      const value = await request(projectId, controller.signal, body, kind === "generate");
      if (!controller.signal.aborted) restore(value);
    } catch (cause) {
      if (!controller.signal.aborted) {
        setError(cause instanceof Error && cause.name !== "AbortError" ? cause.message : "Request timed out. Reload the saved recipe before retrying.");
        if (cause instanceof Error && "code" in cause && cause.code === "recipe_stale") {
          try { const value = await request(projectId, controller.signal); if (!controller.signal.aborted) setResult(value); } catch { /* Keep the saved draft and original safe error. */ }
        }
      }
    } finally { if (action.current === controller) { action.current = null; if (!controller.signal.aborted) setBusy(false); } }
  }

  return <section className="reference-section" aria-label="Color recipe">
    <h3>Color recipe</h3>
    <p className="hint">Experimental creative settings, not exposure stops, recovered LUTs or a guarantee of matching appearance. White balance is not inferred. Changing these controls does not produce a live video preview. Save, then render to see the result.</p>
    {!result && !error && <p role="status">Loading recipe…</p>}
    {!analysesReady && <p>Analyze valid reference and footage colors before generating or regenerating a suggestion.</p>}
    {error && <p role="alert">{error}</p>}
    {result?.status === "empty" && <button disabled={busy || !analysesReady} onClick={() => void run("generate")}>Generate suggestion</button>}
    {result?.status === "stale" && <p role="alert">{result.message || "Recipe is stale."} Saved settings remain available for inspection; explicit regeneration is required before future rendering.</p>}
    {recipe && <>
      <p role="status">{result?.status === "ready" ? dirty ? "Unsaved changes" : "Recipe saved" : "Stale saved recipe"} · Revision {recipe.revision}</p>
      <fieldset className="recipe-controls" disabled={busy || result?.status !== "ready"}>
        <legend>Selected settings</legend>
        {fields.map(f => <label key={f.name} htmlFor={`recipe-${projectId}-${f.name}`}>{f.label}: <output>{draft.selected[f.name].toFixed(2)}</output>
          <input id={`recipe-${projectId}-${f.name}`} type="range" min={f.min} max={f.max} step={f.step} value={draft.selected[f.name]}
            onChange={event => setDraft(previous => ({ ...previous, selected: { ...previous.selected, [f.name]: Number(event.target.value) } }))} />
        </label>)}
        <label htmlFor={`recipe-${projectId}-strength`}>Strength: <output>{Math.round(draft.strength * 100)}%</output>
          <input id={`recipe-${projectId}-strength`} type="range" min="0" max="100" step="1" value={Math.round(draft.strength * 100)} onChange={event => setDraft(previous => ({ ...previous, strength: Number(event.target.value) / 100 }))} />
        </label>
        <button disabled={!dirty} onClick={() => void run("save")}>Save recipe</button>
        <button onClick={() => setDraft({ selected: recipe.suggested, strength: .5 })}>Reset to suggestion</button>
        <button onClick={() => setDraft({ selected: neutral, strength: 0 })}>Reset to neutral</button>
      </fieldset>
      <p className="hint">Saved {recipe.updated_at} · {recipe.suggestion_algorithm_version}</p>
      {recipe.explanations.map(note => <p role="note" key={note}>{note}</p>)}
      <button disabled={busy || !analysesReady} onClick={() => setConfirmRegenerate(true)}>Regenerate suggestion</button>
      {confirmRegenerate && <div role="group" aria-label="Confirm recipe replacement"><p>Regeneration replaces the saved recipe and your edits with a new suggestion at 50% strength.</p>
        <button disabled={busy} onClick={() => void run("generate")}>Replace saved recipe</button><button disabled={busy} onClick={() => setConfirmRegenerate(false)}>Cancel regeneration</button>
      </div>}
    </>}
    <button disabled={busy} onClick={() => void run("reload")}>{dirty ? "Discard changes and reload" : "Reload saved recipe"}</button>
    {busy && <p role="status">Saving or loading recipe…</p>}
    <EditPlan key={`plan-${projectId}`} projectId={projectId} recipeReady={result?.status === "ready" && !dirty && !busy} onState={setPlanState} />
    <AudioChoices key={`audio-${projectId}`} projectId={projectId} onState={setAudioState} />
    <RenderVideo projectId={projectId} revision={recipe?.revision ?? null} recipeReady={result?.status === "ready"} dirty={dirty} busy={busy} planState={planState} audioState={audioState} />
  </section>;
}
