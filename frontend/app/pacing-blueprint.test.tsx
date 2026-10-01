import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { PacingBlueprint } from "./pacing-blueprint";

const blueprint = {
  schema_version: 1, algorithm_version: "scdet-consecutive-v1", analyzed_at: "today",
  source: { media_sha256: "a".repeat(64) }, tool_versions: { ffmpeg: "9.0.2" },
  duration_seconds: 7, processing_width: 64, processing_height: 64, threshold: 10,
  successful_frames: 84, candidate_cut_timestamps: [1, 3], estimated_cut_count: 2,
  shot_count: 3, mean_shot_seconds: 7/3, median_shot_seconds: 2, cuts_per_minute: 120/7,
  shots: [{ start_seconds: 0, end_seconds: 1, duration_seconds: 1 },
    { start_seconds: 1, end_seconds: 3, duration_seconds: 2 },
    { start_seconds: 3, end_seconds: 7, duration_seconds: 4 }],
  interpretation_limits: ["Flashes and motion can cause false positives."],
  color_metadata: { warnings: [] }, other_categories: { transitions: "not_analyzed", captions: "not_analyzed", audio: "not_analyzed" },
};
const response = (status: string, extra = {}) => ({ ok: true, json: async () => ({
  status, message: null, failure_code: null, blueprint: status === "ready" ? blueprint : null, ...extra,
}) } as Response);
afterEach(() => vi.unstubAllGlobals());

it("analyzes pacing and restores metrics and contiguous shots without another POST", async () => {
  let started = false;
  const mock = vi.fn(async (_url, options) => {
    if (options.method === "POST") { started = true; return response("running"); }
    return response(started ? "ready" : "idle");
  });
  vi.stubGlobal("fetch", mock);
  const view = render(<PacingBlueprint projectId="first" />);
  fireEvent.click(await screen.findByRole("button", { name: "Analyze pacing" }));
  await screen.findByText(/Estimated cuts and pacing are ready/);
  expect(screen.getByText("2.333 seconds")).toBeInTheDocument();
  expect(screen.getByText("17.14")).toBeInTheDocument();
  fireEvent.click(screen.getByText("Estimated shot durations (3 shots)"));
  expect(screen.getAllByRole("row")).toHaveLength(4);
  expect(screen.getByText(/Flashes and motion/)).toBeInTheDocument();
  view.unmount(); render(<PacingBlueprint projectId="first" />);
  await screen.findByText(/Estimated cuts and pacing are ready/);
  expect(mock.mock.calls.filter(([, options]) => options.method === "POST")).toHaveLength(1);
});

it("requires explicit retry and rejects nonfinite result metrics", async () => {
  let retry = false;
  const mock = vi.fn(async (_url, options) => {
    if (options.method === "POST") retry = true;
    return retry ? response("ready", { blueprint: { ...blueprint, cuts_per_minute: Infinity } }) :
      response("failed", { message: "Detector output missing", failure_code: "detector_output_missing" });
  });
  vi.stubGlobal("fetch", mock);
  render(<PacingBlueprint projectId="first" />);
  await screen.findByText(/Detector output missing/);
  expect(mock.mock.calls.every(([, options]) => options.method === "GET")).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Retry pacing analysis" }));
  await screen.findByText(/Invalid pacing blueprint response/);
  expect(screen.queryByText(/Estimated cuts and pacing are ready/)).not.toBeInTheDocument();
});
