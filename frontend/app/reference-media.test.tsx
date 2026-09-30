import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ReferenceMedia } from "./reference-media";

const response = (status: string, extra = {}) => ({ ok: true, json: async () => ({ status, operation_id: "op", message: null, media: null, ...extra }) } as Response);
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

it("starts, polls, and displays retained details", async () => {
  let calls = 0;
  const mock = vi.fn(async (_url: string, options: RequestInit) => {
    if (options.method === "POST") return response("running");
    return response(++calls === 1 ? "idle" : calls === 2 ? "running" : "ready", calls > 2 ? { media: { duration_seconds: 1, width: 320, height: 240, size_bytes: 10, video_codec: "hevc", has_audio: false, audio_codec: null, sha256: "a".repeat(64), retrieved_at: "today", versions: { yt_dlp: "2026.8.19" } } } : {});
  });
  vi.stubGlobal("fetch", mock);
  render(<ReferenceMedia projectId="first" />);
  fireEvent.click(await screen.findByRole("button", { name: "Retrieve reference" }));
  await screen.findByText(/Retrieving and validating/);
  await screen.findByText(/Style analysis is not implemented/, {}, { timeout: 4000 });
  expect(screen.getByText("Absent")).toBeInTheDocument();
  expect(mock.mock.calls.filter(([, options]) => options.method === "POST")).toHaveLength(1);
});

it("reopens failed state and retries explicitly", async () => {
  let retried = false;
  vi.stubGlobal("fetch", vi.fn(async (_url, options) => {
    if (options.method === "POST") retried = true;
    return response(retried ? "running" : "failed", { message: "Rate limited", failure_code: "rate_limited" });
  }));
  const view = render(<ReferenceMedia projectId="first" />);
  await screen.findByText(/Rate limited/);
  fireEvent.click(screen.getByRole("button", { name: "Retry reference retrieval" }));
  await screen.findByText(/Retrieving and validating/);
  view.unmount();
});

it("navigation aborts polling and ignores a late response without cancelling server work", async () => {
  let resolve!: (value: Response) => void;
  let signal!: AbortSignal;
  const mock = vi.fn((_url, options) => {
    signal = options.signal;
    return new Promise<Response>(done => { resolve = done; });
  });
  vi.stubGlobal("fetch", mock);
  const view = render(<ReferenceMedia projectId="first" />);
  await waitFor(() => expect(mock).toHaveBeenCalledOnce());
  view.unmount();
  expect(signal.aborted).toBe(true);
  await act(async () => resolve(response("running")));
  expect(mock).toHaveBeenCalledOnce();
  expect(mock.mock.calls[0][1].method).toBe("GET");
});

it("bounds status polling", async () => {
  vi.useFakeTimers();
  const mock = vi.fn(async () => response("running"));
  vi.stubGlobal("fetch", mock);
  const view = render(<ReferenceMedia projectId="first" />);
  await act(async () => { await vi.advanceTimersByTimeAsync(182000); });
  expect(screen.getByRole("alert")).toHaveTextContent("Status polling stopped");
  const count = mock.mock.calls.length;
  await act(async () => { await vi.advanceTimersByTimeAsync(20000); });
  expect(mock).toHaveBeenCalledTimes(count);
  view.unmount();
});
