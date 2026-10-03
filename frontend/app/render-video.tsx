"use client";

import { MediaOperation, useMediaOperation } from "./use-media-operation";

type Output = { output_id: string; spec: { recipe_revision: number }; width: number; height: number; duration_seconds: number; size_bytes: number };
type Operation = MediaOperation & { output: Output | null; spec: { recipe_revision: number } | null; outdated: boolean };
const apiBase = (process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
function parse(data: unknown): Operation {
  const value = data as Operation;
  if (!value || !["idle", "running", "ready", "failed"].includes(value.status) || typeof value.outdated !== "boolean") throw new Error("Invalid render response.");
  if (value.output) {
    const o = value.output;
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(o.output_id) ||
      !Number.isSafeInteger(o.spec?.recipe_revision) || o.spec.recipe_revision < 1 ||
      ![o.width, o.height, o.duration_seconds, o.size_bytes].every(n => Number.isFinite(n) && n > 0)) throw new Error("Invalid rendered video response.");
  }
  return value;
}

export function RenderVideo({ projectId, revision, recipeReady, dirty, busy }: { projectId: string; revision: number | null; recipeReady: boolean; dirty: boolean; busy: boolean }) {
  const { operation, error, starting, start } = useMediaOperation(projectId, "render", parse, 360);
  const output = operation?.output;
  const outdated = !!output && (operation?.outdated || !recipeReady || output.spec.recipe_revision !== revision);
  const running = operation?.status === "running";
  const current = operation?.status === "ready" && !!output && !outdated;
  const url = output ? `${apiBase}/api/projects/${encodeURIComponent(projectId)}/outputs/${output.output_id}` : "";
  return <section className="reference-section" aria-label="Rendered video">
    <h3>Rendered video</h3>
    <p className="hint">Color only, using the saved recipe. Pacing edits, captions and reference music are not applied. These controls have no live preview; render to see the actual result.</p>
    {dirty && <p role="status">Save your unsaved recipe changes before rendering.</p>}
    {!recipeReady && <p>Generate or regenerate a valid color recipe before rendering.</p>}
    <button disabled={!revision || !recipeReady || dirty || busy || starting || running || current}
      onClick={() => void start({ expected_revision: revision })}>{operation?.status === "failed" ? "Retry render" : "Render video"}</button>
    {running && <p role="status">Rendering saved recipe revision {operation.spec?.recipe_revision}… This can take up to five minutes.</p>}
    {error && <p role="alert">{error}</p>}
    {operation?.status === "failed" && <p role="alert">{operation.message || "Rendering failed. Retry explicitly."}</p>}
    {output && <>
      <p role="status">{outdated ? "Outdated output" : "Rendered output"} · Recipe revision {output.spec.recipe_revision} · {output.width} × {output.height} · {output.duration_seconds.toFixed(2)} seconds</p>
      {outdated && <p className="hint">This video uses an older saved recipe or source. Save a valid recipe and render again to replace it.</p>}
      <video key={output.output_id} className="rendered-video" controls preload="metadata" src={`${url}/video`} aria-label={`Rendered video, recipe revision ${output.spec.recipe_revision}`} />
      <p><a className="download-link" href={`${url}/download`}>Download MP4</a> · {(output.size_bytes / 1024 / 1024).toFixed(2)} MiB</p>
    </>}
    {!operation && !error && <p role="status">Loading render status…</p>}
  </section>;
}
