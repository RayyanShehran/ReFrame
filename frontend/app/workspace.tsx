"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { ClipUpload, parseClipDetails, type ClipDetails } from "./clip-upload";
import { ReferenceSelection } from "./reference-selection";
import { ProjectColors } from "./project-colors";

type Project = {
  id: string; name: string; created_at: string; updated_at: string;
  reference: { canonical_url: string; title: string | null; author_name: string | null };
  reference_inspected_at: string; status: "active" | "deleting";
  clip_status: "empty" | "staging" | "validated" | "ready" | "unavailable";
  clip: ClipDetails | null;
};
const apiBase = (process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

async function request(path: string, signal: AbortSignal, options: RequestInit = {}): Promise<unknown> {
  const controller = new AbortController();
  const abort = () => { controller.abort(); clearTimeout(timer); };
  const timer = setTimeout(abort, 20000);
  signal.addEventListener("abort", abort, { once: true });
  if (signal.aborted) controller.abort();
  try {
    const response = await fetch(`${apiBase}/api/projects${path}`, { ...options, signal: controller.signal });
    if (response.status === 204) return null;
    const data = await response.json();
    if (!response.ok) throw new Error(data?.error?.message || "Project request failed. Please retry.");
    return data;
  } catch (cause) {
    if (controller.signal.aborted && !signal.aborted) throw new Error("Project request took too long. Please retry.");
    throw cause;
  } finally { clearTimeout(timer); signal.removeEventListener("abort", abort); }
}

function project(data: unknown): Project {
  if (typeof data !== "object" || data === null || !("id" in data) || typeof data.id !== "string" ||
      !("name" in data) || typeof data.name !== "string" || !("reference" in data) || !data.reference ||
      !("clip_status" in data) || !["empty", "staging", "validated", "ready", "unavailable"].includes(String(data.clip_status)) ||
      !("status" in data) || !["active", "deleting"].includes(String(data.status)) ||
      typeof data.reference !== "object" || !("canonical_url" in data.reference) || typeof data.reference.canonical_url !== "string" ||
      !("title" in data.reference) || (data.reference.title !== null && typeof data.reference.title !== "string") ||
      !("author_name" in data.reference) || (data.reference.author_name !== null && typeof data.reference.author_name !== "string") ||
      !("clip" in data) || !("reference_inspected_at" in data) || typeof data.reference_inspected_at !== "string") throw new Error("Invalid saved project response.");
  return { ...data, clip: data.clip_status === "ready" ? parseClipDetails(data.clip, true) : null } as Project;
}

export function Workspace() {
  const [projectId, setProjectId] = useState<string | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [current, setCurrent] = useState<Project | null>(null);
  const [referenceUrl, setReferenceUrl] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const [newVersion, setNewVersion] = useState(0);
  const [deleting, setDeleting] = useState<Project | null>(null);
  const mutation = useRef<AbortController | null>(null);
  const generation = useRef(0);

  useEffect(() => {
    const restore = () => {
      generation.current++; mutation.current?.abort(); mutation.current = null;
      setBusy(false); setCurrent(null); setDeleting(null); setError("");
      setRevision((value) => value + 1);
      setProjectId(new URLSearchParams(window.location.search).get("project"));
    };
    restore();
    window.addEventListener("popstate", restore);
    const generationRef = generation;
    return () => { window.removeEventListener("popstate", restore); mutation.current?.abort(); generationRef.current++; };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const version = generation.current;
    let live = true;
    (async () => {
      try {
        const data = await request("", controller.signal);
        if (!Array.isArray(data)) throw new Error("Invalid project list response.");
        if (live && generation.current === version) setProjects(data.map(project));
        if (projectId) {
          const saved = project(await request(`/${encodeURIComponent(projectId)}`, controller.signal));
          if (live && generation.current === version) setCurrent(saved);
        }
      } catch (cause) { if (live && generation.current === version) setError(cause instanceof Error ? cause.message : "Could not open projects."); }
      finally { if (live) setLoading(false); }
    })();
    return () => { live = false; controller.abort(); };
  }, [projectId, revision]);

  function open(id: string | null) {
    generation.current++; mutation.current?.abort(); mutation.current = null;
    setCurrent(null); setError(""); setBusy(false); setDeleting(null); setReferenceUrl(null);
    setLoading(true); setRevision((value) => value + 1);
    if (!id) setNewVersion((value) => value + 1);
    const url = new URL(window.location.href);
    if (id) url.searchParams.set("project", id); else url.searchParams.delete("project");
    window.history.pushState(null, "", url);
    setProjectId(id);
  }

  async function mutate(path: string, options: RequestInit, after: (data: unknown) => void) {
    if (mutation.current) return;
    const controller = new AbortController();
    mutation.current = controller;
    const version = generation.current;
    setBusy(true); setError("");
    try {
      const data = await request(path, controller.signal, options);
      if (generation.current === version) { after(data); setRevision((value) => value + 1); }
    } catch (cause) {
      if (generation.current === version) { setError(cause instanceof Error ? cause.message : "Project request failed."); setRevision((value) => value + 1); }
    } finally {
      if (mutation.current === controller) mutation.current = null;
      if (generation.current === version) setBusy(false);
    }
  }

  function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!referenceUrl) return;
    void mutate("", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, reference_url: referenceUrl }) }, (data) => open(project(data).id));
  }

  return <>
    <section className="reference-section" aria-labelledby="projects-title">
      <h2 id="projects-title">Saved projects</h2>
      <p className="hint">Single-user local storage · Up to 10 projects · One reference and one clip each</p>
      <button className="clear-clip" onClick={() => open(null)}>New project</button>
      <ul className="project-list">{projects.map((saved) => <li key={saved.id}>
        <span>{saved.name}{saved.status === "deleting" ? " — deletion incomplete" : ""}</span>
        <button onClick={() => open(saved.id)}>Open {saved.name}</button>
        <button onClick={() => setDeleting(saved)}>Delete {saved.name}</button>
      </li>)}</ul>
      {loading && <p role="status">Loading projects…</p>}
      {error && <p role="alert" className="error">{error}</p>}
      {deleting && <div role="dialog" aria-modal="false" aria-labelledby="delete-title" className="reference-card">
        <h3 id="delete-title">Delete “{deleting.name}”?</h3>
        <p>This removes the saved project, reference media and uploaded clip from this computer.</p>
        <div className="reference-actions"><button disabled={busy} onClick={() => void mutate(`/${deleting.id}`, { method: "DELETE" }, () => { if (projectId === deleting.id) open(null); setDeleting(null); })}>Confirm delete</button>
          <button disabled={busy} onClick={() => setDeleting(null)}>Cancel deletion</button></div>
      </div>}
    </section>
    {!projectId && <>
      <ReferenceSelection key={newVersion} onSelectedChange={(id, url) => {
        if (mutation.current) { generation.current++; mutation.current.abort(); mutation.current = null; setBusy(false); }
        setReferenceUrl(id && url ? url : null);
      }} />
      {referenceUrl && <form className="reference-section" onSubmit={create}>
        <label htmlFor="project-name">Project name</label>
        <div className="reference-form-row"><input id="project-name" value={name} onChange={(event) => setName(event.target.value)} minLength={1} maxLength={80} required />
          <button disabled={busy || !name.trim()} type="submit">{busy ? "Creating…" : "Create project"}</button></div>
      </form>}
    </>}
    {current && <section className="footage-section" aria-label="Saved project workspace">
      <h2>{current.name}</h2>
      <div className="reference-card"><h3>{current.reference.title || "TikTok reference"}</h3>
        <p>Creator: {current.reference.author_name || "Unavailable"}</p>
        <a href={current.reference.canonical_url} target="_blank" rel="noreferrer">Open reference on TikTok</a>
        <p className="hint">Saved metadata snapshot from {current.reference_inspected_at}. The reference is fixed for this project.</p>
      </div>
      {current.status === "active" && <ProjectColors key={current.id} projectId={current.id} hasFootage={current.clip_status === "ready"} />}
      {current.status === "deleting" ? <p role="alert">Deletion is incomplete. Retry deletion from the project list.</p> :
        current.clip_status === "empty" || current.clip_status === "ready" ?
          <ClipUpload key={`${current.id}-${current.clip_status}`} projectId={current.id} initialDetails={current.clip} onSaved={() => setRevision((value) => value + 1)} /> :
          <p role="alert">{current.clip_status === "unavailable" ? "Saved media is unavailable or corrupt. Delete this project and create a new one." : "A clip save is incomplete. Reopen after the operation finishes; restart the backend if it was interrupted."}</p>}
    </section>}
  </>;
}
