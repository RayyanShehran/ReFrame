"use client";

import { useEffect, useRef, useState } from "react";

export type AudioState = { revision: number | null; ready: boolean; dirty: boolean; busy: boolean; mode?: Mode };
type Mode = "original" | "reference" | "mix" | "mute";
type Choices = { mode: Mode; original_volume: number; reference_volume: number; reference_offset_seconds: number };
type Result = { status: "default" | "ready" | "stale"; settings: Choices & { schema_version: 1; revision: number };
  availability: { original_has_audio: boolean; reference_has_audio: boolean; reference_duration_seconds: number | null }; message: string | null };
const defaults: Choices = { mode: "original", original_volume: 100, reference_volume: 100, reference_offset_seconds: 0 };
const apiBase = (process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
export const audioModeLabels = { original: "Original footage audio", reference: "Reference audio", mix: "Mix", mute: "Mute" };
const finite = (v: unknown, max: number) => typeof v === "number" && Number.isFinite(v) && v >= 0 && v <= max;
function parse(data: unknown): Result {
  const r = data as Result, s = r?.settings, a = r?.availability;
  if (!r || !["default", "ready", "stale"].includes(r.status) || !s || s.schema_version !== 1 ||
    !Object.hasOwn(audioModeLabels, s.mode) || !Number.isSafeInteger(s.revision) || s.revision < 0 ||
    !finite(s.original_volume, 100) || !finite(s.reference_volume, 100) || !finite(s.reference_offset_seconds, 120) ||
    !a || typeof a.original_has_audio !== "boolean" || typeof a.reference_has_audio !== "boolean" ||
    (a.reference_duration_seconds !== null && (!finite(a.reference_duration_seconds, 120.1) || a.reference_duration_seconds <= 0))) throw new Error("Invalid audio settings response.");
  return r;
}
async function request(projectId: string, signal: AbortSignal, body?: unknown): Promise<Result> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal.addEventListener("abort", abort, { once: true });
  if (signal.aborted) controller.abort();
  const timer = setTimeout(abort, 10000);
  try {
    const response = await fetch(`${apiBase}/api/projects/${encodeURIComponent(projectId)}/audio`, {
      method: body ? "POST" : "GET", signal: controller.signal,
      ...(body ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}),
    });
    const data = await response.json();
    if (!response.ok) throw Object.assign(new Error(data?.error?.message || "Audio request failed."), { code: data?.error?.code });
    return parse(data);
  } finally { clearTimeout(timer); signal.removeEventListener("abort", abort); }
}
function choices(s: Choices): Choices {
  return { mode: s.mode, original_volume: s.original_volume, reference_volume: s.reference_volume, reference_offset_seconds: s.reference_offset_seconds };
}

