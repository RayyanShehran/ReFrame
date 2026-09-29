"use client";

import { useEffect, useState } from "react";

type Status = "checking" | "connected" | "unavailable";
const apiBase = (process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

export function Connectivity() {
  const [status, setStatus] = useState<Status>("checking");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    const timeout = setTimeout(() => controller.abort(), 4000);

    async function check() {
      try {
        const response = await fetch(`${apiBase}/health`, { signal: controller.signal, cache: "no-store" });
        if (!response.ok) throw new Error("Health request failed");
        const health: unknown = await response.json();
        if (typeof health !== "object" || health === null || !("status" in health) || health.status !== "ok" || !("service" in health) || health.service !== "reframe-api") {
          throw new Error("Invalid health response");
        }
        if (active) setStatus("connected");
      } catch {
        if (active) setStatus("unavailable");
      } finally {
        clearTimeout(timeout);
      }
    }
    void check();
    return () => { active = false; clearTimeout(timeout); controller.abort(); };
  }, [attempt]);

  return <div className="connectivity" role="status" aria-live="polite">
    <span className={`status-dot ${status}`} aria-hidden="true" />
    <span>API {status === "checking" ? "checking…" : status}</span>
    {status === "unavailable" && <button type="button" onClick={() => { setStatus("checking"); setAttempt((value) => value + 1); }}>Retry</button>}
  </div>;
}
