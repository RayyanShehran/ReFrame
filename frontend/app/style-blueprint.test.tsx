import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { StyleBlueprint } from "./style-blueprint";

const blueprint = {
  schema_version: 1, algorithm_version: "encoded-rgb-midpoints-v1", analyzed_at: "today",
  source: { media_sha256: "a".repeat(64), video_id: "123" }, tool_versions: { ffmpeg: "9.0.2" },
  sampling: { successful_samples: 12, timestamps_seconds: Array.from({ length: 12 }, (_, i) => (i + .5) / 12), duration_seconds: 1, frame_width: 96, frame_height: 96, decoded_bytes: 331776 },
  color_metadata: { warnings: ["Missing tags; ordinary SDR assumed."], assumption: "Encoded SDR" },
  color: { rgb_mean: [1, 0, 0], brightness_p05: .2126, brightness_p50: .2126, brightness_p95: .2126, contrast_spread: 0, mean_hsv_saturation: 1, palette: [{ hex: "#ff0000", proportion: 1 }], palette_coverage: 1 },
  interpretation_limits: ["Brightness measures encoded RGB pixels, not physical exposure."],
  other_categories: { pacing: "not_analyzed", transitions: "not_analyzed", captions: "not_analyzed", audio: "not_analyzed" },
};
const response = (status: string, extra = {}) => ({ ok: true, json: async () => ({ status, message: null, failure_code: null, blueprint: status === "ready" ? blueprint : null, ...extra }) } as Response);
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

it("analyzes, polls and restores the read-only blueprint on remount", async () => {
  let started = false; let reads = 0;
  const mock = vi.fn(async (_url, options) => {
    if (options.method === "POST") { started = true; return response("running"); }
    return response(!started ? "idle" : ++reads === 1 ? "running" : "ready");
  });
  vi.stubGlobal("fetch", mock);
  const view = render(<StyleBlueprint projectId="first" />);
  fireEvent.click(await screen.findByRole("button", { name: "Analyze reference" }));
  await screen.findByText(/Sampling reference frames/);
  await screen.findByText(/Color analysis is ready/, {}, { timeout: 4000 });
  expect(screen.getByText(/#ff0000/)).toBeInTheDocument();
  expect(screen.getByText(/12 \/ 12 midpoint samples/)).toBeInTheDocument();
  expect(screen.getByText(/not physical exposure/)).toBeInTheDocument();
  expect(screen.getByRole("note")).toHaveTextContent("SDR assumed");
  expect(screen.getByText(/Pacing, transitions, captions and audio: not analyzed/)).toBeInTheDocument();
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  view.unmount(); render(<StyleBlueprint projectId="first" />);
  await screen.findByText(/Color analysis is ready/);
  expect(mock.mock.calls.filter(([, options]) => options.method === "POST")).toHaveLength(1);
});

it("retries a failed analysis explicitly", async () => {
  let retried = false;
  const mock = vi.fn(async (_url, options) => {
    if (options.method === "POST") retried = true;
    return retried ? response("ready") : response("failed", { message: "Decode failed", failure_code: "decode_failed" });
  });
  vi.stubGlobal("fetch", mock);
  render(<StyleBlueprint projectId="first" />);
  await screen.findByText(/Decode failed/);
  expect(mock.mock.calls.every(([, options]) => options.method === "GET")).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Retry color analysis" }));
  await screen.findByText(/Color analysis is ready/);
});

it("ignores late status after navigating to another project", async () => {
  let late!: (response: Response) => void; let oldSignal!: AbortSignal;
  const mock = vi.fn((url, options) => {
    if (url.includes("/first/")) { oldSignal = options.signal; return new Promise<Response>(resolve => { late = resolve; }); }
    return Promise.resolve(response("idle"));
  });
  vi.stubGlobal("fetch", mock);
  const view = render(<StyleBlueprint key="first" projectId="first" />);
  await waitFor(() => expect(mock).toHaveBeenCalledOnce());
  view.rerender(<StyleBlueprint key="second" projectId="second" />);
  await screen.findByRole("button", { name: "Analyze reference" });
  expect(oldSignal.aborted).toBe(true);
  await act(async () => late(response("ready")));
  expect(screen.queryByText(/Color analysis is ready/)).not.toBeInTheDocument();
  expect(mock.mock.calls.every(([, options]) => options.method === "GET")).toBe(true);
});

it("rejects malformed ready blueprints safely", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => response("ready", { blueprint: { ...blueprint, color: { ...blueprint.color, palette: [{ hex: "javascript:bad", proportion: 1 }] } } })));
  render(<StyleBlueprint projectId="first" />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Invalid color blueprint response");
});
