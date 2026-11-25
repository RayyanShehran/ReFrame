"use client";

import { createPortal } from "react-dom";
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { Badge, Banner, Button } from "./ui";

export const sections = { reference: "Reference", footage: "Media", cuts: "Cuts", style: "Color", captions: "Captions", audio: "Audio", framing: "Framing", export: "Export" };
export type Section = keyof typeof sections;
// 24 px stroke icons for the section rail (decorative; each button keeps its text label).
const icons: Record<Section, string> = {
  reference: "M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1",
  footage: "M3 6h12v12H3zM15 10l6-3v10l-6-3",
  cuts: "M6 4v16M18 4v16M3 8h3M3 16h3M18 8h3M18 16h3",
  style: "M12 3a9 9 0 1 0 0 18c1 0 1.5-.8 1.5-1.6 0-1.4 1-2.4 2.4-2.4H18a3 3 0 0 0 3-3c0-5-4-9-9-9zM7.5 11h.01M10 7h.01M14.5 7h.01",
  captions: "M3 5h18v14H3zM7 12h4M13 12h4M7 15h10",
  audio: "M9 18V5l12-2v13M9 18a3 3 0 1 1-6 0 3 3 0 0 1 6 0zM21 16a3 3 0 1 1-6 0 3 3 0 0 1 6 0z",
  framing: "M4 8V4h4M16 4h4v4M20 16v4h-4M8 20H4v-4",
  export: "M12 3v12M7 10l5 5 5-5M4 19h16",
};
type Report = { section: Section; state: "Needs input" | "Ready" | "Working" | "Needs attention"; message?: string; dirty?: boolean };
type Action = { section: Section; label: string; focusId?: string };
type Selection = { kind: "slot" | "cue" | "clip"; id: string; label: string; time: number };
const Context = createContext<null | { selection: Selection | null; select: (value: Selection) => void; preview: HTMLDivElement | null; strip: HTMLDivElement | null; footer: HTMLDivElement | null; active: Section; open: (section: Section, focusId?: string) => void; report: (key: string, value: Report | null) => void; next: (value: Action | null) => void }>(null);
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
export function WorkspaceSurface({ name, children }: { name: "preview" | "strip" | "footer"; children: ReactNode }) {
  const workspace = useContext(Context);
  return workspace ? workspace[name] ? createPortal(children, workspace[name]) : null : <>{children}</>;
}
/** The Customize editor: section rail, stage (preview and slot strip) and inspector. Inside the
 * automatic maker it also fills two header slots: a Working badge and a "Render video" shortcut. */
export function GuidedWorkspace({ hasFootage, footageUnavailable = false, headerStatusTarget = null, headerActionTarget = null, children }: { hasFootage: boolean; footageUnavailable?: boolean; headerStatusTarget?: HTMLElement | null; headerActionTarget?: HTMLElement | null; children: ReactNode }) {
  const [active, setActive] = useState<Section>("cuts");
  const [slotSelection, setSlotSelection] = useState<Selection | null>(null), [cueSelection, setCueSelection] = useState<Selection | null>(null);
  const [colorSelection, setColorSelection] = useState<Selection | null>(null);
  const selection = active === "captions" ? cueSelection : active === "style" ? colorSelection ?? slotSelection : active === "cuts" ? slotSelection : null;
  const monitor = useRef<HTMLDivElement | null>(null);
  const [preview, setPreview] = useState<HTMLDivElement | null>(null), [strip, setStrip] = useState<HTMLDivElement | null>(null), [footer, setFooter] = useState<HTMLDivElement | null>(null);
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
  const working = Object.entries(reports).filter(([, value]) => value.state === "Working");
  const activeDirty = Object.values(reports).some(value => value.dirty && value.section === active);
  return <Context.Provider value={{ active, open, report, next: setAction, selection, select, preview, strip, footer }}>
    {headerStatusTarget && !!working.length && createPortal(<Badge state="working" />, headerStatusTarget)}
    {/* Hidden on Export, where the section's own "Render video" button starts the render. */}
    {headerActionTarget && active !== "export" && createPortal(<Button variant="primary" className="editor-render" onClick={() => open("export")}>Render video</Button>, headerActionTarget)}
    <div className="guided-workspace">
      <div className="editor-layout"><nav className="workspace-nav" aria-label="Editing sections"><label className="workspace-section-picker">Editing section<select aria-label="Editing section" value={active} onChange={e => open(e.target.value as Section)}>{Object.entries(sections).map(([key, label]) => <option key={key} value={key}>{label} · {state(key as Section)}</option>)}</select></label>
        <div className="workspace-section-buttons">{Object.entries(sections).map(([key, label]) => { const value = state(key as Section); return <button key={key} aria-label={label} aria-describedby={`section-state-${key}`} data-state={value} aria-current={active === key ? "page" : undefined} onClick={() => open(key as Section)}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d={icons[key as Section]} /></svg><span className="rail-label">{label}</span>
          {(value === "Needs attention" || value === "Unsaved") && <span className="rail-dot" aria-hidden="true" />}<small id={`section-state-${key}`} className="visually-hidden">{value}</small></button>; })}</div></nav>
      <div className="editor-stage">
        {working.map(([key, value]) => <p className="workspace-job" role="status" key={key}>{value.message || "Processing…"} <button onClick={() => open(value.section)}>View {sections[value.section]}</button></p>)}
        <div className="editor-preview" ref={monitorRef} />
        <p className="editor-info">Showing last render · unsaved edits are not previewed</p>
        <p className="editor-next">Next: {next.label}</p>
        <div className="editor-strip" ref={setStrip} />
      </div>
      <div className="editor-inspector"><div className="editor-inspector-body">
        {activeDirty && <Banner tone="warning" role="status" className="editor-unsaved">Unsaved edits in {sections[active]}. Save them in this panel to use them in the next render.</Banner>}
        <p className="editor-scope">{selection && ["cuts", "style", "captions"].includes(active) ? `${selection.label} · ${selection.time.toFixed(2)} s` : active === "cuts" ? "Sequence and selected slot" : active === "captions" ? "Caption track and selected cue" : "Whole project"}</p><h2 className="workspace-section-title" ref={heading} tabIndex={-1}>{sections[active]}</h2>
        {children}
      </div><div className="editor-inspector-footer" ref={setFooter} /></div>
    </div></div>
  </Context.Provider>;
}
