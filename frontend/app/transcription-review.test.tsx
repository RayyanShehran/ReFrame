import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { CaptionEditor } from "./caption-editor";
import { TranscriptionReview } from "./transcription-review";

const timeline = { mode: "whole", plan_revision: null, duration_seconds: 4 };
const cue = { start: .5, end: 2, text: "Hello world." };
const ready = { status: "ready", message: null, failure_code: null, operation_id: "one", stale: false, proposal: {
  cues: [cue], requested_language: "en", detected_language: "en", binding: { timeline }, model_revision: "pinned",
} };
const ok = (data: unknown) => ({ ok: true, json: async () => data } as Response);
afterEach(() => vi.unstubAllGlobals());

it("generates, reviews and transfers unsaved cues, preserves style and restores saved provenance", async () => {
  let operation: unknown = { ...ready, status: "idle", operation_id: null, proposal: null };
  let captions: unknown = { status: "default", message: null, whole_duration_seconds: 4, track: {
    schema_version: 1, revision: 0, enabled: false, cues: [], style: { color: "yellow", size: "medium", placement: "center" }, provenance: "manual", timeline: null,
  } };
  const saved: unknown[] = [];
  const fetcher = vi.fn(async (url: string, options: RequestInit) => {
    if (url.endsWith("/transcription/apply")) {
      const b = JSON.parse(options.body as string);
      expect(b).toEqual({ operation_id: "one", expected_caption_revision: 0, replace: false, cues: [{ ...cue, text: "Reviewed text." }] });
      return ok({ cues: b.cues, timeline, automatic_proposal_id: "one", provenance: "automatic_transcription" });
    }
    if (url.endsWith("/transcription")) {
      if (options.method === "POST") { expect(JSON.parse(options.body as string)).toEqual({ language: "en", replace: false }); operation = ready; }
      return ok(operation);
    }
    if (options.method === "POST") {
      const b = JSON.parse(options.body as string); saved.push(b);
      expect(b).toEqual(expect.objectContaining({ provenance: "automatic_transcription", automatic_proposal_id: "one", style: { color: "yellow", size: "medium", placement: "center" } }));
      captions = { status: "ready", message: null, whole_duration_seconds: 4, track: { ...b, schema_version: 1, revision: 1, timeline } };
    }
    return ok(captions);
  });
  vi.stubGlobal("fetch", fetcher);
  const plan = { revision: null, ready: false, dirty: false, busy: false, duration: null };
  const view = render(<CaptionEditor projectId="p" planState={plan} onState={vi.fn()} />);
  await waitFor(() => expect(screen.getByRole("button", { name: "Generate caption proposal" })).toBeEnabled());
  fireEvent.change(screen.getByLabelText("Speech language"), { target: { value: "en" } });
  fireEvent.click(screen.getByRole("button", { name: "Generate caption proposal" }));
  fireEvent.change(await screen.findByLabelText("Proposed cue 1 text"), { target: { value: "Reviewed text." } });
  fireEvent.click(screen.getByRole("button", { name: "Use these captions" }));
  expect(await screen.findByLabelText("Cue 1 text")).toHaveValue("Reviewed text.");
  expect(saved).toHaveLength(0);
  expect(screen.getByLabelText("Caption color")).toHaveValue("yellow");
  fireEvent.click(screen.getByRole("button", { name: "Save captions" }));
  await screen.findByText(/Captions saved.*Automatic transcription/);
  view.unmount(); render(<CaptionEditor projectId="p" planState={plan} onState={vi.fn()} />);
  expect(await screen.findByLabelText("Cue 1 text")).toHaveValue("Reviewed text.");
  expect(screen.getByLabelText("Enable captions")).toBeChecked();
});

it("requires replacement confirmation and drops it if the current draft changes", async () => {
  const apply = vi.fn(), fetcher = vi.fn(async (_url: string, options: RequestInit) => {
    if (options.method === "POST") { expect(JSON.parse(options.body as string).replace).toBe(true); return ok({ cues: [cue], timeline, automatic_proposal_id: "one", provenance: "automatic_transcription" }); }
    return ok(ready);
  });
  vi.stubGlobal("fetch", fetcher);
  const props = { projectId: "p", revision: 2, hasText: true, draftToken: "old", busy: false, onApply: apply, onBusy: vi.fn() };
  const view = render(<TranscriptionReview {...props} />);
  fireEvent.click(await screen.findByRole("button", { name: "Use these captions" }));
  expect(apply).not.toHaveBeenCalled();
  view.rerender(<TranscriptionReview {...props} draftToken="changed" />);
  expect(screen.queryByRole("button", { name: "Replace draft with automatic captions" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Use these captions" }));
  fireEvent.click(screen.getByRole("button", { name: "Replace draft with automatic captions" }));
  await waitFor(() => expect(apply).toHaveBeenCalledOnce());
});

it.each([true, false])("blocks applying %s stale or empty proposals", async stale => {
  vi.stubGlobal("fetch", vi.fn(async () => ok({ ...ready, stale, proposal: { ...ready.proposal, cues: stale ? [cue] : [] } })));
  render(<TranscriptionReview projectId="p" revision={0} hasText={false} draftToken="" busy={false} onApply={vi.fn()} onBusy={vi.fn()} />);
  expect(await screen.findByRole("button", { name: "Use these captions" })).toBeDisabled();
});
