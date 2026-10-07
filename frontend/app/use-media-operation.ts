"use client";

import { usePreparationOperation } from "./preparation";
import { useWorkspaceReport, type Section } from "./guided-workspace";
import { useCallback, useEffect, useRef, useState } from "react";

export type MediaOperation = { status: "idle" | "running" | "ready" | "failed"; message: string | null; failure_code: string | null };
const apiBase = (process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

export function useMediaOperation<T extends MediaOperation>(projectId: string, route: string, validate: (data: unknown) => T, pollingSeconds = 180) {
  const [operation, setOperation] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [starting, setStarting] = useState(false);
  const section: Section = route === "reference-media" ? "reference" : route === "render" ? "export" : route === "transcription" ? "audio" : "style";
  const labels: Record<string, string> = { "reference-media": "Retrieving reference…", "style-blueprint": "Analyzing reference colors…", "footage-color": "Analyzing footage colors…", pacing: "Analyzing reference pacing…", render: "Rendering video…", transcription: "Generating caption proposal…" };
  useWorkspaceReport(route, section, starting || operation?.status === "running" ? "Working" : error || operation?.status === "failed" ? "Needs attention" : operation?.status === "ready" ? "Ready" : "Needs input", labels[route] || "Processing…");
  const action = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const pollEpoch = useRef(0);
  const request = useCallback(async (method: string, controller: AbortController, body?: unknown, suffix = ""): Promise<T> => {
    const timer = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch(`${apiBase}/api/projects/${encodeURIComponent(projectId)}/${route}${suffix}`, { method, signal: controller.signal,
        ...(body ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}) });
      const data = await response.json();
      if (!response.ok) throw new Error(data?.error?.message || "Media request failed.");
      return validate(data);
    } finally { clearTimeout(timer); }
  }, [projectId, route, validate]);

  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController;
    const deadline = Date.now() + pollingSeconds * 1000;
    async function poll() {
      const epoch = pollEpoch.current;
      controller = new AbortController();
      try {
        const result = await request("GET", controller);
        if (!live || epoch !== pollEpoch.current) return;
        setOperation(result); setError("");
        if (result.status === "running") {
          if (Date.now() < deadline) timer = setTimeout(poll, 2000);
          else setError("Status polling stopped. Reopen this project to check; server processing continues.");
        }
      } catch (cause) {
        if (live && epoch === pollEpoch.current) setError(cause instanceof Error && cause.name !== "AbortError" ? cause.message : "Status request timed out. Reopen the project to check.");
      }
    }
    void poll();
    return () => { live = false; clearTimeout(timer); controller?.abort(); };
  }, [request, revision, pollingSeconds]);

  useEffect(() => {
    const version = generation;
    return () => { version.current++; action.current?.abort(); };
  }, [projectId, route]);

  const start = useCallback(async (body?: unknown, suffix = "") => {
    if (action.current) return;
    const controller = new AbortController();
    action.current = controller;
    pollEpoch.current++;
    const version = generation.current;
    setStarting(true); setError("");
    try {
      const result = await request("POST", controller, body, suffix);
      if (version === generation.current) { setOperation(result); setRevision(value => value + 1); return result; }
    } catch (cause) {
      if (version === generation.current) setError(cause instanceof Error && cause.name !== "AbortError" ? cause.message : "Start response timed out. Reopen to check before retrying.");
    } finally {
      if (action.current === controller) action.current = null;
      if (version === generation.current) setStarting(false);
    }
  }, [request]);
  const refresh = useCallback(() => setRevision(value => value + 1), []);
  const controls = { operation, error, starting, start, revision, refresh };
  usePreparationOperation(route, controls);
  return controls;
}
