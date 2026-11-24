"use client";

import { useEffect, useRef, useState } from "react";
import { apiBase, clipRequest, footageRejection, readLibrary, type Library } from "./clip-library";
import { FootagePicker } from "./clip-upload";
import { useMediaOperation, type MediaOperation } from "./use-media-operation";
import { AutomaticCaptionReview, type CaptionReview } from "./automatic-caption-review";
import { Workspace } from "./workspace";
import { AppHeader } from "./app-header";
import { Banner, Button, ProgressSteps } from "./ui";
import { fetchReference } from "./reference-selection";

const MiB = 1024 * 1024;
// The stage strings generation reports, in order (backend generation.STAGES).
const STAGES = ["Reading reference", "Choosing your clips", "Matching the look", "Recreating captions", "Rendering"] as const;

type Options = { audio: "reference" | "original" | "mix" | "mute"; captions: "reference" | "output_audio" | "off"; allow_reused_ranges: boolean; matching_mode?: "subject_aware" | "measurements" };
type Generation = MediaOperation & { project_id: string; revision: number; operation_id: string | null; stage: string | null; output_id: string | null; notices: string[]; options: Options | null; caption_review?: CaptionReview | null };
type Item = { id: string; file: File; url: string; status: "pending" | "uploading" | "succeeded" | "failed"; progress: number; error: string };
type SavedProject = { id: string; name: string; reference: { canonical_url: string } };
function parse(value: unknown): Generation {
  const g = value as Generation;
  if (!g || typeof g.project_id !== "string" || !Number.isSafeInteger(g.revision) || g.revision < 0 || !["idle", "running", "ready", "failed"].includes(g.status) || !Array.isArray(g.notices) || g.notices.length > 30 || g.notices.some(n => typeof n !== "string" || n.length > 2000)) throw new Error("Generation status could not be read.");
  if (g.caption_review && (!Array.isArray(g.caption_review.cues) || g.caption_review.cues.length > 200 || g.caption_review.cues.some(c => !Number.isFinite(c.start) || !Number.isFinite(c.end) || c.start < 0 || c.end <= c.start || typeof c.text !== "string" || c.text.length > 200))) throw new Error("Detected caption review could not be read.");
  return g;
}
async function request(path: string, signal: AbortSignal, body?: unknown) {
  const response = await fetch(`${apiBase}/api/projects${path}`, { signal, method: body ? "POST" : "GET", ...(body ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}) });
  const value = await response.json();
  if (!response.ok) throw Object.assign(new Error(value?.error?.message || "Request failed. Retry explicitly."), { code: value?.error?.code });
  return value;
}
function upload(projectId: string, file: File, signal: AbortSignal, progress: (value: number) => void): Promise<Library> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest(), body = new FormData(); body.append("file", file);
    const abort = () => xhr.abort(); signal.addEventListener("abort", abort, { once: true });
    const finish = () => signal.removeEventListener("abort", abort);
    xhr.open("POST", `${apiBase}/api/projects/${encodeURIComponent(projectId)}/clips`); xhr.timeout = 35000;
    xhr.upload.onprogress = e => { if (e.lengthComputable) progress(Math.round(e.loaded / e.total * 100)); };
    xhr.onload = () => { finish(); try { const data = JSON.parse(xhr.responseText); if (xhr.status < 200 || xhr.status >= 300) throw new Error(data?.error?.message || "Upload failed."); resolve(readLibrary(data)); } catch (error) { reject(error); } };
    xhr.onerror = xhr.ontimeout = () => { finish(); reject(new Error("Upload interrupted. Check saved clips before retrying.")); };
    xhr.onabort = () => { finish(); reject(new DOMException("Upload stopped", "AbortError")); };
    if (signal.aborted) { finish(); reject(new DOMException("Upload stopped", "AbortError")); return; }
    xhr.send(body);
  });
}
export function AutomaticMaker() {
  const [projectId, setProjectId] = useState<string | null>(null), [projects, setProjects] = useState<SavedProject[]>([]);
  const [link, setLink] = useState(""), [audio, setAudio] = useState<Options["audio"]>("reference"), [captionMode, setCaptionMode] = useState<Options["captions"]>("reference");
  const [library, setLibrary] = useState<Library | null>(null), [queue, setQueue] = useState<Item[]>([]);
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [customize, setCustomize] = useState(false);
  const [allowReuse, setAllowReuse] = useState(false), [replace, setReplace] = useState(false), [needsReplace, setNeedsReplace] = useState(false);
  const [customizeMounted, setCustomizeMounted] = useState(false), [acceptPartial, setAcceptPartial] = useState(false);
  const [reviewTarget, setReviewTarget] = useState<HTMLDivElement | null>(null), [menuTarget, setMenuTarget] = useState<HTMLDivElement | null>(null);
  const [reviewDirty, setReviewDirty] = useState(false), [replaceVerified, setReplaceVerified] = useState(false);
  const [outputId, setOutputId] = useState<string | null>(null);
  const [preview, setPreview] = useState<{ url: string; state: "checking" | "ready" | "failed"; title?: string | null; author?: string | null } | null>(null);
  const items = useRef<Item[]>([]), action = useRef<AbortController | null>(null), currentId = useRef<string | null>(null);
  const urls = useRef(new Set<string>());
  const generation = useMediaOperation(projectId ?? "", "generation", parse, 1200);
  const operation = generation.operation?.project_id === projectId ? generation.operation : null;
  const running = busy || operation?.status === "running";
  function update(value: Item[]) { items.current = value; setQueue(value); }
  function mark(id: string, values: Partial<Item>) { update(items.current.map(item => item.id === id ? { ...item, ...values } : item)); }
  function files(selected: File[]) {
    if (running) return;
    let count = library?.clips.length ?? 0, bytes = library?.total_bytes ?? 0;
    for (const item of items.current) if (item.status === "pending") { count++; bytes += item.file.size; }
    update([...items.current, ...selected.slice(0, 30).map(file => {
      const rejected = footageRejection(file, count, bytes), url = rejected ? "" : URL.createObjectURL(file);
      if (!rejected) { count++; bytes += file.size; urls.current.add(url); }
      return { id: crypto.randomUUID(), file, url, status: rejected ? "failed" as const : "pending" as const, progress: 0, error: rejected };
    })]);
    if (selected.length > 30) setError("Select at most 30 files at a time; only 10 clips can be saved.");
  }
  async function open(id: string | null, changeHistory = true) {
    action.current?.abort(); action.current = null;
    const old = currentId.current;
    if (old && old !== id) {
      try { await request(`/${encodeURIComponent(old)}/generation/cancel`, AbortSignal.timeout(10000), {}); }
      catch { setError("Could not confirm cancellation. Retry before switching projects."); return; }
    }
    currentId.current = id; setProjectId(id); setLibrary(null); setOutputId(null); setCustomize(false); setCustomizeMounted(false); setBusy(false); setError(""); setReplace(false); setNeedsReplace(false); setAcceptPartial(false); setReviewDirty(false); setReplaceVerified(false);
    for (const url of urls.current) URL.revokeObjectURL(url); urls.current.clear(); update([]);
    if (changeHistory) { const url = new URL(window.location.href); if (id) url.searchParams.set("project", id); else url.searchParams.delete("project"); window.history.pushState(null, "", url); }
    if (!id) { setLink(""); setAudio("reference"); setCaptionMode("reference"); return; }
    try {
      const [saved, retained, render] = await Promise.all([request(`/${id}`, AbortSignal.timeout(10000)), clipRequest(id, "/clips"), clipRequest(id, "/render")]);
      if (currentId.current !== id) return;
      setLink(saved.reference.canonical_url); setLibrary(readLibrary(retained)); setOutputId(render.output?.output_id ?? null);
    } catch (cause) { if (currentId.current === id) setError(cause instanceof Error ? cause.message : "Project could not be opened."); }
  }
  useEffect(() => {
    const controller = new AbortController();
    void request("", controller.signal).then(value => { if (!controller.signal.aborted) setProjects(value); }).catch(() => {});
    const id = new URLSearchParams(window.location.search).get("project");
    void Promise.resolve().then(() => { if (!controller.signal.aborted) return open(id, false); });
    const restore = () => { void open(new URLSearchParams(window.location.search).get("project"), false); };
    window.addEventListener("popstate", restore);
    const ownedUrls = urls.current;
    return () => { controller.abort(); action.current?.abort(); window.removeEventListener("popstate", restore); for (const url of ownedUrls) URL.revokeObjectURL(url); };
    // Opening a project is an explicit action; this effect only initializes browser history.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    if (!operation) return;
    // Hydrate local drafts from persisted server status on restore or a completed operation.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (operation.options) { setAudio(operation.options.audio); setCaptionMode(operation.options.captions); setAllowReuse(operation.options.allow_reused_ranges); }
    if (operation.output_id) setOutputId(operation.output_id);
  }, [operation?.revision, operation?.status, operation?.output_id]); // eslint-disable-line react-hooks/exhaustive-deps
  // Optional title/creator preview for a new project's link: debounced, aborted on any change and
  // shown only for the link it was fetched for. A failure is a hint; it never blocks Generate.
  const previewUrl = !projectId && !running && /^https:\/\/([a-z0-9-]+\.)*tiktok\.com\//i.test(link.trim()) ? link.trim() : "";
  useEffect(() => {
    if (!previewUrl) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      setPreview({ url: previewUrl, state: "checking" });
      fetchReference(previewUrl, controller.signal)
        .then(details => setPreview({ url: previewUrl, state: "ready", title: details.title, author: details.author_name }))
        .catch(() => { if (!controller.signal.aborted) setPreview({ url: previewUrl, state: "failed" }); });
    }, 500);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [previewUrl]);
  const shownPreview = preview && preview.url === previewUrl ? preview : null;
  async function generate(matchingMode: "subject_aware" | "measurements" = operation?.options?.matching_mode ?? "subject_aware", captions = captionMode, partial = false, audioMode = audio) {
    if (action.current || running || reviewDirty || (!projectId && !link.trim())) return;
    const controller = new AbortController(); action.current = controller; setBusy(true); setError("");
    try {
      let id = projectId;
      if (!id) {
        const saved = await request("", controller.signal, { name: "My ReFrame video", reference_url: link.trim() });
        if (controller.signal.aborted) return;
        id = saved.id; currentId.current = id; setProjectId(id); setProjects(all => [saved, ...all]);
        const url = new URL(window.location.href); url.searchParams.set("project", id!); window.history.replaceState(null, "", url);
      }
      let retained = readLibrary(await clipRequest(id!, "/clips", undefined, controller.signal)); setLibrary(retained);
      while (!controller.signal.aborted) {
        const item = items.current.find(item => item.status === "pending"); if (!item) break;
        const rejected = footageRejection(item.file, retained.clips.length, retained.total_bytes);
        if (rejected) { mark(item.id, { status: "failed", error: rejected }); continue; }
        mark(item.id, { status: "uploading", progress: 0 });
        try { retained = await upload(id!, item.file, controller.signal, value => mark(item.id, { progress: value })); if (controller.signal.aborted) return; mark(item.id, { status: "succeeded", progress: 100 }); setLibrary(retained); }
        catch (cause) { if (controller.signal.aborted) return; mark(item.id, { status: "failed", error: cause instanceof Error ? cause.message : "Upload failed." }); }
      }
      if (controller.signal.aborted || currentId.current !== id) return;
      if (items.current.some(item => item.status === "failed")) throw new Error("Some files could not be uploaded. Retry/remove them; successful clips remain saved.");
      if (!retained.clips.length) throw new Error("Choose at least one MP4 or MOV clip.");
      await request(`/${id}/generation`, controller.signal, { expected_revision: operation?.revision ?? 0, audio: audioMode, captions, allow_reused_ranges: allowReuse, replace_customized: replace, matching_mode: matchingMode, accept_partial_captions: partial, replace_verified_captions: replaceVerified });
      generation.refresh(); setNeedsReplace(false); setReplace(false); setAcceptPartial(false); setReviewDirty(false); setReplaceVerified(false);
    } catch (cause) { if (!controller.signal.aborted) { setError(cause instanceof Error ? cause.message : "Generation failed."); if (cause instanceof Error && "code" in cause && cause.code === "generation_replace_required") setNeedsReplace(true); } }
    finally { if (action.current === controller) { action.current = null; setBusy(false); } }
  }
  const projectName = projects.find(p => p.id === projectId)?.name || "Your project";
  // Usage counts saved clips plus files still waiting to upload, as footageRejection does.
  const waiting = queue.filter(item => item.status === "pending" || item.status === "uploading");
  const usage = `${(library?.clips.length ?? 0) + waiting.length} of ${library?.max_clips ?? 10} · ${(((library?.total_bytes ?? 0) + waiting.reduce((sum, item) => sum + item.file.size, 0)) / MiB).toFixed(1)} of ${Math.round((library?.max_bytes ?? 500 * MiB) / MiB)} MiB`;
  const generateControls = <div className="generate-controls">
    {(operation?.failure_code === "footage_shortage" || allowReuse) && <label><input type="checkbox" checked={allowReuse} disabled={!!running} onChange={e => setAllowReuse(e.target.checked)} /> Allow reused footage ranges</label>}
    {needsReplace && <label><input type="checkbox" checked={replace} onChange={e => setReplace(e.target.checked)} /> Replace my saved sequence, color, audio and caption choices for this generation</label>}
    <Button variant="primary" size="lg" className="generate-button" disabled={!!running || (!projectId && (!link.trim() || !queue.some(item => item.status === "pending"))) || (needsReplace && !replace)} onClick={() => void generate()}>{busy ? "Saving your clips…" : operation?.status === "failed" ? "Retry generation" : outputId ? "Generate again" : "Generate video"}</Button>
  </div>;
  const failureMessage = error || generation.error || operation?.message;
  const visualRecovery = operation?.status === "failed" && ["sample_limit", "inference_failed", "cache_limit", "analysis_limit", "processing_timeout", "timeout", "deadline", "model_unavailable", "visual_setup", "dimension_limit"].includes(operation.failure_code ?? "") && (operation.stage === "Choosing your clips" || operation.failure_code === "visual_setup") && <><p>Visual matching could not finish within its limits. Your clips and completed work are saved.</p><button disabled={!!running} onClick={() => void generate("measurements", operation?.options?.captions ?? captionMode, false, operation?.options?.audio ?? audio)}>Continue using motion and scenes</button></>;
  const captionRecovery = ((operation?.status === "ready" && !!operation.caption_review?.unreadable_samples) || operation?.status === "failed" && (["caption_setup", "caption_unreadable", "caption_partial", "caption_review_required", "caption_cue_limit", "ocr_unavailable", "caption_work_limit", "caption_region_limit", "caption_style_limit", "caption_appearance_limit", "no_useful_font_match", "appearance_unusable", "ocr_failed", "ocr_output_invalid", "handwriting_unavailable"].includes(operation.failure_code ?? "") || (operation.failure_code === "generation_failed" && operation.stage === "Recreating captions"))) && <div className="caption-recovery"><button disabled={!!running || reviewDirty} onClick={() => void generate(undefined, "reference", false, operation?.options?.audio ?? audio)}>Retry caption reading</button><button disabled={!!running || reviewDirty} onClick={() => void generate(undefined, "off", false, operation?.options?.audio ?? audio)}>Continue without captions</button>{!!operation.caption_review?.cues.length && <><details><summary>{operation.caption_review.cues.length} detected cues — incomplete</summary><ul>{operation.caption_review.cues.map((cue, i) => <li key={i}>{cue.start.toFixed(2)}–{cue.end.toFixed(2)}: {cue.text}</li>)}</ul></details><label><input type="checkbox" checked={acceptPartial} onChange={e => setAcceptPartial(e.target.checked)} /> I want to export these incomplete captions; some words may be missing or misread.</label><button disabled={!!running || reviewDirty || !acceptPartial} onClick={() => void generate(undefined, "reference", true, operation?.options?.audio ?? audio)}>Use detected captions</button></>}</div>;
  const setupDetails = (operation?.failure_code?.includes("setup") || operation?.failure_code === "handwriting_unavailable") && <details><summary>Setup details</summary><p>Install both local feature sets in backend: <code>uv sync --locked --extra visual --extra transcription</code>. Follow the local visual, OCR and transcription setup guides in the repository’s docs. No assets download during Generate.</p><p>Optional English handwriting reader: from the project folder run backend\.venv\Scripts\python.exe backend\handwriting_ocr.py setup --download. This explicitly installs the MIT-labelled TrOCR-base checkpoint (1.34 GB) in the isolated CPU environment. It can still misread text and is limited to 16 requests per generation. The earlier small checkpoint stays unused; installed assets and saved readings are preserved.</p></details>;
  return <div className={customize ? "customize-open" : undefined}>
    {/* Customize puts the editor's "Saved projects" menu in the header slot (a portal from Workspace). */}
    <AppHeader project={customize ? `${projectName} · Customize` : projectId ? projectName : "Reference in. Your video out."}
      menu={customize ? <div ref={setMenuTarget} /> : !!projects.length && <details className="project-switcher"><summary>Saved videos</summary><ul>{projects.map(project => <li key={project.id}><button onClick={() => void open(project.id)}>{project.name}</button></li>)}</ul></details>}
      actions={customize ? <button onClick={() => setCustomize(false)}>Return to result</button> : <button onClick={() => void open(null)}>New video</button>} />
    <div className="page-title"><h1>{projectId ? projectName : "New video"}</h1><p>{projectId ? "Change your clips or choices, then generate again." : "Paste a TikTok reference, add your clips, then generate."}</p></div>
    <section className="create-card" aria-label="Create video">
      <ol className="create-steps" aria-label="Create steps">
        <li><h2>Reference link</h2>
          <label>TikTok reference link<input aria-label="TikTok reference link" type="url" placeholder="https://www.tiktok.com/…" value={link} disabled={running || !!projectId} onChange={e => setLink(e.target.value)} /></label>
          <div aria-live="polite">{shownPreview?.state === "checking" ? <p className="hint">Checking reference…</p>
            : shownPreview?.state === "ready" ? <p className="reference-preview">{shownPreview.title || "TikTok video"} · {shownPreview.author || "Unknown creator"}</p>
            : shownPreview?.state === "failed" ? <p className="hint">Reference details could not be loaded. You can still generate.</p> : null}</div>
        </li>
        <li><h2>Clips</h2>
          <p className="clip-usage">{usage}</p>
          <ul className="automatic-clips">{queue.map(item => <li key={item.id}>
            {item.url && <video src={item.url} preload="metadata" muted aria-label={`Thumbnail: ${item.file.name}`} />}
            <span>{item.file.name}<small>{item.status}{item.status === "uploading" ? ` · ${item.progress}% uploaded` : ""}</small></span>
            {item.error && <p role="alert">{item.error}</p>}
            {item.status === "failed" && <button disabled={!!running} onClick={() => mark(item.id, { status: "pending", error: "" })}>Retry {item.file.name}</button>}
            {item.status !== "succeeded" && <button disabled={!!running} onClick={() => { if (item.url) { URL.revokeObjectURL(item.url); urls.current.delete(item.url); } update(items.current.filter(value => value.id !== item.id)); }}>Remove {item.file.name}</button>}
          </li>)}
          {library?.clips.map(clip => <li key={clip.id} className="saved-clip"><span>{clip.name}<small>{clip.available ? "saved" : "unavailable"}</small></span></li>)}
          <li className="clip-add"><span className="clip-add-title">Add clips</span><FootagePicker actionLabel="Generate video" label="Choose your clips" disabled={!!running} onFilesSelected={files} /></li></ul>
          <p className="hint">MP4 or MOV, up to 100 MiB each. You can add clips before the link.</p>
        </li>
        <li><h2>Audio and captions</h2>
          <div className="automatic-options"><label>Audio<select value={audio} disabled={!!running} onChange={e => setAudio(e.target.value as Options["audio"])}><option value="reference">Reference audio</option><option value="original">My footage</option><option value="mix">Mix</option><option value="mute">Mute</option></select></label>
          <label>Captions<select value={captionMode} disabled={!!running} onChange={e => setCaptionMode(e.target.value as Options["captions"])}><option value="reference">Recreate reference captions</option><option value="output_audio">Caption my audio in reference style</option><option value="off">Off</option></select></label></div>
          <p className="hint">{captionMode === "reference" ? "Reads the reference’s on-screen words (text recognition, not speech)." : captionMode === "output_audio" ? "Transcribes your chosen audio in the reference’s caption style." : "No captions."}{audio !== "original" && audio !== "mute" && " Only reuse reference audio you have permission to use."}</p>
        </li>
      </ol>
      {generateControls}
    </section>
    {operation?.status === "running" && <section className="progress-panel" role="status" aria-label="Generation progress">
      <h2>Generating your video</h2>
      {(STAGES as readonly string[]).includes(operation.stage ?? "") ? <ProgressSteps aria-label="Generation stages" steps={STAGES} current={operation.stage!} /> : <p>{operation.stage}</p>}
      <Button onClick={() => void generation.start({}, "/cancel")}>Cancel</Button>
    </section>}
    {/* One danger banner, with every recovery path for the failure grouped beneath it. */}
    {(failureMessage || visualRecovery || captionRecovery || setupDetails) && <section className="status-panel">
      {failureMessage && <Banner tone="danger" role="alert">{failureMessage}</Banner>}
      {(visualRecovery || captionRecovery || setupDetails) && <div className="recovery" role="group" aria-label="Recovery options">{visualRecovery}{captionRecovery}{setupDetails}</div>}
    </section>}
    {outputId && projectId && <section className="reference-section automatic-result" aria-label="Your video"><h2>Your video</h2>{operation?.status !== "ready" && <p className="hint">Your previous video stays available until a replacement succeeds.</p>}<video controls playsInline preload="metadata" src={`${apiBase}/api/projects/${projectId}/outputs/${outputId}/video`} aria-label="Generated video" /><a className="button-link" href={`${apiBase}/api/projects/${projectId}/outputs/${outputId}/download`}>Download video</a><button onClick={() => { setCustomizeMounted(true); setCustomize(value => !value); }}>{customize ? "Close Customize" : "Customize"}</button>{!!operation?.notices.length && <details><summary>About this result</summary><ul>{Array.from(new Set(operation.notices)).map(note => <li key={note}>{note}</li>)}</ul></details>}</section>}
    {operation?.status === "ready" && !!operation.caption_review?.unreadable_samples && !!operation.caption_review?.cues.length && <details><summary>Saved caption reading — incomplete</summary><p>These detected words were retained for review. Check the saved caption mode for this video.</p><ul>{operation.caption_review.cues.map((cue, i) => <li key={i}>{cue.start.toFixed(2)}–{cue.end.toFixed(2)}: {cue.text}</li>)}</ul></details>}
    {projectId && operation?.caption_review && <AutomaticCaptionReview portalTarget={customize ? reviewTarget : null} key={`${projectId}:${operation.revision}:${operation.status}`} projectId={projectId} revision={operation.revision} review={operation.caption_review} disabled={!!running} onSaved={generation.refresh} onDirty={setReviewDirty}>{(!!operation.caption_review.verified_cues?.length || !!operation.caption_review.corrected_cues?.length) && <label><input type="checkbox" checked={replaceVerified} disabled={!!running || reviewDirty} onChange={e => setReplaceVerified(e.target.checked)} /> Replace my verified or corrected cues on the next retry</label>}{customize && <div className="caption-recovery"><button disabled={!!running || reviewDirty} onClick={() => void generate(undefined, "reference", false, operation?.options?.audio ?? audio)}>Retry caption reading</button><button disabled={!!running || reviewDirty} onClick={() => void generate(undefined, "off", false, operation?.options?.audio ?? audio)}>Continue without captions</button></div>}</AutomaticCaptionReview>}
    {!outputId && projectId && <button onClick={() => { setCustomizeMounted(true); setCustomize(value => !value); }}>{customize ? "Close Customize" : "Customize"}</button>}
    {customizeMounted && <div hidden={!customize}><Workspace key={projectId} onCaptionReviewTarget={setReviewTarget} headerMenuTarget={customize ? menuTarget : null} captionReviewDirty={reviewDirty} captionReviewIssues={operation?.caption_review?.unreadable_samples ?? 0} onProjectChange={id => void open(id)} /></div>}
  </div>;
}
