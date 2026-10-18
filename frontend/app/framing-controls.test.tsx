import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { FramingControls } from "./framing-controls";

const initial = { status: "default", settings: { schema_version: 1, revision: 0, format: "original", fit: "fit", horizontal: .5, vertical: .5 } };
const geometry = { display_width: 640, display_height: 360, original_width: 640, original_height: 360 };
const ok = (data: unknown) => ({ ok: true, json: async () => data } as Response);
afterEach(() => vi.unstubAllGlobals());

it("disables both crop axes for an exact square despite floating point scaling", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => ok(url.endsWith("/source") ? { display_width: 169, display_height: 169, original_width: 168, original_height: 168 } : initial)));
  render(<FramingControls projectId="one" onState={vi.fn()} />);
  fireEvent.change(await screen.findByLabelText("Output format"), { target: { value: "square" } });
  fireEvent.change(screen.getByLabelText("How footage fits"), { target: { value: "fill" } });
  await screen.findByText(/Draft canvas: 720 × 720/);
  expect(screen.getByLabelText("Horizontal crop position")).toBeDisabled();
  expect(screen.getByLabelText("Vertical crop position")).toBeDisabled();
});

it("saves framing, restores it and keeps Reset as a draft change", async () => {
  let saved = initial;
  const fetcher = vi.fn(async (url: string, options: RequestInit) => {
    if (url.endsWith("/source")) return ok(geometry);
    if (options.method === "POST") {
      const body = JSON.parse(options.body as string);
      expect(body).toMatchObject({ expected_revision: 0, format: "square", fit: "fill", horizontal: .8, vertical: .5 });
      saved = { status: "ready", settings: { ...initial.settings, ...body, revision: 1 } };
    }
    return ok(saved);
  });
  vi.stubGlobal("fetch", fetcher);
  const state = vi.fn();
  const view = render(<FramingControls projectId="one" onState={state} />);
  fireEvent.change(await screen.findByLabelText("Output format"), { target: { value: "square" } });
  fireEvent.change(screen.getByLabelText("How footage fits"), { target: { value: "fill" } });
  await waitFor(() => expect(screen.getByLabelText("Horizontal crop position")).toBeEnabled());
  expect(screen.getByLabelText("Vertical crop position")).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Horizontal crop position"), { target: { value: "80" } });
  fireEvent.click(screen.getByRole("button", { name: "Save framing" }));
  expect(await screen.findByText(/Framing saved.*Revision 1.*Square.*720 × 720/)).toBeInTheDocument();
  view.unmount();
  render(<FramingControls projectId="one" onState={state} />);
  expect(await screen.findByLabelText("Output format")).toHaveValue("square");
  expect(screen.getByLabelText("Horizontal crop position")).toHaveValue("80");
  fireEvent.click(screen.getByRole("button", { name: "Reset framing" }));
  expect(screen.getByLabelText("Output format")).toHaveValue("original");
  expect(screen.getByText(/Unsaved framing changes/)).toBeInTheDocument();
  expect(fetcher.mock.calls.filter(([, options]) => options.method === "POST")).toHaveLength(1);
});

it("uses normalized display geometry for crop axes and retains draft after conflict", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string, options: RequestInit) => {
    if (url.endsWith("/source")) return ok({ ...geometry, display_width: 360, display_height: 1280, original_width: 360, original_height: 1280 });
    if (options.method === "POST") return { ok: false, json: async () => ({ error: { code: "revision_conflict", message: "Framing changed elsewhere." } }) } as Response;
    return ok(initial);
  }));
  render(<FramingControls projectId="one" onState={vi.fn()} />);
  fireEvent.change(await screen.findByLabelText("Output format"), { target: { value: "landscape" } });
  fireEvent.change(screen.getByLabelText("How footage fits"), { target: { value: "fill" } });
  await waitFor(() => expect(screen.getByLabelText("Vertical crop position")).toBeEnabled());
  expect(screen.getByLabelText("Horizontal crop position")).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Vertical crop position"), { target: { value: "20" } });
  fireEvent.click(screen.getByRole("button", { name: "Save framing" }));
  expect(await screen.findByText("Framing changed elsewhere.")).toBeInTheDocument();
  expect(screen.getByLabelText("Vertical crop position")).toHaveValue("20");
  expect(screen.getByText(/Unsaved framing changes/)).toBeInTheDocument();
});

it("keeps selected-slot drafts, hides saved previews after edits, and restores overrides", async () => {
  const a = "11111111-1111-4111-8111-111111111111", b = "22222222-2222-4222-8222-222222222222";
  const picture = { width: 2, height: 2, png_base64: "iVBORw0KGgoAAA==" };
  let saved = { status: "ready", settings: { ...initial.settings, format: "square", fit: "automatic", bars: "auto", slots: {}, canvas: null, decisions: {}, revision: 1 } };
  const grading = { revision: 1, mode: "original" as const, ready: true, dirty: false, busy: false, sequenceReady: true, sequenceRevision: 2, choices: [a,b].map((id,i) => ({ key: `slot:${id}`, name: `Shot ${i+1}`, clipId: id, slotId: id, start: 0, end: 1, version: "v1" })) };
  vi.stubGlobal("fetch", vi.fn(async (url: string, options: RequestInit) => {
    if (url.includes("/source")) return ok(geometry);
    if (url.endsWith("/frame-preview")) return ok({ edited: picture, framing_revision: saved.settings.revision, grading_revision: 1, slot_id: a, framing_note: "Center Fill" });
    if (options.method === "POST") {
      const body = JSON.parse(options.body as string);
      saved = { ...saved, settings: { ...saved.settings, ...body, revision: saved.settings.revision+1 } };
    }
    return ok(saved);
  }));
  const view = render(<FramingControls projectId="one" onState={vi.fn()} grading={grading} />);
  await screen.findByLabelText("Slot to frame");
  fireEvent.click(screen.getByRole("button", { name: "Update saved framing preview" }));
  expect(await screen.findByAltText(/Actual selected footage/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Slot framing"), { target: { value: "fill" } });
  fireEvent.change(screen.getByLabelText("Slot zoom"), { target: { value: "1.25" } });
  expect(screen.queryByAltText(/Actual selected footage/)).not.toBeInTheDocument();
  expect(screen.getByText(/Preview pending/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Slot to frame"), { target: { value: `slot:${b}` } });
  fireEvent.change(screen.getByLabelText("Slot to frame"), { target: { value: `slot:${a}` } });
  expect(screen.getByLabelText("Slot zoom")).toHaveValue("1.25");
  fireEvent.click(screen.getByRole("button", { name: "Save framing" }));
  await screen.findByText(/Framing saved.*Revision 2/);
  view.unmount();
  render(<FramingControls projectId="one" onState={vi.fn()} grading={grading} />);
  expect(await screen.findByLabelText("Slot zoom")).toHaveValue("1.25");
  fireEvent.click(screen.getByRole("button", { name: "Reset selected slot to automatic" }));
  expect(screen.getByLabelText("Slot framing")).toHaveValue("automatic");
  expect(screen.getByText(/Unsaved framing changes/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Embedded-bar removal"), { target: { value: "off" } });
  expect(screen.getByLabelText("Top border trim")).toBeDisabled();
});

