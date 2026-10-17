"use client";

import { createPortal } from "react-dom";
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";

export const sections = { reference: "Reference", footage: "Media", cuts: "Cuts", style: "Color", captions: "Captions", audio: "Audio", framing: "Framing", export: "Export" };
export type Section = keyof typeof sections;
type Report = { section: Section; state: "Needs input" | "Ready" | "Working" | "Needs attention"; message?: string; dirty?: boolean };
type Action = { section: Section; label: string; focusId?: string };
type Selection = { kind: "slot" | "cue" | "clip"; id: string; label: string; time: number };
const Context = createContext<null | { selection: Selection | null; select: (value: Selection) => void; preview: HTMLDivElement | null; strip: HTMLDivElement | null; active: Section; open: (section: Section, focusId?: string) => void; report: (key: string, value: Report | null) => void; next: (value: Action | null) => void }>(null);
export function useWorkspaceNavigation() { return useContext(Context); }
export function useWorkspaceReport(key: string, section: Section, state: Report["state"], message?: string, dirty = false) {
  const report = useContext(Context)?.report;
  useEffect(() => { report?.(key, { section, state, message, dirty }); }, [report, key, section, state, message, dirty]);
  useEffect(() => () => report?.(key, null), [report, key]);
}
export function WorkspaceSection({ section, children }: { section: Section; children: ReactNode }) {
  const workspace = useContext(Context);
  return <div className="workspace-pane" hidden={!!workspace && workspace.active !== section}>{children}</div>;
}
export function WorkspaceSurface({ name, children }: { name: "preview" | "strip"; children: ReactNode }) {
  const workspace = useContext(Context);
  return workspace ? workspace[name] ? createPortal(children, workspace[name]) : null : <>{children}</>;
}
export function GuidedWorkspace({ hasFootage, footageUnavailable = false, children }: { hasFootage: boolean; footageUnavailable?: boolean; children: ReactNode }) {
  const [active, setActive] = useState<Section>("cuts");
  const [slotSelection, setSlotSelection] = useState<Selection | null>(null), [cueSelection, setCueSelection] = useState<Selection | null>(null);
  const [colorSelection, setColorSelection] = useState<Selection | null>(null);
  const selection = active === "captions" ? cueSelection : active === "style" ? colorSelection ?? slotSelection : active === "cuts" ? slotSelection : null;
  const monitor = useRef<HTMLDivElement | null>(null);
  const [preview, setPreview] = useState<HTMLDivElement | null>(null), [strip, setStrip] = useState<HTMLDivElement | null>(null);
  const select = useCallback((value: Selection) => {
    if (value.kind === "clip") { setColorSelection(value); setActive("style"); }
    else if (value.kind === "slot") { setSlotSelection(value); setColorSelection(value); setActive(previous => previous === "style" ? previous : "cuts"); }
    else { setCueSelection(value); setActive("captions"); }
    const player = monitor.current?.querySelector("video");
    if (player && Number.isFinite(player.duration)) player.currentTime = Math.min(value.time, player.duration);
  }, []);
  const monitorRef = useCallback((node: HTMLDivElement | null) => { monitor.current = node; setPreview(node); }, []);
  const [reports, setReports] = useState<Record<string, Report>>({});
  const [action, setAction] = useState<Action | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const open = useCallback((section: Section, focusId?: string) => { setActive(section); requestAnimationFrame(() => ((focusId ? document.getElementById(focusId) : null) ?? heading.current)?.focus()); }, []);
  const report = useCallback((key: string, value: Report | null) => setReports(previous => {
    const updated = { ...previous };
    if (value) updated[key] = value; else delete updated[key];
    return updated;
  }), []);
  const retrieval = reports["reference-media"];
  const referenceColor = reports["style-blueprint"], footageColor = reports["footage-color"];
  const next = footageUnavailable ? { section: "footage" as const, label: "Review unavailable footage" }
    : !hasFootage ? { section: "footage" as const, label: "Upload your footage" }
    : retrieval?.state !== "Ready" || referenceColor?.state !== "Ready" || footageColor?.state !== "Ready" ? { section: "reference" as const, label: "Prepare reference and footage", focusId: "prepare-workflow" }
    : action ?? { section: "style" as const, label: "Review your color recipe" };
  function state(section: Section) {
    const values = Object.values(reports).filter(value => value.section === section);
    if (values.some(value => value.state === "Working")) return "Working";
    if (values.some(value => value.dirty)) return "Unsaved";
    const required = section === "cuts" && reports.sequence?.state === "Ready" ? [reports.sequence] : section === "audio" ? [reports["audio-settings"]].filter(Boolean) : section === "captions" ? [reports["caption-settings"], reports["caption-proposal"]].filter(Boolean) : section === "style" ? [reports.recipe].filter(Boolean) : values;
    if (required.some(value => value.state === "Needs attention")) return "Needs attention";
    if (section === "footage") return footageUnavailable ? "Needs attention" : hasFootage ? "Ready" : "Needs input";
    if (section === "style") return reports.recipe?.state ?? "Needs input";
    return required.length && required.every(value => value.state === "Ready") ? "Ready" : "Needs input";
  }
  return <Context.Provider value={{ active, open, report, next: setAction, selection, select, preview, strip }}>
    <div className="guided-workspace">
      <div className="workspace-guidance"><p role="status">{Object.values(reports).some(r => r.dirty) ? `Unsaved edits: ${Array.from(new Set(Object.values(reports).filter(r => r.dirty).map(r => sections[r.section]))).join(", ")}` : "No unsaved edits reported"}</p><button className="button-primary" onClick={() => open("export")}>Review export</button><p className="hint">Save edits in their own panel, then update the output. Category switches preserve drafts. The player shows the last export, not unsaved changes.</p><p className="workspace-next-hint">{next.label}</p></div>
      {Object.entries(reports).filter(([, value]) => value.state === "Working").map(([key, value]) => <p className="workspace-job" role="status" key={key}>{value.message || "Processing…"} <button onClick={() => open(value.section)}>View {sections[value.section]}</button></p>)}
      <div className="editor-layout"><nav className="workspace-nav" aria-label="Editing sections"><label className="workspace-section-picker">Editing section<select aria-label="Editing section" value={active} onChange={e => open(e.target.value as Section)}>{Object.entries(sections).map(([key, label]) => <option key={key} value={key}>{label} · {state(key as Section)}</option>)}</select></label><div className="workspace-section-buttons">{Object.entries(sections).map(([key, label]) => <button key={key} aria-label={label} aria-describedby={`section-state-${key}`} data-state={state(key as Section)} aria-current={active === key ? "page" : undefined} onClick={() => open(key as Section)}>{label}<small id={`section-state-${key}`}>{state(key as Section)}</small></button>)}</div></nav>
      <div className="editor-stage"><div className="editor-preview" ref={monitorRef} /><div className="editor-strip" ref={setStrip} /></div><div className="editor-inspector"><p className="editor-scope">{selection && ["cuts", "style", "captions"].includes(active) ? `${selection.label} · ${selection.time.toFixed(2)} s` : active === "cuts" ? "Sequence and selected slot" : active === "captions" ? "Caption track and selected cue" : "Whole project"}</p><h2 className="workspace-section-title" ref={heading} tabIndex={-1}>{sections[active]}</h2>
      {children}
    </div></div></div>
  </Context.Provider>;
}
