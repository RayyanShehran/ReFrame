import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ReferenceSelection } from "./reference-selection";

const url = "https://www.tiktok.com/@scout2015/video/6718335390845095173";
const details = {
  provider: "tiktok", video_id: "6718335390845095173", canonical_url: url,
  title: "A reference", author_name: "Scout", metadata_status: "available", analysis_status: "not_started",
};
const ok = (body = details) => ({ ok: true, json: async () => body } as Response);
const failure = (message: string) => ({ ok: false, json: async () => ({ error: { code: "invalid_url", message } }) } as Response);

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

function enter(value = url) {
  fireEvent.change(screen.getByRole("textbox", { name: "TikTok video URL" }), { target: { value } });
}

it("loads details, selects, changes and clears the reference", async () => {
  let resolve!: (response: Response) => void;
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((done) => { resolve = done; })));
  render(<ReferenceSelection />);
  enter();
  fireEvent.click(screen.getByRole("button", { name: "Check reference" }));
  expect(screen.getByText("Loading reference details…")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Checking…" })).toBeDisabled();
  await act(async () => resolve(ok()));
  expect(screen.getByText("A reference")).toBeInTheDocument();
  expect(screen.getByText("Creator: Scout")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "View on TikTok" })).toHaveAttribute("href", url);
  expect(screen.getByText("Reference details loaded. Save a project and upload footage, then prepare its analyses.")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Use this reference" }));
  expect(screen.getByLabelText("Selected reference")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Change reference" }));
  expect(screen.queryByText("A reference")).not.toBeInTheDocument();
  enter("https://tiktok.com/@other/video/123");
  expect(screen.getByRole("textbox", { name: "TikTok video URL" })).toHaveValue("https://tiktok.com/@other/video/123");
});

it("shows a safe failure and permits retry", async () => {
  const fetchMock = vi.fn().mockResolvedValueOnce(failure("Enter a full TikTok video URL.")).mockResolvedValueOnce(ok());
  vi.stubGlobal("fetch", fetchMock);
  render(<ReferenceSelection />);
  enter();
  fireEvent.click(screen.getByRole("button", { name: "Check reference" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Enter a full TikTok video URL.");
  fireEvent.click(screen.getByRole("button", { name: "Check reference" }));
  expect(await screen.findByText("A reference")).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

it("shows short-link guidance", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(failure("Open this link in your browser and paste the full TikTok video URL.")));
  render(<ReferenceSelection />);
  enter("https://vm.tiktok.com/abc");
  fireEvent.click(screen.getByRole("button", { name: "Check reference" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Open this link in your browser and paste the full TikTok video URL.");
});

it("invalidates a pending request on edit and ignores its late response", async () => {
  let first!: (response: Response) => void;
  const fetchMock = vi.fn().mockImplementationOnce(() => new Promise<Response>((done) => { first = done; })).mockResolvedValueOnce(ok({ ...details, title: "New reference" }));
  vi.stubGlobal("fetch", fetchMock);
  render(<ReferenceSelection />);
  enter();
  fireEvent.click(screen.getByRole("button", { name: "Check reference" }));
  const firstSignal = fetchMock.mock.calls[0][1].signal as AbortSignal;
  enter("https://www.tiktok.com/@other/video/2");
  expect(firstSignal.aborted).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Check reference" }));
  expect(await screen.findByText("New reference")).toBeInTheDocument();
  await act(async () => first(ok()));
  expect(screen.getByText("New reference")).toBeInTheDocument();
  expect(screen.queryByText("A reference")).not.toBeInTheDocument();
});

it("clears loaded results and blocks duplicate pending submissions", async () => {
  let resolve!: (response: Response) => void;
  const fetchMock = vi.fn(() => new Promise<Response>((done) => { resolve = done; }));
  vi.stubGlobal("fetch", fetchMock);
  render(<ReferenceSelection />);
  enter();
  const form = screen.getByRole("button", { name: "Check reference" }).closest("form")!;
  fireEvent.submit(form);
  fireEvent.submit(form);
  expect(fetchMock).toHaveBeenCalledTimes(1);
  await act(async () => resolve(ok()));
  fireEvent.click(screen.getByRole("button", { name: "Use this reference" }));
  fireEvent.click(screen.getByRole("button", { name: "Clear" }));
  expect(screen.queryByText("A reference")).not.toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "TikTok video URL" })).toHaveValue("");
});

it("aborts pending work on unmount", async () => {
  const fetchMock = vi.fn((_url: string, options: RequestInit) => new Promise<Response>((_resolve, reject) => {
    options.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
  }));
  vi.stubGlobal("fetch", fetchMock);
  const view = render(<ReferenceSelection />);
  enter();
  fireEvent.click(screen.getByRole("button", { name: "Check reference" }));
  const signal = fetchMock.mock.calls[0][1].signal as AbortSignal;
  view.unmount();
  await waitFor(() => expect(signal.aborted).toBe(true));
});

it("times out a stalled reference request", async () => {
  vi.useFakeTimers();
  vi.stubGlobal("fetch", vi.fn((_url: string, options: RequestInit) => new Promise((_resolve, reject) => {
    options.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
  })));
  render(<ReferenceSelection />);
  enter();
  fireEvent.click(screen.getByRole("button", { name: "Check reference" }));
  await act(async () => { await vi.advanceTimersByTimeAsync(15000); });
  expect(screen.getByRole("alert")).toHaveTextContent("Reference check took too long. Please try again.");
});

it("clears the reference timeout when unmounted even if fetch ignores abort", () => {
  vi.useFakeTimers();
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
  const view = render(<ReferenceSelection />);
  enter();
  fireEvent.click(screen.getByRole("button", { name: "Check reference" }));
  expect(vi.getTimerCount()).toBeGreaterThan(0);
  view.unmount();
  expect(vi.getTimerCount()).toBe(0);
});
