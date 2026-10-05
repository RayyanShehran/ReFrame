import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { FontPicker, defaultStyle, readStyle } from "./caption-style-controls";
import { CaptionAppearancePreview } from "./caption-appearance-preview";
import { CaptionEditor } from "./caption-editor";

const font = { kind: "custom" as const, font_id: "11111111-1111-4111-8111-111111111111", sha256: "a".repeat(64), family: "Licensed fixture", style: "Book" };
const image = { width: 480, height: 270, png_base64: "iVBORw0KGgoAAA==" };
const reference = { status: "ready", operation_id: "22222222-2222-4222-8222-222222222222", media: { sha256: "b".repeat(64), duration_seconds: 4 } };
const track = { revision: 2, enabled: true, font_binding: font, cues: [{ start: 1.2, end: 1.7, text: "مرحبا {\\b1}" }], timeline: { mode: "cuts" as const, plan_revision: 3 } };
const context = { recipeRevision: 2, recipeReady: true, recipeDirty: false, recipeBusy: false, framing: { revision: 1, ready: true, dirty: false, busy: false } };
const plan = { revision: 3, ready: true, dirty: false, busy: false, duration: 3 };
const props = { projectId: "one", track, context, plan, dirty: false, ready: true, disabled: false };
const frame = { schema_version: 1, source: { project_id: "one", media_sha256: "c".repeat(64) }, recipe_revision: 2, framing_revision: 1, caption_revision: 2, font, cue_index: 0, cue_start_seconds: 1.2, cue_end_seconds: 1.7, output_timestamp_seconds: 1.433333333, source_timestamp_seconds: 2.433333333, mode: "cuts", plan_revision: 3, image, warnings: [] };
const ok = (data: unknown) => ({ ok: true, json: async () => data } as Response);
afterEach(() => vi.unstubAllGlobals());

it("uploads only explicitly, requires replacement confirmation and preserves the usable font on failure", async () => {
  const uploaded = vi.fn(), change = vi.fn();
  const fetcher = vi.fn(async (_url: string, options: RequestInit) => options.method === "POST" ? ({ ok: false, json: async () => ({ error: { message: "Invalid font; prior font retained." } }) } as Response) : ok({ revision: 1, font, available: true, message: null }));
  vi.stubGlobal("fetch", fetcher);
  render(<FontPicker projectId="one" revision={2} value="custom" dirty={false} disabled={false} onChange={change} onUploaded={uploaded} />);
  await screen.findByText(/Saved custom font: Licensed fixture/);
  fireEvent.change(screen.getByLabelText("Upload custom font"), { target: { files: [new File(["invalid"], "bad.ttf")] } });
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: "Replace custom font" })).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox", { name: /Replace the saved custom font/ }));
  fireEvent.click(screen.getByRole("button", { name: "Replace custom font" }));
  await screen.findByRole("alert");
  expect(fetcher.mock.calls[1][0]).toContain("expected_revision=1&expected_caption_revision=2&replace=true");
  expect(screen.getByText(/Saved custom font: Licensed fixture/)).toBeVisible();
  expect(uploaded).not.toHaveBeenCalled();
});

it("compares explicit local reference and real saved-cue frames, keeps stale images and directs unsaved changes to Save", async () => {
  const fetcher = vi.fn(async (url: string, options: RequestInit) => {
    if (url.endsWith("reference-media")) return ok(reference);
    if (url.endsWith("reference-frame")) { expect(JSON.parse(options.body as string)).toEqual({ timestamp_seconds: 2.5, expected_reference_operation_id: reference.operation_id }); return ok({ schema_version: 1, project_id: "one", reference_operation_id: reference.operation_id, source: { media_sha256: reference.media.sha256 }, requested_timestamp_seconds: 2.5, timestamp_seconds: 2.5, image, warnings: [] }); }
    expect(JSON.parse(options.body as string)).toEqual({ cue_index: 0, expected_recipe_revision: 2, expected_framing_revision: 1, expected_caption_revision: 2, expected_plan_revision: 3 });
    return ok(frame);
  });
  vi.stubGlobal("fetch", fetcher);
  const view = render(<CaptionAppearancePreview {...props} />);
  await screen.findByText("Retained reference: 4.000 seconds.");
  expect(fetcher).toHaveBeenCalledTimes(1);
  fireEvent.change(screen.getByLabelText("Reference source time (seconds)"), { target: { value: "2.5" } });
  fireEvent.click(screen.getByRole("button", { name: "Inspect reference frame" }));
  await screen.findByRole("img", { name: /Retained reference frame at 2.500/ });
  fireEvent.click(screen.getByRole("button", { name: "Update caption preview" }));
  await screen.findByRole("img", { name: /Actual saved caption preview/ });
  expect(screen.getByText(/Footage source 2.433/)).toBeVisible();
  view.rerender(<CaptionAppearancePreview {...props} track={{ ...track, revision: 3 }} dirty />);
  expect(screen.getByText(/Outdated caption preview/)).toBeVisible();
  expect(screen.getByText(/Save your unsaved caption/)).toBeVisible();
  expect(screen.getByRole("button", { name: "Update caption preview" })).toBeDisabled();
  expect(screen.getAllByRole("img")).toHaveLength(2);
});

