import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ProjectColors, colorDifferences } from "./project-colors";
import type { Blueprint } from "./style-blueprint";

const blueprint: Blueprint = {
  schema_version: 1, algorithm_version: "encoded-rgb-midpoints-v1", analyzed_at: "today",
  source: { media_sha256: "a".repeat(64) }, tool_versions: { ffmpeg: "fixture" },
  sampling: { successful_samples: 12, timestamps_seconds: Array.from({ length: 12 }, (_, i) => (i+.5)/12), duration_seconds: 1, frame_width: 96, frame_height: 96, decoded_bytes: 331776 },
  color_metadata: { warnings: [], assumption: "SDR" },
  color: { rgb_mean: [1, 0, 0], brightness_p05: .2126, brightness_p50: .2126, brightness_p95: .2126, contrast_spread: 0, mean_hsv_saturation: 1, palette_coverage: 1, palette: [{ hex: "#ff0000", proportion: 1 }] },
  interpretation_limits: ["Sampled pixels"], other_categories: { pacing: "not_analyzed", transitions: "not_analyzed", captions: "not_analyzed", audio: "not_analyzed" },
};
afterEach(() => vi.unstubAllGlobals());

it("calculates reference minus footage in percentage points", () => {
  expect(colorDifferences(blueprint.color, blueprint.color)).toEqual([0, 0, 0, 0, 0, 0]);
  expect(colorDifferences(blueprint.color, { ...blueprint.color, rgb_mean: [0, 1, 0], brightness_p50: .7152 })).toEqual([expect.closeTo(-50.26), 0, 0, 100, -100, 0]);
});

it("analyzes footage, shows zero comparison differences and restores after remount", async () => {
  let analyzed = false;
  const mock = vi.fn(async (url: string, options: RequestInit) => {
    const isFootage = url.endsWith("/footage-color");
    if (isFootage && options.method === "POST") analyzed = true;
    const status = url.endsWith("/pacing") || (isFootage && !analyzed) ? "idle" : "ready";
    return { ok: true, json: async () => ({ status, message: null, failure_code: null, blueprint: status === "ready" ? blueprint : null, media: { duration_seconds: 1, sha256: "a".repeat(64), versions: {} } }) } as Response;
  });
  vi.stubGlobal("fetch", mock);
  const view = render(<ProjectColors projectId="first" hasFootage />);
  fireEvent.click(await screen.findByRole("button", { name: "Analyze my footage" }));
  const table = await screen.findByRole("table");
  expect(within(table).getAllByText("0.0 pp")).toHaveLength(6);
  expect(screen.getByText(/different scene content/)).toBeInTheDocument();
  view.unmount(); render(<ProjectColors projectId="first" hasFootage />);
  await screen.findByRole("table");
  expect(mock.mock.calls.filter(([, options]) => options.method === "POST")).toHaveLength(1);
});

it("supports footage without reference media and omits failed comparisons", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => ({ ok: true, json: async () => ({ status: url.endsWith("/footage-color") ? "failed" : "idle", message: "Decode failed", failure_code: "decode_failed", blueprint: null }) } as Response)));
  render(<ProjectColors projectId="first" hasFootage />);
  await screen.findByRole("button", { name: "Retry footage analysis" });
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
  expect(screen.getByText(/Retrieve the reference and analyze/)).toBeInTheDocument();
});
