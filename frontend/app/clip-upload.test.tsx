import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ClipUpload } from "./clip-upload";

const details = {
  filename: "clip.mp4", size_bytes: 1048576, duration_seconds: 1,
  width: 320, height: 240, video_codec: "h264", has_audio: false,
  audio_codec: null, frame_rate: 30, validation_status: "accepted", storage_status: "not_retained",
};
const ok = (data: unknown) => ({ ok: true, json: async () => data } as Response);
const fail = (message: string) => ({ ok: false, json: async () => ({ error: { code: "unsupported_media", message } }) } as Response);

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

function choose(name = "clip.mp4") {
  const file = new File(["data"], name, { type: "video/mp4" });
  fireEvent.change(screen.getByLabelText("Video clip"), { target: { files: [file] } });
  return file;
}

it("uploads one clip and displays accepted metadata", async () => {
  let resolve!: (response: Response) => void;
  const fetchMock = vi.fn(() => new Promise<Response>((done) => { resolve = done; }));
  vi.stubGlobal("fetch", fetchMock);
  render(<ClipUpload />);
  choose();
  expect(screen.getByText("Selected: clip.mp4")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Inspect clip" }));
  expect(screen.getByText("Uploading and inspecting clip…")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Inspecting…" })).toBeDisabled();
  await act(async () => resolve(ok(details)));
  expect(screen.getByLabelText("Accepted clip details")).toHaveTextContent("320 × 240");
  expect(screen.getByText(/It is not retained or edited/)).toBeInTheDocument();
  const [, options] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
  expect(options.body).toBeInstanceOf(FormData);
  expect((options.body as FormData).get("file")).toBeInstanceOf(File);
  expect(options.headers).toBeUndefined();
});

it("shows rejection, permits retry, and prevents duplicate submission", async () => {
  let resolve!: (response: Response) => void;
  const fetchMock = vi.fn().mockImplementationOnce(() => new Promise<Response>((done) => { resolve = done; })).mockResolvedValueOnce(ok(details));
  vi.stubGlobal("fetch", fetchMock);
  render(<ClipUpload />);
  choose();
  const form = screen.getByRole("button", { name: "Inspect clip" }).closest("form")!;
  fireEvent.submit(form);
  fireEvent.submit(form);
  expect(fetchMock).toHaveBeenCalledTimes(1);
  await act(async () => resolve(fail("The file is not a readable MP4 or MOV video.")));
  expect(screen.getByRole("alert")).toHaveTextContent("The file is not a readable MP4 or MOV video.");
  fireEvent.click(screen.getByRole("button", { name: "Retry inspection" }));
  expect(await screen.findByLabelText("Accepted clip details")).toBeInTheDocument();
});

it("replacing or clearing a file invalidates late responses", async () => {
  let first!: (response: Response) => void;
  const fetchMock = vi.fn().mockImplementationOnce(() => new Promise<Response>((done) => { first = done; })).mockResolvedValueOnce(ok({ ...details, filename: "new.mov" }));
  vi.stubGlobal("fetch", fetchMock);
  render(<ClipUpload />);
  choose();
  fireEvent.click(screen.getByRole("button", { name: "Inspect clip" }));
  const firstSignal = (fetchMock.mock.calls[0][1] as RequestInit).signal as AbortSignal;
  choose("new.mov");
  expect(firstSignal.aborted).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Inspect clip" }));
  expect(await screen.findByLabelText("Accepted clip details")).toHaveTextContent("new.mov");
  await act(async () => first(ok(details)));
  expect(screen.getByLabelText("Accepted clip details")).toHaveTextContent("new.mov");
  fireEvent.click(screen.getByRole("button", { name: "Clear clip" }));
  expect(screen.queryByLabelText("Accepted clip details")).not.toBeInTheDocument();
});

it("times out a stalled clip request and allows retry", async () => {
  vi.useFakeTimers();
  const fetchMock = vi.fn((_url: string, options: RequestInit) => new Promise<Response>((_resolve, reject) => {
    options.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
  }));
  vi.stubGlobal("fetch", fetchMock);
  render(<ClipUpload />);
  choose();
  fireEvent.click(screen.getByRole("button", { name: "Inspect clip" }));
  await act(async () => { await vi.advanceTimersByTimeAsync(45000); });
  expect(screen.getByRole("alert")).toHaveTextContent("Clip inspection took too long. Please retry.");
  expect(screen.getByRole("button", { name: "Retry inspection" })).toBeEnabled();
});

it("clears clip timeout on unmount when fetch ignores abort", () => {
  vi.useFakeTimers();
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
  const view = render(<ClipUpload />);
  choose();
  fireEvent.click(screen.getByRole("button", { name: "Inspect clip" }));
  expect(vi.getTimerCount()).toBeGreaterThan(0);
  view.unmount();
  expect(vi.getTimerCount()).toBe(0);
});
