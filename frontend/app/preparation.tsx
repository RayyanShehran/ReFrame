"use client";

import { createContext, useContext, useEffect, useRef, useState, useCallback, useMemo, type ReactNode } from "react";
import { useWorkspaceNavigation } from "./guided-workspace";
import type { MediaOperation } from "./use-media-operation";

const steps = [
  ["reference-media", "Retrieve reference media"],
  ["style-blueprint", "Analyze reference color"],
  ["footage-color", "Analyze footage color"],
  ["pacing", "Analyze reference pacing (optional)"],
] as const;
type Controls = { operation: MediaOperation | null; error: string; starting: boolean; revision: number; start: () => Promise<MediaOperation | undefined> };
const Context = createContext<null | ((route: string, controls: Controls | null) => void)>(null);
export function usePreparationOperation(route: string, controls: Controls) {
  const register = useContext(Context);
  const { operation, error, starting, start, revision } = controls;
  useEffect(() => {
    if (steps.some(([key]) => key === route)) register?.(route, { operation, error, starting, start, revision });
  }, [register, route, operation, error, starting, start, revision]);
  useEffect(() => () => register?.(route, null), [register, route]);
}

export function Preparation({ hasFootage, children }: { hasFootage: boolean; children: ReactNode }) {
  // ponytail: session-only continuation; persist orchestration only if cross-session automation is required.
  const [operations, setOperations] = useState<Record<string, Controls>>({});
  const [pacing, setPacing] = useState(false), [active, setActive] = useState(false);
  const [current, setCurrent] = useState<string | null>(null), [message, setMessage] = useState("");
  const [completed, setCompleted] = useState<Record<string, string>>({});
  const [stopRequested, setStopRequested] = useState(false);
  const [failed, setFailed] = useState(false), [waiting, setWaiting] = useState(false);
  const live = useRef(true), owned = useRef(false), stop = useRef(false), launched = useRef<string | null>(null);
  const expectedRevision = useRef(0), observedRunning = useRef<string | null>(null);
  const navigation = useWorkspaceNavigation();
  const register = useCallback((route: string, controls: Controls | null) => setOperations(previous => {
    if (!steps.some(([key]) => key === route)) return previous;
    const updated = { ...previous };
    if (controls) updated[route] = controls; else delete updated[route];
    return updated;
  }), []);
  useEffect(() => { live.current = true; return () => { live.current = false; owned.current = false; stop.current = true; }; }, []);
  const selected = useMemo(() => steps.slice(0, pacing ? 4 : 3), [pacing]);
  useEffect(() => {
    let superseded = false;
    // Coalesce registered hook updates before deciding whether another step may launch.
    queueMicrotask(() => {
    if (superseded || !active || waiting || !live.current || !owned.current) return;
    if (stop.current && !current) { owned.current = false; setActive(false); setMessage("Stopped. Saved work is retained; Continue explicitly to prepare missing steps."); return; }
    const step = selected.find(([key]) => !completed[key]);
    if (!step) { owned.current = false; setActive(false); setCurrent(null); setMessage("Analysis complete. Generate or review your color recipe; settings and rendering need your explicit action."); return; }
    const [key, label] = step, control = operations[key];
    if (!control || (!control.operation && !control.error)) { setCurrent(key); return; }
    if (current !== key) setCurrent(key);
    if (control.starting) return;
    if (launched.current === key && control.revision < expectedRevision.current) return;
    if (launched.current === key || observedRunning.current === key) {
      if (control.error || control.operation?.status === "failed") {
        owned.current = false; setActive(false); setFailed(true); setMessage(`${label} failed: ${control.error || control.operation?.message || "Retry this step explicitly."} Successful work is retained. Retry preparation, or open the step’s section for recovery.`); return;
      }
    }
    if (control.operation?.status === "running") { observedRunning.current = key; return; }
    if (!control.error && control.operation?.status === "ready") {
      setCompleted(previous => ({ ...previous, [key]: launched.current === key ? "Completed" : "Reused saved result" }));
      setCurrent(null); return;
    }
    if (stop.current) { owned.current = false; setActive(false); setCurrent(null); setMessage("Stopped before launching another step. Continue explicitly when ready."); return; }
    if (launched.current === key) return;
    launched.current = key; expectedRevision.current = control.revision + 1; setWaiting(true);
    void control.start().then(result => {
      if (!live.current || !owned.current) return;
      if (!result) { owned.current = false; setActive(false); setFailed(true); setMessage(`${label} could not start. Check the step’s error, then Retry preparation explicitly; saved work is retained.`); }
    }).finally(() => { if (live.current) setWaiting(false); });
    });
    return () => { superseded = true; };
  }, [active, waiting, operations, completed, current, selected]);
  function begin() {
    if (owned.current || !hasFootage) return;
    owned.current = true; stop.current = false; setStopRequested(false); launched.current = null; observedRunning.current = null;
    // Revalidate through restored operation status; reuse ready results, never regenerate creative settings.
    setCompleted({}); setCurrent(null); setFailed(false); setMessage(""); setActive(true);
  }
  const allReady = selected.every(([key]) => !operations[key]?.error && operations[key]?.operation?.status === "ready");
  return <Context.Provider value={register}>
    <section id="prepare-workflow" tabIndex={-1} className="reference-section" aria-label="Coordinated preparation">
      <h3>Prepare reference and footage</h3>
      <p>One explicit action prepares the listed analyses in order. Valid saved results are reused.</p>
      <ol>{selected.map(([key, label]) => <li key={key}>{label} — {completed[key] || (current === key && active ? operations[key]?.operation?.status === "running" ? "Working" : "Checking / starting" : operations[key]?.operation?.status === "ready" && !operations[key]?.error ? "Ready to reuse" : operations[key]?.operation?.status === "running" ? "Already running; Continue to follow it" : operations[key]?.error || operations[key]?.operation?.status === "failed" ? "Needs explicit retry / recovery" : "If needed")}</li>)}</ol>
      <label><input type="checkbox" checked={pacing} disabled={active} onChange={e => setPacing(e.target.checked)} /> Include optional reference pacing for cuts</label>
      {!hasFootage && <p>Upload footage first. <button onClick={() => navigation?.open("footage")}>Open Footage</button></p>}
      <button disabled={active || !hasFootage} onClick={begin}>{active ? "Preparing…" : failed ? "Retry preparation" : message || selected.some(([key]) => operations[key]?.operation?.status === "running" || operations[key]?.operation?.status === "ready") ? "Continue preparation" : "Prepare reference and footage"}</button>
      {active && <button disabled={stopRequested} onClick={() => { stop.current = true; setStopRequested(true); setMessage("Stop requested: the current backend step continues. No subsequent step will launch."); }}>Stop after current step</button>}
      {message && <p role={failed ? "alert" : "status"}>{message}</p>}
      {(allReady || (!active && Object.keys(completed).length >= 3)) && <button onClick={() => navigation?.open("style")}>Generate or review color recipe</button>}
      {failed && <button onClick={() => navigation?.open(current === "reference-media" ? "reference" : "style")}>Open failed step</button>}
      <p className="hint">Leaving or reloading stops automatic continuation. The current backend operation follows its existing lifecycle; reopen and Continue explicitly. This does not generate recipes/plans, replace captions, accept suggestions, run OCR/transcription or render video.</p>
    </section>
    {children}
  </Context.Provider>;
}
