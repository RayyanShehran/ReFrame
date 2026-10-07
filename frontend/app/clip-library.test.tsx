import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ClipLibrary } from "./clip-library";
const MiB = 1024 * 1024;
const metadata = { filename: "old.mp4", size_bytes: 1000, duration_seconds: 1, width: 32, height: 24, video_codec: "h264", has_audio: false, audio_codec: null, frame_rate: 30, validation_status: "accepted", storage_status: "not_retained" };
function library(count = 0, bytes = count * 1000) {
  return { clips: Array.from({ length: count }, (_, i) => ({ id: `11111111-1111-4111-8111-${String(i).padStart(12, "0")}`, name: `Saved ${i}`, primary: i === 0, available: true, sha256: "a".repeat(64), metadata })), total_bytes: bytes, max_clips: 10, max_bytes: 500 * MiB };
}
const ok = (data: unknown) => ({ ok: true, json: async () => data } as Response);
const file = (name: string, size = 1000) => { const f = new File(["video"], name, { type: "video/mp4" }); Object.defineProperty(f, "size", { value: size }); return f; };
async function add(files: File[]) { await waitFor(() => expect(screen.getByLabelText("Add footage")).toBeEnabled()); fireEvent.change(screen.getByLabelText("Add footage"), { target: { files } }); }
afterEach(() => vi.unstubAllGlobals());
it("uploads sequentially, continues after failure, and retries only the failed file", async () => {
  let finish!: (value: Response) => void, retained = library();
  const onSaved = vi.fn();
  const submitted: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (_url: string, options: RequestInit) => {
    if (options.method !== "POST") return ok(retained);
    const name = (options.body as FormData).get("file") as File; submitted.push(name.name);
    if (submitted.length === 1) return new Promise<Response>(resolve => { finish = resolve; });
    expect(onSaved).not.toHaveBeenCalled();
    if (submitted.length === 2) return { ok: false, json: async () => ({ error: { message: "Invalid video content." } }) } as Response;
    retained = library(retained.clips.length + 1); return ok(retained);
  }));
  const view = render(<ClipLibrary projectId="one" sourceKey="1" onSaved={onSaved} />);
  await add([file("first.mp4"), file("bad.mov"), file("third.mp4"), file("remove.mp4")]);
  fireEvent.click(screen.getByRole("button", { name: "Upload queued files" }));
  await waitFor(() => expect(submitted).toEqual(["first.mp4"]));
  expect(screen.getByText("first.mp4 · Uploading")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Remove pending remove.mp4" }));
  retained = library(1); await act(async () => finish(ok(retained)));
  view.rerender(<ClipLibrary projectId="one" sourceKey="after-first-success" initialDetails={metadata as never} onSaved={onSaved} />);
  await screen.findByText("third.mp4 · Succeeded");
  expect(submitted).toEqual(["first.mp4", "bad.mov", "third.mp4"]);
  expect(screen.getByText(/Invalid video content/)).toBeInTheDocument();
  expect(onSaved).toHaveBeenCalledTimes(1);
  onSaved.mockClear();
  fireEvent.click(screen.getByRole("button", { name: "Retry bad.mov" }));
  await screen.findByText("bad.mov · Succeeded");
  expect(submitted).toEqual(["first.mp4", "bad.mov", "third.mp4", "bad.mov"]);
  expect(screen.getByText("3/10 clips · 0.0/500 MiB retained")).toBeInTheDocument();
});
it("aborts on project switching and never submits the remaining old-project files", async () => {
  let finish!: (value: Response) => void, signal!: AbortSignal;
  const posts: string[] = [], saved = vi.fn();
  vi.stubGlobal("fetch", vi.fn(async (url: string, options: RequestInit) => {
    if (options.method !== "POST") return ok(library());
    posts.push(url); signal = options.signal as AbortSignal;
    return new Promise<Response>(resolve => { finish = resolve; });
  }));
  const view = render(<ClipLibrary projectId="one" sourceKey="1" onSaved={saved} />);
  await add([file("one.mp4"), file("two.mp4")]); fireEvent.click(screen.getByRole("button", { name: "Upload queued files" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  view.rerender(<ClipLibrary projectId="two" sourceKey="1" onSaved={saved} />);
  expect(signal.aborted).toBe(true);
  await act(async () => finish(ok(library(1))));
  expect(posts).toHaveLength(1); expect(saved).not.toHaveBeenCalled(); expect(screen.getByLabelText("Upload queue")).toBeEmptyDOMElement();
});
it("counts existing and pending clips when accepting dropped files", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => ok(library(9))));
  render(<ClipLibrary projectId="one" sourceKey="1" onSaved={vi.fn()} />);
  await waitFor(() => expect(screen.getByLabelText("Add footage")).toBeEnabled());
  fireEvent.drop(screen.getByRole("group", { name: "Add footage files" }), { dataTransfer: { files: [file("last.mov"), file("overflow.mp4")] } });
  expect(screen.getByText("last.mov · Pending")).toBeInTheDocument();
  expect(screen.getByText(/This project allows 10 clips/)).toBeInTheDocument();
});
it("counts retained and pending bytes and explains invalid files", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => ok(library(4, 450 * MiB))));
  render(<ClipLibrary projectId="one" sourceKey="1" onSaved={vi.fn()} />);
  await add([file("accepted.mp4", 40 * MiB), file("budget.mov", 11 * MiB), file("large.mp4", 101 * MiB), file("not-video.txt"), file("empty.mp4", 0)]);
  expect(screen.getByText("accepted.mp4 · Pending")).toBeInTheDocument();
  expect(screen.getByText(/would exceed the 500 MiB/)).toBeInTheDocument();
  expect(screen.getByText(/exceeds 100 MiB/)).toBeInTheDocument(); expect(screen.getByText(/Choose an MP4 or MOV/)).toBeInTheDocument(); expect(screen.getByText(/file is empty/)).toBeInTheDocument();
});
it("refreshes server budgets before uploading and leaves successful uploads untouched", async () => {
  let gets = 0; const posts: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (_url: string, options: RequestInit) => {
    if (options.method !== "POST") return ok(library(gets++ ? 9 : 8));
    posts.push(((options.body as FormData).get("file") as File).name); return ok(library(10));
  }));
  render(<ClipLibrary projectId="one" sourceKey="1" onSaved={vi.fn()} />);
  await add([file("first.mp4"), file("second.mp4")]); fireEvent.click(screen.getByRole("button", { name: "Upload queued files" }));
  await screen.findByText(/This project allows 10 clips/);
  expect(posts).toEqual(["first.mp4"]); expect(screen.getByText("first.mp4 · Succeeded")).toBeInTheDocument();
});


