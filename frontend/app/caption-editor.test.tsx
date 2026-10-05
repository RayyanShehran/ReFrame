import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { CaptionEditor } from "./caption-editor";
import { defaultStyle } from "./caption-style-controls";
vi.mock("./transcription-review", () => ({ TranscriptionReview: () => null }));
vi.mock("./caption-style-controls", async original => ({ ...await original<typeof import("./caption-style-controls")>(), FontPicker: () => null }));

const initial = { status: "default", whole_duration_seconds: 4, message: null, track: { schema_version: 1, revision: 0, enabled: false, cues: [] as { start: number; end: number; text: string }[], style: defaultStyle, provenance: "manual", timeline: null as null | { mode: string; plan_revision: number | null; duration_seconds: number } } };
const plan = { revision: 2, ready: true, dirty: false, busy: false, duration: 3 };
const ok = (data: unknown) => ({ ok: true, json: async () => data } as Response);
afterEach(() => vi.unstubAllGlobals());

it("saves appearance edits without changing reviewed automatic text, timing or provenance", async () => {
  const saved = { ...initial, status: "ready", track: { ...initial.track, revision: 2, enabled: true, provenance: "automatic_transcription", automatic_proposal_id: "proposal", cues: [{ start: .3, end: 1.4, text: "Reviewed مرحبا" }], timeline: { mode: "whole", plan_revision: null, duration_seconds: 4 } } };
  vi.stubGlobal("fetch", vi.fn(async (_url: string, options: RequestInit) => {
    if (options.method === "POST") {
      const body = JSON.parse(options.body as string);
      expect(body).toEqual(expect.objectContaining({ expected_revision: 2, automatic_proposal_id: "proposal", provenance: "automatic_transcription", cues: saved.track.cues, confirm_rebind: false }));
      expect(body.style).toEqual({ ...defaultStyle, size_percent: 8, bold: true });
      expect(body).not.toHaveProperty("font_binding");
      return ok({ ...saved, track: { ...saved.track, style: body.style, revision: 3 } });
    }
    return ok(saved);
  }));
  render(<CaptionEditor projectId="one" planState={plan} onState={vi.fn()} />);
  fireEvent.change(await screen.findByLabelText("Text size (% of output height)"), { target: { value: "8" } });
  fireEvent.click(screen.getByLabelText("Bold"));
  fireEvent.click(screen.getByRole("button", { name: "Save captions" }));
  await screen.findByText(/Captions saved.*revision 3/);
  expect(screen.getByLabelText("Cue 1 text")).toHaveValue("Reviewed مرحبا");
});

it("disables stale automatic captions while retaining cues outside the new cut duration", async () => {
  const old = { ...initial, status: "stale", message: "Render current audio before regeneration.", track: { ...initial.track, revision: 1, enabled: true, provenance: "automatic_transcription", automatic_proposal_id: "proposal", cues: [{ start: .5, end: 1, text: "Preserve timing" }], timeline: { mode: "cuts", plan_revision: 1, duration_seconds: 4 } } };
  let result = old;
  vi.stubGlobal("fetch", vi.fn(async (_url: string, options: RequestInit) => {
    if (options.method === "POST") {
      const body = JSON.parse(options.body as string);
      expect(body).toEqual(expect.objectContaining({ enabled: false, confirm_rebind: false, automatic_proposal_id: "proposal", cues: old.track.cues }));
      result = { ...old, track: { ...old.track, revision: 2, enabled: false } };
    }
    return ok(result);
  }));
  const state = vi.fn();
  render(<CaptionEditor projectId="one" planState={{ ...plan, duration: .3, ready: false }} onState={state} />);
  fireEvent.click(await screen.findByLabelText("Enable captions"));
  expect(screen.getByRole("button", { name: "Save captions" })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "Save captions" }));
  await waitFor(() => expect(state).toHaveBeenLastCalledWith(expect.objectContaining({ revision: 2, enabled: false, ready: true, dirty: false })));
  expect(screen.getByLabelText("Cue 1 text")).toHaveValue("Preserve timing");
});

