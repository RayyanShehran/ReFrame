import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Connectivity } from "./connectivity";

afterEach(() => vi.unstubAllGlobals());

it("shows checking then connected after a valid health response", async () => {
  let resolve!: (value: Response) => void;
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((done) => { resolve = done; })));
  render(<Connectivity />);
  expect(screen.getByText("API checking…")).toBeInTheDocument();
  resolve({ ok: true, json: async () => ({ status: "ok", service: "reframe-api" }) } as Response);
  expect(await screen.findByText("API connected")).toBeInTheDocument();
});

it.each([
  ["network failure", () => Promise.reject(new Error("offline"))],
  ["HTTP failure", () => Promise.resolve({ ok: false } as Response)],
  ["invalid data", () => Promise.resolve({ ok: true, json: async () => ({ status: "ok", service: "other" }) } as Response)],
])("shows unavailable for %s and recovers on retry", async (_name, fail) => {
  const fetchMock = vi.fn().mockImplementationOnce(fail).mockResolvedValue({ ok: true, json: async () => ({ status: "ok", service: "reframe-api" }) });
  vi.stubGlobal("fetch", fetchMock);
  render(<Connectivity />);
  expect(await screen.findByText("API unavailable")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  expect(await screen.findByText("API connected")).toBeInTheDocument();
});

it("times out a stalled request", async () => {
  vi.useFakeTimers();
  vi.stubGlobal("fetch", vi.fn((_url: string, options: RequestInit) => new Promise((_resolve, reject) => {
    options.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
  })));
  render(<Connectivity />);
  await act(async () => { await vi.advanceTimersByTimeAsync(4000); });
  expect(screen.getByText("API unavailable")).toBeInTheDocument();
  vi.useRealTimers();
});
