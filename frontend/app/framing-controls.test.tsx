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
      expect(body).toEqual({ expected_revision: 0, format: "square", fit: "fill", horizontal: .8, vertical: .5 });
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