it("previews SRT before replacing, edits, saves and restores plain text", async () => {
  let result = initial;
  const imported = [{ start: .5, end: 1.5, text: "مرحبا {\\b1} <i>" }];
  const fetcher = vi.fn(async (url: string, options: RequestInit) => {
    if (url.endsWith("/import")) { expect(options.body).toBeInstanceOf(File); return ok({ cues: imported }); }
    if (options.method === "POST") {
      const body = JSON.parse(options.body as string);
      expect(body).toEqual({ expected_revision: 0, mode: "whole", expected_plan_revision: null, confirm_rebind: false, enabled: true, style: initial.track.style, provenance: "srt_import", cues: [{ ...imported[0], text: "Edited مرحبا" }] });
      result = { ...initial, status: "ready", track: { ...initial.track, ...body, revision: 1, timeline: { mode: "whole", plan_revision: null, duration_seconds: 4 } } };
    }
    return ok(result);
  });
  vi.stubGlobal("fetch", fetcher);
  const state = vi.fn(), view = render(<CaptionEditor projectId="one" planState={plan} onState={state} />);
  fireEvent.change(await screen.findByLabelText("Import SRT"), { target: { files: [new File(["srt"], "sample.srt")] } });
  const preview = await screen.findByRole("group", { name: "SRT import preview" });
  expect(within(preview).getByText(imported[0].text)).toBeInTheDocument();
  expect(screen.queryByLabelText("Cue 1 text")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Replace draft with imported cues" }));
  fireEvent.change(screen.getByLabelText("Cue 1 text"), { target: { value: "Edited مرحبا" } });
  fireEvent.click(screen.getByLabelText("Enable captions"));
  expect(state).toHaveBeenLastCalledWith(expect.objectContaining({ dirty: true }));
  fireEvent.click(screen.getByRole("button", { name: "Save captions" }));
  await screen.findByText(/Captions saved.*revision 1/);
  view.unmount(); render(<CaptionEditor projectId="one" planState={plan} onState={state} />);
  expect(await screen.findByLabelText("Cue 1 text")).toHaveValue("Edited مرحبا");
  expect(screen.getByLabelText("Enable captions")).toBeChecked();
});

it("requires explicit timeline confirmation, resets it on plan changes and preserves edits on conflict", async () => {
  const result = { ...initial, status: "ready", track: { ...initial.track, enabled: true, revision: 1, cues: [{ start: .5, end: 1, text: "Keep me" }], timeline: { mode: "whole", plan_revision: null, duration_seconds: 4 } } };
  const fetcher = vi.fn(async (_url: string, options: RequestInit) => {
    if (options.method === "POST") {
      expect(JSON.parse(options.body as string)).toEqual(expect.objectContaining({ expected_revision: 1, expected_plan_revision: 3, confirm_rebind: true, cues: result.track.cues }));
      return { ok: false, json: async () => ({ error: { code: "revision_conflict", message: "Captions changed elsewhere." } }) } as Response;
    }
    return ok(result);
  });
  vi.stubGlobal("fetch", fetcher);
  const view = render(<CaptionEditor projectId="one" planState={plan} onState={vi.fn()} />);
  fireEvent.change(await screen.findByLabelText("Caption timeline"), { target: { value: "cuts" } });
  fireEvent.click(screen.getByRole("button", { name: "Save captions" }));
  expect(screen.getByRole("group", { name: "Confirm caption timeline rebind" })).toBeInTheDocument();
  expect(fetcher.mock.calls.filter(c => c[1].method === "POST")).toHaveLength(0);
  view.rerender(<CaptionEditor projectId="one" planState={{ ...plan, revision: 3 }} onState={vi.fn()} />);
  expect(screen.queryByRole("group", { name: "Confirm caption timeline rebind" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Save captions" }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm rebind and save" }));
  expect(await screen.findByText("Captions changed elsewhere.")).toBeInTheDocument();
  expect(screen.getByLabelText("Cue 1 text")).toHaveValue("Keep me");
});

it("blocks invalid cues, respects code-point length and deletes only the draft", async () => {
  const fetcher = vi.fn(async () => ok(initial)); vi.stubGlobal("fetch", fetcher);
  render(<CaptionEditor projectId="one" planState={plan} onState={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "Add cue" }));
  expect(screen.getByRole("button", { name: "Save captions" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Cue 1 text"), { target: { value: "😀".repeat(200) } });
  expect(screen.getByRole("button", { name: "Save captions" })).toBeEnabled();
  fireEvent.change(screen.getByLabelText("Cue 1 end"), { target: { value: "5" } });
  expect(screen.getByRole("button", { name: "Save captions" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Delete cue 1" }));
  await waitFor(() => expect(screen.queryByLabelText("Cue 1 text")).not.toBeInTheDocument());
  expect(fetcher).toHaveBeenCalledTimes(1);
});
