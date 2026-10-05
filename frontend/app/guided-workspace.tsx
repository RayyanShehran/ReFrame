"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";

export const sections = { reference: "Reference", footage: "Footage", style: "Style & cuts", audio: "Audio & captions", export: "Export" };
export type Section = keyof typeof sections;
type Report = { section: Section; state: "Needs input" | "Ready" | "Working" | "Needs attention"; message?: string };
type Action = { section: Section; label: string };
const Context = createContext<null | { active: Section; open: (section: Section) => void; report: (key: string, value: Report | null) => void; next: (value: Action | null) => void }>(null);
export function useWorkspaceNavigation() { return useContext(Context); }
export function useWorkspaceReport(key: string, section: Section, state: Report["state"], message?: string) {
  const report = useContext(Context)?.report;
  useEffect(() => { report?.(key, { section, state, message }); }, [report, key, section, state, message]);
  useEffect(() => () => report?.(key, null), [report, key]);
}
export function WorkspaceSection({ section, children }: { section: Section; children: ReactNode }) {
  const workspace = useContext(Context);
  return <div hidden={!!workspace && workspace.active !== section}>{children}</div>;
}
export function GuidedWorkspace({ hasFootage, footageUnavailable = false, children }: { hasFootage: boolean; footageUnavailable?: boolean; children: ReactNode }) {
  const [active, setActive] = useState<Section>("reference");
  const [reports, setReports] = useState<Record<string, Report>>({});
  const [action, setAction] = useState<Action | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const open = useCallback((section: Section) => { setActive(section); requestAnimationFrame(() => heading.current?.focus()); }, []);
  const report = useCallback((key: string, value: Report | null) => setReports(previous => {
    const updated = { ...previous };
    if (value) updated[key] = value; else delete updated[key];
    return updated;
  }), []);
  const retrieval = reports["reference-media"];
  const referenceColor = reports["style-blueprint"], footageColor = reports["footage-color"];
  const next = footageUnavailable ? { section: "footage" as const, label: "Review unavailable footage" }
    : !hasFootage ? { section: "footage" as const, label: "Upload your footage" }
    : retrieval?.state !== "Ready" ? { section: "reference" as const, label: retrieval?.state === "Working" ? "View reference retrieval" : "Retrieve your reference" }
    : referenceColor?.state !== "Ready" || footageColor?.state !== "Ready" ? { section: "style" as const, label: "Analyze reference and footage colors" }
    : action ?? { section: "style" as const, label: "Review your color recipe" };
  function state(section: Section) {
    const values = Object.values(reports).filter(value => value.section === section);
    if (values.some(value => value.state === "Working")) return "Working";
    if (values.some(value => value.state === "Needs attention")) return "Needs attention";
    if (section === "footage") return footageUnavailable ? "Needs attention" : hasFootage ? "Ready" : "Needs input";
    if (section === "style") return reports.recipe?.state ?? "Needs input";
    const required = section === "audio" ? values.filter(value => value !== reports.transcription) : values;
    return required.length && required.every(value => value.state === "Ready") ? "Ready" : "Needs input";
  }
  return <Context.Provider value={{ active, open, report, next: setAction }}>
    <div className="guided-workspace">
      <div className="workspace-guidance"><p>Next step</p><button onClick={() => open(next.section)}>{next.label}</button><p className="hint">Open a section to continue. Switching sections keeps unsaved edits; refresh restores saved settings only. Cuts and captions are optional.</p></div>
      {Object.entries(reports).filter(([, value]) => value.state === "Working").map(([key, value]) => <p className="workspace-job" role="status" key={key}>{value.message || "Processing…"} <button onClick={() => open(value.section)}>View {sections[value.section]}</button></p>)}
      <nav className="workspace-nav" aria-label="Editing sections">{Object.entries(sections).map(([key, label]) => <button key={key} aria-label={label} aria-describedby={`section-state-${key}`} aria-current={active === key ? "step" : undefined} onClick={() => open(key as Section)}>{label}<small id={`section-state-${key}`}>{state(key as Section)}</small></button>)}</nav>
      <h2 ref={heading} tabIndex={-1}>{sections[active]}</h2>
      {children}
    </div>
  </Context.Provider>;
}
