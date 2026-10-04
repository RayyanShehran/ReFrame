import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { EditPlan } from "./edit-plan";

const segments = [0, 45, 90].map((start, i) => ({ source_start_frame: start, source_end_frame: start + 30, output_start_frame: i * 30, output_end_frame: (i + 1) * 30 }));
const saved = { status: "ready", message: null, max_duration_seconds: 3, plan: { schema_version: 1, fps: 30, revision: 1, output_frames: 90, footage_frames: 120, requested_duration_seconds: 3, segments, suggested_source_starts: [0, 45, 90], merged_subframe_intervals: 0 } };
const ok = (data: unknown) => ({ ok: true, json: async () => data } as Response);
afterEach(() => vi.unstubAllGlobals());

it("generates, validates edits, saves only starts and revision, and restores", async () => {
  let result: unknown = { status: "empty", message: null, max_duration_seconds: 3, plan: null };
  const onState = vi.fn();
  vi.stubGlobal("fetch", vi.fn(async (url: string, options: RequestInit) => {
    if (options.method === "POST") {
      const body = JSON.parse(options.body as string);
      if (url.endsWith("/generate")) { expect(body).toEqual({ expected_revision: 0, replace: false, output_duration_seconds: 3 }); result = saved; }
      else { expect(body).toEqual({ expected_revision: 1, source_starts_seconds: [0, 2, 3] }); result = { ...saved, plan: { ...saved.plan, revision: 2, segments: segments.map((s, i) => i === 1 ? { ...s, source_start_frame: 60, source_end_frame: 90 } : s) } }; }
    }
    return ok(result);
  }));
  const view = render(<EditPlan projectId="first" recipeReady onState={onState} />);
  fireEvent.click(await screen.findByRole("button", { name: "Generate cut plan" }));
  const input = await screen.findByRole("spinbutton", { name: "Segment 2 source start" });
  fireEvent.change(input, { target: { value: ".5" } });
  expect(screen.getByRole("button", { name: "Save cut plan" })).toBeDisabled();
  fireEvent.change(input, { target: { value: "2" } });
  fireEvent.click(screen.getByRole("button", { name: "Save cut plan" }));
  await waitFor(() => expect(onState).toHaveBeenLastCalledWith({ revision: 2, ready: true, dirty: false, busy: false, duration: 3 }));
  view.unmount();
  render(<EditPlan projectId="first" recipeReady onState={onState} />);
  expect(await screen.findByRole("spinbutton", { name: "Segment 2 source start" })).toHaveValue(2);
});

it("preserves unsaved edits on conflict and requires explicit replacement", async () => {
  vi.stubGlobal("fetch", vi.fn(async (_url: string, options: RequestInit) => options.method === "POST"
    ? { ok: false, json: async () => ({ error: { code: "revision_conflict", message: "Cut plan changed elsewhere." } }) } as Response : ok(saved)));
  render(<EditPlan projectId="first" recipeReady onState={vi.fn()} />);
  const input = await screen.findByRole("spinbutton", { name: "Segment 2 source start" });
  fireEvent.change(input, { target: { value: "2" } });
  fireEvent.click(screen.getByRole("button", { name: "Save cut plan" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Cut plan changed elsewhere.");
  expect(input).toHaveValue(2);
  expect(screen.getByText(/Unsaved cut changes/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Regenerate cut plan" }));
  expect(screen.getByRole("group", { name: "Confirm cut plan replacement" })).toBeInTheDocument();
});

it("shows stale saved ranges and explains continuous footage", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => ok({ ...saved, status: "stale", message: "Pacing changed.", plan: { ...saved.plan, segments: segments.map((s, i) => ({ ...s, source_start_frame: i * 30, source_end_frame: (i + 1) * 30 })) } })));
  render(<EditPlan projectId="first" recipeReady={false} onState={vi.fn()} />);
  expect(await screen.findByText(/Stale cut plan/)).toBeInTheDocument();
  expect(screen.getByRole("spinbutton", { name: "Segment 1 source start" })).toBeDisabled();
  expect(screen.getByText(/boundaries alone do not create visible jumps/)).toBeInTheDocument();
});
