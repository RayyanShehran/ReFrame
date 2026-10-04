import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AudioChoices } from "./audio-choices";

const initial = { status: "default", settings: { schema_version: 1, revision: 0, mode: "original", original_volume: 100, reference_volume: 100, reference_offset_seconds: 0 },
  availability: { original_has_audio: true, reference_has_audio: true, reference_duration_seconds: 3 }, message: null };
const ok = (data: unknown) => ({ ok: true, json: async () => data } as Response);
afterEach(() => vi.unstubAllGlobals());

it("suggests mix gains, saves only choices and revision, then restores after remount", async () => {
  let result = initial;
  const fetcher = vi.fn(async (_url: string, options: RequestInit) => {
    if (options.method === "POST") {
      const body = JSON.parse(options.body as string);
      expect(body).toEqual({ expected_revision: 0, refresh_sources: false, mode: "mix", original_volume: 60, reference_volume: 30, reference_offset_seconds: .5 });
      result = { ...initial, status: "ready", settings: { ...initial.settings, ...body, revision: 1 } };
    }
    return ok(result);
  });
  vi.stubGlobal("fetch", fetcher);
  const state = vi.fn();
  const view = render(<AudioChoices projectId="one" onState={state} />);
  fireEvent.change(await screen.findByRole("combobox", { name: "Audio mode" }), { target: { value: "mix" } });
  expect(screen.getByLabelText("Original volume")).toHaveValue("70");
  expect(screen.getByLabelText("Reference volume")).toHaveValue("30");
  fireEvent.change(screen.getByLabelText("Original volume"), { target: { value: "60" } });
  fireEvent.change(screen.getByLabelText("Reference start offset (seconds)"), { target: { value: ".5" } });
  expect(state).toHaveBeenLastCalledWith(expect.objectContaining({ dirty: true }));
  fireEvent.click(screen.getByRole("button", { name: "Save audio" }));
  expect(await screen.findByText(/Audio saved.*Audio revision 1/)).toBeInTheDocument();
  view.unmount();
  render(<AudioChoices projectId="one" onState={state} />);
  expect(await screen.findByRole("combobox", { name: "Audio mode" })).toHaveValue("mix");
  expect(screen.getByLabelText("Original volume")).toHaveValue("60");
  expect(screen.getByLabelText("Reference start offset (seconds)")).toHaveValue(.5);
});

it("explains missing streams, permits silent original and mute, rejects an endpoint offset", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => ok({ ...initial, availability: { ...initial.availability, original_has_audio: false, reference_has_audio: false } })));
  const view = render(<AudioChoices projectId="one" onState={vi.fn()} />);
  await screen.findByRole("combobox");
  expect(screen.getByRole("option", { name: "Reference audio" })).toBeDisabled();
  expect(screen.getByRole("option", { name: "Mix" })).toBeDisabled();
  expect(screen.getByText(/Original mode remains valid and silent/)).toBeInTheDocument();
  fireEvent.change(screen.getByRole("combobox"), { target: { value: "mute" } });
  expect(screen.getByRole("button", { name: "Save audio" })).toBeEnabled();
  view.unmount();
  vi.stubGlobal("fetch", vi.fn(async () => ok(initial)));
  render(<AudioChoices projectId="two" onState={vi.fn()} />);
  fireEvent.change(await screen.findByRole("combobox"), { target: { value: "reference" } });
  fireEvent.change(screen.getByLabelText("Reference start offset (seconds)"), { target: { value: "3" } });
  expect(screen.getByRole("button", { name: "Save audio" })).toBeDisabled();
});

it("retains edits on conflict and requires explicit rebinding of stale settings", async () => {
  let conflict = true;
  const fetcher = vi.fn(async (_url: string, options: RequestInit) => {
    if (options.method === "POST") {
      if (conflict) return { ok: false, json: async () => ({ error: { code: "revision_conflict", message: "Audio changed elsewhere." } }) } as Response;
      expect(JSON.parse(options.body as string).refresh_sources).toBe(true);
      return ok({ ...initial, status: "ready", settings: { ...initial.settings, mode: "reference", revision: 2 } });
    }
    return ok({ ...initial, status: "stale", settings: { ...initial.settings, mode: "reference", revision: 1 }, message: "Source changed." });
  });
  vi.stubGlobal("fetch", fetcher);
  render(<AudioChoices projectId="one" onState={vi.fn()} />);
  fireEvent.change(await screen.findByLabelText("Reference volume"), { target: { value: "40" } });
  fireEvent.click(screen.getByRole("button", { name: "Save with current sources" }));
  expect(await screen.findByText("Audio changed elsewhere.")).toBeInTheDocument();
  expect(screen.getByLabelText("Reference volume")).toHaveValue("40");
  conflict = false;
  fireEvent.click(screen.getByRole("button", { name: "Save with current sources" }));
  await waitFor(() => expect(screen.getByText(/Audio saved.*Audio revision 2/)).toBeInTheDocument());
});