export function AudioChoices({ projectId, onState }: { projectId: string; onState: (state: AudioState) => void }) {
  const [result, setResult] = useState<Result | null>(null);
  const [draft, setDraft] = useState<Choices>(defaults);
  const [offset, setOffset] = useState("0");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const action = useRef<AbortController | null>(null);
  const mixedBefore = useRef(false);
  const revision = result?.settings.revision ?? null;
  const ready = !!result && result.status !== "stale";
  const dirty = !!result && (Object.keys(defaults).some(k => {
    const key = k as keyof Choices;
    return draft[key] !== result.settings[key];
  }) || !offset.trim() || Number(offset) !== result.settings.reference_offset_seconds);
  const reference = draft.mode === "reference" || draft.mode === "mix";
  const available = !!result && (!reference || result.availability.reference_has_audio) &&
    (draft.mode !== "mix" || result.availability.original_has_audio);
  const valid = available && (!reference || (!!offset.trim() && finite(Number(offset), 120) &&
    Number(offset) < (result?.availability.reference_duration_seconds ?? 0)));
  useEffect(() => { onState({ revision, ready, dirty, busy, mode: result?.settings.mode }); }, [onState, revision, ready, dirty, busy, result?.settings.mode]);
  function restore(value: Result) {
    setResult(value); setDraft(choices(value.settings)); setOffset(String(value.settings.reference_offset_seconds)); setError("");
    if (value.settings.mode === "mix") mixedBefore.current = true;
  }
  useEffect(() => {
    const controller = new AbortController();
    void request(projectId, controller.signal).then(value => { if (!controller.signal.aborted) restore(value); })
      .catch(cause => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "Audio could not be loaded."); });
    return () => { controller.abort(); action.current?.abort(); };
  }, [projectId]);
  async function run(save: boolean) {
    if (action.current) return;
    const controller = new AbortController(); action.current = controller; setBusy(true); setError("");
    try {
      const value = await request(projectId, controller.signal, save ? {
        ...draft, reference_offset_seconds: reference ? Number(offset) : draft.reference_offset_seconds,
        expected_revision: revision, refresh_sources: result?.status === "stale",
      } : undefined);
      if (!controller.signal.aborted) restore(value);
    } catch (cause) {
      if (!controller.signal.aborted) {
        setError(cause instanceof Error && cause.name !== "AbortError" ? cause.message : "Request timed out. Reload explicitly to check saved state. Your choices are retained.");
        if (cause instanceof Error && "code" in cause && cause.code === "audio_stale") {
          try { const value = await request(projectId, controller.signal); if (!controller.signal.aborted) setResult(value); } catch { /* Preserve draft and safe error. */ }
        }
      }
    } finally { if (action.current === controller) { action.current = null; if (!controller.signal.aborted) setBusy(false); } }
  }
  function mode(value: Mode) {
    const firstMix = value === "mix" && !mixedBefore.current;
    if (value === "mix") mixedBefore.current = true;
    setDraft(previous => {
      if (firstMix) return { ...previous, mode: value, original_volume: 70, reference_volume: 30 };
      return { ...previous, mode: value, ...(value === "original" ? { original_volume: 100 } : value === "reference" ? { reference_volume: 100 } : {}) };
    });
  }
  return <section className="reference-section" aria-label="Audio">
    <h3>Audio</h3>
    <p className="hint">Reference audio may include speech and sound effects. Retrieval does not grant permission to reuse content.</p>
    {!result && !error && <p role="status">Loading audio choices…</p>}
    {error && <p role="alert">{error}</p>}
    {result && <>
      <p role="status">{result.status === "stale" ? "Stale audio settings" : dirty ? "Unsaved audio changes" : result.status === "default" ? "Default original audio" : "Audio saved"} · Audio revision {revision}</p>
      {result.status === "stale" && <p role="alert">{result.message || "Audio sources changed."} Saved choices are retained. Choose an available mode and explicitly save with current sources.</p>}
      {!result.availability.reference_has_audio && <p>Reference audio is unavailable. Retrieve ready reference media containing audio, then reload audio choices.</p>}
      {!result.availability.original_has_audio && <p>Footage has no available audio. Original mode remains valid and silent; Mix requires both audio streams.</p>}
      <fieldset className="recipe-controls" disabled={busy}>
        <legend>Saved audio choices</legend>
        <label>Audio mode <select value={draft.mode} onChange={e => mode(e.target.value as Mode)}>
          <option value="original">Original footage audio</option>
          <option value="reference" disabled={!result.availability.reference_has_audio}>Reference audio</option>
          <option value="mix" disabled={!result.availability.reference_has_audio || !result.availability.original_has_audio}>Mix</option>
          <option value="mute">Mute</option>
        </select></label>
        {(draft.mode === "original" || draft.mode === "mix") && <label>Original volume: <output>{draft.original_volume}%</output>
          <input aria-label="Original volume" type="range" min="0" max="100" step="1" value={draft.original_volume} onChange={e => setDraft(v => ({ ...v, original_volume: Number(e.target.value) }))} /></label>}
        {reference && <>
          <label>Reference volume: <output>{draft.reference_volume}%</output>
            <input aria-label="Reference volume" type="range" min="0" max="100" step="1" value={draft.reference_volume} onChange={e => setDraft(v => ({ ...v, reference_volume: Number(e.target.value) }))} /></label>
          <label>Reference start offset (seconds)<input type="number" min="0" max={result.availability.reference_duration_seconds ?? 120} step="any" value={offset} onChange={e => { setOffset(e.target.value); if (finite(Number(e.target.value), 120)) setDraft(v => ({ ...v, reference_offset_seconds: Number(e.target.value) })); }} /></label>
          <p className="hint">Offset is inside the reference video’s timeline. Reference audio plays continuously from output time zero, including across cuts. Short tracks are padded with silence; they are not looped or stretched.</p>
        </>}
        <button disabled={!valid || (!dirty && result.status !== "stale")} onClick={() => void run(true)}>{result.status === "stale" ? "Save with current sources" : "Save audio"}</button>
      </fieldset>
    </>}
    <button disabled={busy} onClick={() => void run(false)}>{dirty ? "Discard audio changes and reload" : "Reload audio choices"}</button>
    {busy && <p role="status">Saving or loading audio…</p>}
  </section>;
}
