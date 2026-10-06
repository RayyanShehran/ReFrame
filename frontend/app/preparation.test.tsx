import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Preparation } from "./preparation";
import { useMediaOperation, type MediaOperation } from "./use-media-operation";

const routes = ["reference-media", "style-blueprint", "footage-color", "pacing"];
const parse = (value: unknown) => value as MediaOperation;
function Operations({ id = "one" }: { id?: string }) {
  useMediaOperation(id, routes[0], parse); useMediaOperation(id, routes[1], parse);
  useMediaOperation(id, routes[2], parse); useMediaOperation(id, routes[3], parse);
  return null;
}
const response = (status: MediaOperation["status"], message: string | null = null) => ({ ok: true, json: async () => ({ status, message, failure_code: null }) } as Response);
const posts = (fetcher: ReturnType<typeof vi.fn>) => fetcher.mock.calls.filter(([, options]) => options?.method === "POST").map(([url]) => String(url).split("/").pop());
afterEach(() => vi.unstubAllGlobals());

it("runs sequentially, reuses ready work, and requires explicit continuation after reopening", async () => {
  const fetcher = vi.fn(async (url: string, options?: RequestInit) => response(options?.method === "POST" || url.endsWith("reference-media") ? "ready" : "idle")); vi.stubGlobal("fetch", fetcher);
  const view = render(<Preparation hasFootage><Operations /></Preparation>);
  await screen.findByText(/Ready to reuse/); expect(posts(fetcher)).toEqual([]);
  fireEvent.click(screen.getByRole("button", { name: "Continue preparation" }));
  await screen.findByText(/Analysis complete/);
  expect(posts(fetcher)).toEqual(["style-blueprint", "footage-color"]);
  expect(screen.getByText(/Retrieve reference media — Reused/)).toBeInTheDocument();
  view.unmount(); render(<Preparation hasFootage><Operations /></Preparation>);
  await screen.findByText(/Ready to reuse/); expect(posts(fetcher)).toHaveLength(2);
});

it("stops on failure and retries only failed/missing steps; ignores double clicks", async () => {
  const states = new Map(routes.map(key => [key, "idle"])); let fail = true;
  const fetcher = vi.fn(async (url: string, options?: RequestInit) => {
    const key = url.split("/").pop()!;
    if (options?.method === "POST") {
      if (key === "footage-color" && fail) { fail = false; states.set(key, "failed"); return response("failed", "Restore valid footage."); }
      states.set(key, "ready");
    }
    return response(states.get(key) as MediaOperation["status"]);
  }); vi.stubGlobal("fetch", fetcher);
  render(<Preparation hasFootage><Operations /></Preparation>);
  const button = screen.getByRole("button", { name: "Prepare reference and footage" });
  fireEvent.click(button); fireEvent.click(button);
  await screen.findByRole("alert"); expect(posts(fetcher)).toEqual(["reference-media", "style-blueprint", "footage-color"]);
  fireEvent.click(screen.getByRole("button", { name: "Retry preparation" }));
  await screen.findByText(/Analysis complete/); expect(posts(fetcher)).toEqual(["reference-media", "style-blueprint", "footage-color", "footage-color"]);
});

it("stops after the current step without cancelling its backend request", async () => {
  let finish!: (value: Response) => void; let signal: AbortSignal | null | undefined;
  const fetcher = vi.fn(async (_url: string, options?: RequestInit) => {
    if (options?.method === "POST") { signal = options.signal; return new Promise<Response>(resolve => { finish = resolve; }); }
    return response("idle");
  }); vi.stubGlobal("fetch", fetcher);
  render(<Preparation hasFootage><Operations /></Preparation>);
  fireEvent.click(screen.getByRole("button", { name: "Prepare reference and footage" }));
  await waitFor(() => expect(posts(fetcher)).toHaveLength(1));
  fireEvent.click(screen.getByRole("button", { name: "Stop after current step" }));
  expect(signal?.aborted).toBe(false); finish(response("ready"));
  await screen.findByText(/Stopped\. Saved work/); expect(posts(fetcher)).toEqual(["reference-media"]);
});

it("project switch and unmount cannot continue after a late start response", async () => {
  let finish!: (value: Response) => void;
  const fetcher = vi.fn(async (_url: string, options?: RequestInit) => options?.method === "POST" ? new Promise<Response>(resolve => { finish = resolve; }) : response("idle")); vi.stubGlobal("fetch", fetcher);
  const view = render(<Preparation key="one" hasFootage><Operations /></Preparation>);
  fireEvent.click(screen.getByRole("button", { name: "Prepare reference and footage" }));
  await waitFor(() => expect(posts(fetcher)).toHaveLength(1));
  view.rerender(<Preparation key="two" hasFootage><Operations id="two" /></Preparation>); finish(response("ready"));
  await screen.findByRole("button", { name: "Prepare reference and footage" }); expect(posts(fetcher)).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "Prepare reference and footage" }));
  await waitFor(() => expect(posts(fetcher)).toHaveLength(2)); view.unmount(); finish(response("ready"));
  await waitFor(() => expect(posts(fetcher)).toHaveLength(2));
});


it("follows an existing running step only after Continue and stops if it fails", async () => {
  let reads = 0;
  const fetcher = vi.fn(async (url: string, options?: RequestInit) => {
    if (url.endsWith("reference-media")) return response(++reads === 1 ? "running" : "failed", "Retrieval interrupted. Retry explicitly.");
    return response(options?.method === "POST" ? "ready" : "idle");
  }); vi.stubGlobal("fetch", fetcher);
  render(<Preparation hasFootage><Operations /></Preparation>);
  await screen.findByText(/Already running/); expect(posts(fetcher)).toEqual([]);
  fireEvent.click(screen.getByRole("button", { name: "Continue preparation" }));
  await screen.findByRole("alert", {}, { timeout: 3500 }); expect(posts(fetcher)).toEqual([]);
});