it("retains the prior image on failure and discards late results after saved revisions change", async () => {
  let release!: (value: Response) => void;
  let count = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.endsWith("reference-media")) return ok(reference);
    count++;
    if (count === 1) return ok(frame);
    if (count === 2) return { ok: false, json: async () => ({ error: { message: "Font unavailable." } }) } as Response;
    return new Promise<Response>(resolve => { release = resolve; });
  }));
  const view = render(<CaptionAppearancePreview {...props} />);
  await waitFor(() => expect(screen.getByRole("button", { name: "Update caption preview" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Update caption preview" }));
  await screen.findByRole("img", { name: /Actual saved caption preview/ });
  fireEvent.click(screen.getByRole("button", { name: "Update caption preview" }));
  await screen.findByText("Font unavailable.");
  expect(screen.getByRole("img", { name: /Actual saved caption preview/ })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Update caption preview" }));
  view.rerender(<CaptionAppearancePreview {...props} context={{ ...context, recipeRevision: 3 }} />);
  await act(async () => release(ok({ ...frame, image: { ...image, png_base64: "iVBORw0KGgoBBB==" } })));
  expect(screen.getByRole("img", { name: /Actual saved caption preview/ })).toHaveAttribute("src", `data:image/png;base64,${image.png_base64}`);
  expect(screen.getByText(/Outdated caption preview/)).toBeVisible();
});

it("retains legacy appearance defaults and rejects invalid extended numeric styles", () => {
  expect(readStyle({ color: "yellow", size: "small", placement: "center" })).toEqual({ ...defaultStyle, color: "yellow", size: "small", placement: "center" });
  for (const style of [{ size_percent: NaN }, { horizontal: 1.01 }, { outline_percent: -1 }, { shadow_percent: Infinity }, { bold: "true" }]) expect(() => readStyle({ ...defaultStyle, ...style })).toThrow("Invalid caption style.");
});

it("mounts one automatic-caption panel alongside comparison and finishes reference loading across recipe initialization", async () => {
  let release!: (value: Response) => void;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.endsWith("/captions")) return ok({ status: "ready", track: { ...track, schema_version: 1, style: defaultStyle, provenance: "manual", timeline: { ...track.timeline, duration_seconds: 3 } }, whole_duration_seconds: 4, message: null });
    if (url.endsWith("/font-match")) return ok({revision: 0, status: "empty", selection: null});
    if (url.endsWith("/caption-font")) return ok({ revision: 0, available: true, font: null, message: null });
    if (url.endsWith("/transcription")) return ok({ status: "idle", operation_id: null, stale: false, proposal: null });
    return new Promise<Response>(resolve => { release = resolve; });
  }));
  const onState = vi.fn();
  const view = render(<CaptionEditor projectId="one" onState={onState} planState={plan} appearance={{ ...context, recipeReady: false, recipeRevision: null }} />);
  await screen.findByRole("region", { name: "Automatic captions" });
  view.rerender(<CaptionEditor projectId="one" onState={onState} planState={plan} appearance={context} />);
  await act(async () => release(ok(reference)));
  await screen.findByText("Retained reference: 4.000 seconds.");
  fireEvent.click(screen.getByLabelText("Bold"));
  expect(screen.getAllByRole("region", { name: "Automatic captions" })).toHaveLength(1);
  expect(onState).toHaveBeenLastCalledWith(expect.objectContaining({ dirty: true }));
});
