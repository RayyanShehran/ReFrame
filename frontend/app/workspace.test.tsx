import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Workspace } from "./workspace";

const reference = { provider: "tiktok", video_id: "123", canonical_url: "https://www.tiktok.com/@a/video/123", title: "Reference", author_name: "A", metadata_status: "available", analysis_status: "not_started" };
const clip = { filename: "saved.mp4", size_bytes: 10, duration_seconds: 1, width: 32, height: 24, video_codec: "mpeg4", has_audio: false, audio_codec: null, frame_rate: 10, validation_status: "accepted", storage_status: "retained" };
const first = { id: "11111111-1111-4111-8111-111111111111", name: "First", created_at: "2026-09-30T00:00:00+00:00", updated_at: "2026-09-30T00:00:00+00:00", reference, reference_inspected_at: "2026-09-30T00:00:00+00:00", status: "active", clip_status: "empty", clip: null };
const second = { ...first, id: "22222222-2222-4222-8222-222222222222", name: "Second", reference: { ...reference, title: "Second reference" } };
const ok = (data: unknown, status = 200) => ({ ok: true, status, json: async () => data } as Response);

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); window.history.replaceState(null, "", "/"); });

it("creates, uploads, restores on refresh, and confirms deletion by name", async () => {
  let saved: typeof first | null = null;
  const fetchMock = vi.fn(async (url: string, options: RequestInit = {}) => {
    if (url.endsWith("/api/references/inspect")) return ok(reference);
    if (url.endsWith("/api/projects") && options.method === "POST") { saved = first; return ok(saved, 201); }
    if (url.endsWith("/clip")) { saved = { ...first, clip_status: "ready", clip } as unknown as typeof first; return ok(saved); }
    if (options.method === "DELETE") { saved = null; return ok(null, 204); }
    return ok(url.endsWith("/api/projects") ? saved ? [saved] : [] : saved);
  });
  vi.stubGlobal("fetch", fetchMock);
  const view = render(<Workspace />);
  fireEvent.change(screen.getByLabelText("TikTok video URL"), { target: { value: reference.canonical_url } });
  fireEvent.click(screen.getByRole("button", { name: "Check reference" }));
  await screen.findByText("Reference");
  fireEvent.click(screen.getByRole("button", { name: "Use this reference" }));
  fireEvent.change(screen.getByLabelText("Project name"), { target: { value: "First" } });
  const form = screen.getByRole("button", { name: "Create project" }).closest("form")!;
  fireEvent.submit(form); fireEvent.submit(form);
  await screen.findByLabelText("Video clip");
  expect(fetchMock.mock.calls.filter(([, options]) => options?.method === "POST" && !(options.body instanceof FormData))).toHaveLength(2); // reference + creation
  expect(window.location.search).toContain(first.id);
  fireEvent.change(screen.getByLabelText("Video clip"), { target: { files: [new File(["video"], "saved.mp4", { type: "video/mp4" })] } });
  fireEvent.click(screen.getByRole("button", { name: "Save clip" }));
  await screen.findByLabelText("Accepted clip details");
  expect(screen.getByText(/Your clip is saved locally for this project/)).toBeInTheDocument();
  expect(screen.queryByLabelText("Video clip")).not.toBeInTheDocument();
  view.unmount();
  render(<Workspace />);
  await screen.findByLabelText("Accepted clip details");
  expect(screen.getByText(/Saved metadata snapshot/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Delete First" }));
  expect(screen.getByRole("dialog")).toHaveTextContent("Delete “First”?");
  expect(screen.getByRole("dialog")).toHaveTextContent("uploaded clip");
  expect(fetchMock.mock.calls.some(([, options]) => options?.method === "DELETE")).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Cancel deletion" }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Delete First" }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
  await waitFor(() => expect(screen.queryByRole("button", { name: "Open First" })).not.toBeInTheDocument());
  expect(window.location.search).toBe("");
});

it("aborts uploads and ignores late project responses across navigation", async () => {
  let lateRead!: (value: Response) => void;
  let lateUpload!: (value: Response) => void;
  let reads = 0;
  let uploadSignal!: AbortSignal;
  vi.stubGlobal("fetch", vi.fn((url: string, options: RequestInit = {}) => {
    if (url.endsWith("/api/projects")) return Promise.resolve(ok([first, second]));
    if (url.endsWith("/clip")) { uploadSignal = options.signal as AbortSignal; return new Promise<Response>((done) => { lateUpload = done; }); }
    if (url.endsWith(first.id)) {
      reads++;
      if (reads === 1) return new Promise<Response>((done) => { lateRead = done; });
      return Promise.resolve(ok(first));
    }
    return Promise.resolve(ok(second));
  }));
  render(<Workspace />);
  fireEvent.click(await screen.findByRole("button", { name: "Open First" }));
  await waitFor(() => expect(reads).toBe(1));
  fireEvent.click(screen.getByRole("button", { name: "Open Second" }));
  await screen.findByText("Second reference");
  await act(async () => lateRead(ok(first)));
  expect(screen.queryByText("Reference")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Open First" }));
  await screen.findByText("Reference");
  fireEvent.change(screen.getByLabelText("Video clip"), { target: { files: [new File(["video"], "saved.mp4", { type: "video/mp4" })] } });
  fireEvent.click(screen.getByRole("button", { name: "Save clip" }));
  fireEvent.click(screen.getByRole("button", { name: "Open Second" }));
  expect(uploadSignal.aborted).toBe(true);
  await screen.findByText("Second reference");
  await act(async () => lateUpload(ok({ ...first, clip })));
  expect(screen.queryByLabelText("Accepted clip details")).not.toBeInTheDocument();
  expect(screen.getByText("Second reference")).toBeInTheDocument();
});

it("shows unavailable media, missing project and retryable deletion errors", async () => {
  window.history.replaceState(null, "", `/?project=${first.id}`);
  vi.stubGlobal("fetch", vi.fn(async (url: string, options: RequestInit = {}) => {
    if (options.method === "DELETE") return { ok: false, status: 500, json: async () => ({ error: { message: "Files could not be removed. Retry deletion." } }) } as Response;
    return ok(url.endsWith("/api/projects") ? [first] : { ...first, clip_status: "unavailable" });
  }));
  const view = render(<Workspace />);
  await screen.findByText(/Saved media is unavailable or corrupt/);
  fireEvent.click(screen.getByRole("button", { name: "Delete First" }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
  await screen.findByText("Files could not be removed. Retry deletion.");
  expect(screen.getByRole("button", { name: "Confirm delete" })).toBeEnabled();
  view.unmount();
  vi.stubGlobal("fetch", vi.fn(async (url: string) => url.endsWith("/api/projects") ? ok([]) : { ok: false, status: 404, json: async () => ({ error: { message: "This project no longer exists." } }) } as Response));
  render(<Workspace />);
  await screen.findByText("This project no longer exists.");
});

it("bounds project requests and removes timers on unmount", async () => {
  vi.useFakeTimers();
  vi.stubGlobal("fetch", vi.fn((_url: string, options: RequestInit) => new Promise<Response>((_done, reject) => options.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError"))))));
  const view = render(<Workspace />);
  await act(async () => { await vi.advanceTimersByTimeAsync(20000); });
  expect(screen.getByRole("alert")).toHaveTextContent("Project request took too long.");
  view.unmount();
  expect(vi.getTimerCount()).toBe(0);
});

it("reopens the same project and restores repeated history entries", async () => {
  window.history.replaceState(null, "", `/?project=${first.id}`);
  vi.stubGlobal("fetch", vi.fn(async (url: string) => ok(url.endsWith("/api/projects") ? [first] : first)));
  render(<Workspace />);
  await screen.findByText("Reference");
  fireEvent.click(screen.getByRole("button", { name: "Open First" }));
  await screen.findByText("Reference");
  await act(async () => window.dispatchEvent(new PopStateEvent("popstate")));
  await screen.findByText("Reference");
  expect(screen.getByLabelText("Video clip")).toBeInTheDocument();
});

it("clears project request timers when a stalled fetch ignores abort", () => {
  vi.useFakeTimers();
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
  const view = render(<Workspace />);
  expect(vi.getTimerCount()).toBeGreaterThan(0);
  view.unmount();
  expect(vi.getTimerCount()).toBe(0);
});