it("routes every file from both actual picker inputs into one queue", async () => {
  const posts: string[] = []; let retained = library();
  vi.stubGlobal("fetch", vi.fn(async (_url: string, options: RequestInit) => {
    if (options.method !== "POST") return ok(retained);
    posts.push(((options.body as FormData).get("file") as File).name);
    retained = library(retained.clips.length + 1); return ok(retained);
  }));
  render(<ClipLibrary projectId="one" sourceKey="1" onSaved={vi.fn()} />);
  await waitFor(() => expect(screen.getByLabelText("Video clip")).toBeEnabled());
  expect(screen.getByLabelText("Video clip")).toHaveAttribute("multiple");
  expect(screen.getByLabelText("Add footage")).toHaveAttribute("multiple");
  fireEvent.change(screen.getByLabelText("Video clip"), { target: { files: [file("first.mp4"), file("second.mov")] } });
  await add([file("third.mp4"), file("fourth.mov")]);
  for (const name of ["first.mp4", "second.mov", "third.mp4", "fourth.mov"]) expect(screen.getByText(`${name} · Pending`)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Upload queued files" }));
  await screen.findByText("fourth.mov · Succeeded");
  expect(posts).toEqual(["first.mp4", "second.mov", "third.mp4", "fourth.mov"]);
});
