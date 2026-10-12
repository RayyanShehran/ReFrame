"use client";

import { useEffect, useRef, useState } from "react";
import { apiBase, clipRequest, footageRejection, readLibrary, type Library } from "./clip-library";
import { FootagePicker } from "./clip-upload";
import { useMediaOperation, type MediaOperation } from "./use-media-operation";
import { AutomaticCaptionReview, type CaptionReview } from "./automatic-caption-review";
import { Workspace } from "./workspace";

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
  const [reviewDirty, setReviewDirty] = useState(false), [replaceVerified, setReplaceVerified] = useState(false);
  const [outputId, setOutputId] = useState<string | null>(null);
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
  return <>
    <header className="topbar"><span className="brand">ReFrame<span className="brand-dot">.</span></span><span className="header-project">Reference in. Your video out.</span><button onClick={() => void open(null)}>New video</button></header>
    <section className="hero"><p className="eyebrow">[ Your reference. Your footage. ]</p><h1>Make it <em>your own.</em></h1><p className="intro">Paste a reference. Add your clips. Generate your video.</p></section>
    <section className="reference-section automatic-creation" aria-label="Create video">
      <label>TikTok reference link<input aria-label="TikTok reference link" type="url" placeholder="https://www.tiktok.com/…" value={link} disabled={running || !!projectId} onChange={e => setLink(e.target.value)} /></label>
      <FootagePicker actionLabel="Generate video" label="Choose your clips" disabled={!!running} onFilesSelected={files} />
      <p className="hint">MP4/MOV · 100 MiB per file · 10 clips and 500 MiB per project. You can choose clips before adding the link.</p>
      <ul className="automatic-clips">{queue.map(item => <li key={item.id}>
        {item.url && <video src={item.url} preload="metadata" muted aria-label={`Thumbnail: ${item.file.name}`} />}
        <span>{item.file.name}<small>{item.status}{item.status === "uploading" ? ` · ${item.progress}% uploaded` : ""}</small></span>
        {item.error && <p role="alert">{item.error}</p>}
        {item.status === "failed" && <button disabled={!!running} onClick={() => mark(item.id, { status: "pending", error: "" })}>Retry {item.file.name}</button>}
        {item.status !== "succeeded" && <button disabled={!!running} onClick={() => { if (item.url) { URL.revokeObjectURL(item.url); urls.current.delete(item.url); } update(items.current.filter(value => value.id !== item.id)); }}>Remove {item.file.name}</button>}
      </li>)}</ul>
      {!!library?.clips.length && <details><summary>{library.clips.length} saved clips</summary><ul>{library.clips.map(clip => <li key={clip.id}>{clip.name}{!clip.available && " · unavailable"}</li>)}</ul></details>}
      <div className="automatic-options"><label>Audio<select value={audio} disabled={!!running} onChange={e => setAudio(e.target.value as Options["audio"])}><option value="reference">Reference audio</option><option value="original">My footage</option><option value="mix">Mix</option><option value="mute">Mute</option></select></label>
      <label>Captions<select value={captionMode} disabled={!!running} onChange={e => setCaptionMode(e.target.value as Options["captions"])}><option value="reference">Recreate reference captions</option><option value="output_audio">Caption my audio in reference style</option><option value="off">Off</option></select></label></div>
      <p className="hint">{captionMode === "reference" ? "Read the reference’s visible words and estimated timing. This is text recognition, not speech transcription." : captionMode === "output_audio" ? "Generate words from your chosen output audio, borrowing the reference appearance." : "Export without captions."}</p>
      {audio !== "original" && audio !== "mute" && <p className="hint">Reference audio can include speech and sound effects. Only reuse content you have permission to use.</p>}
      {(operation?.failure_code === "footage_shortage" || allowReuse) && <label><input type="checkbox" checked={allowReuse} disabled={!!running} onChange={e => setAllowReuse(e.target.checked)} /> Allow reused footage ranges</label>}
      {needsReplace && <label><input type="checkbox" checked={replace} onChange={e => setReplace(e.target.checked)} /> Replace my saved sequence, color, audio and caption choices for this generation</label>}
      <button className="button-primary" disabled={!!running || (!projectId && (!link.trim() || !queue.some(item => item.status === "pending"))) || (needsReplace && !replace)} onClick={() => void generate()}>{busy ? "Saving your clips…" : operation?.status === "failed" ? "Retry generation" : outputId ? "Generate again" : "Generate video"}</button>
      {operation?.status === "running" && <div role="status" className="automatic-progress"><span className="brand-dot">●</span> {operation.stage}<button onClick={() => void generation.start({}, "/cancel")}>Cancel</button></div>}
      {(error || generation.error || operation?.message) && <p role="alert">{error || generation.error || operation?.message}</p>}
      {operation?.status === "failed" && ["sample_limit", "inference_failed", "cache_limit", "analysis_limit", "processing_timeout", "timeout", "deadline", "model_unavailable", "visual_setup", "dimension_limit"].includes(operation.failure_code ?? "") && (operation.stage === "Choosing your clips" || operation.failure_code === "visual_setup") && <><p>Visual matching could not finish within its limits. Your clips and completed work are saved.</p><button disabled={!!running} onClick={() => void generate("measurements", operation?.options?.captions ?? captionMode, false, operation?.options?.audio ?? audio)}>Continue using motion and scenes</button></>}
      {((operation?.status === "ready" && !!operation.caption_review?.unreadable_samples) || operation?.status === "failed" && (["caption_setup", "caption_unreadable", "caption_partial", "caption_review_required", "caption_cue_limit", "ocr_unavailable", "caption_work_limit", "caption_region_limit", "caption_style_limit", "caption_appearance_limit", "no_useful_font_match", "appearance_unusable", "ocr_failed", "ocr_output_invalid", "handwriting_unavailable"].includes(operation.failure_code ?? "") || (operation.failure_code === "generation_failed" && operation.stage === "Recreating captions"))) && <div className="caption-recovery"><button disabled={!!running || reviewDirty} onClick={() => void generate(undefined, "reference", false, operation?.options?.audio ?? audio)}>Retry caption reading</button><button disabled={!!running || reviewDirty} onClick={() => void generate(undefined, "off", false, operation?.options?.audio ?? audio)}>Continue without captions</button>{!!operation.caption_review?.cues.length && <><details><summary>{operation.caption_review.cues.length} detected cues — incomplete</summary><ul>{operation.caption_review.cues.map((cue, i) => <li key={i}>{cue.start.toFixed(2)}–{cue.end.toFixed(2)}: {cue.text}</li>)}</ul></details><label><input type="checkbox" checked={acceptPartial} onChange={e => setAcceptPartial(e.target.checked)} /> I want to export these incomplete captions; some words may be missing or misread.</label><button disabled={!!running || reviewDirty || !acceptPartial} onClick={() => void generate(undefined, "reference", true, operation?.options?.audio ?? audio)}>Use detected captions</button></>}</div>}
      {(operation?.failure_code?.includes("setup") || operation?.failure_code === "handwriting_unavailable") && <details><summary>Setup details</summary><p>Install both local feature sets in backend: <code>uv sync --locked --extra visual --extra transcription</code>. Follow the local visual, OCR and transcription setup guides in the repository’s docs. No assets download during Generate.</p><p>Optional English handwriting reader: from the project folder, run <code>backend\.venv\Scripts\python.exe backend\handwriting_ocr.py setup --download</code>. This explicitly downloads 247 MB of model assets plus CPU dependencies into a separate local environment. It can still misread text; Arabic continues using the installed Arabic reader.</p></details>}
    </section>
    {outputId && projectId && <section className="reference-section automatic-result" aria-label="Your video"><h2>Your video</h2>{operation?.status !== "ready" && <p className="hint">Your previous video stays available until a replacement succeeds.</p>}<video controls playsInline preload="metadata" src={`${apiBase}/api/projects/${projectId}/outputs/${outputId}/video`} aria-label="Generated video" /><a className="button-link" href={`${apiBase}/api/projects/${projectId}/outputs/${outputId}/download`}>Download video</a><button onClick={() => { setCustomizeMounted(true); setCustomize(value => !value); }}>{customize ? "Close Customize" : "Customize"}</button>{!!operation?.notices.length && <details><summary>About this result</summary><ul>{Array.from(new Set(operation.notices)).map(note => <li key={note}>{note}</li>)}</ul></details>}</section>}
    {operation?.status === "ready" && !!operation.caption_review?.unreadable_samples && !!operation.caption_review?.cues.length && <details><summary>Saved caption reading — incomplete</summary><p>These detected words were retained for review. Check the saved caption mode for this video.</p><ul>{operation.caption_review.cues.map((cue, i) => <li key={i}>{cue.start.toFixed(2)}–{cue.end.toFixed(2)}: {cue.text}</li>)}</ul></details>}
    {projectId && operation?.caption_review && <><AutomaticCaptionReview key={`${projectId}:${operation.revision}:${operation.status}`} projectId={projectId} revision={operation.revision} review={operation.caption_review} disabled={!!running} onSaved={generation.refresh} onDirty={setReviewDirty} />{(!!operation.caption_review.verified_cues?.length || !!operation.caption_review.corrected_cues?.length) && <label><input type="checkbox" checked={replaceVerified} disabled={!!running || reviewDirty} onChange={e => setReplaceVerified(e.target.checked)} /> Replace my verified or corrected cues on the next retry</label>}</>}
    {!outputId && projectId && <button onClick={() => { setCustomizeMounted(true); setCustomize(value => !value); }}>{customize ? "Close Customize" : "Customize"}</button>}
    {customizeMounted && <div hidden={!customize}><Workspace key={projectId} onProjectChange={id => void open(id)} /></div>}
    {!!projects.length && <details className="project-switcher"><summary>Saved videos</summary><ul>{projects.map(project => <li key={project.id}><button onClick={() => void open(project.id)}>{project.name}</button></li>)}</ul></details>}
  </>;
}
