import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { RenderVideo } from "./render-video";

const id = "11111111-1111-4111-8111-111111111111";
const idle = { status: "idle", output: null, spec: null, outdated: false, message: null, failure_code: null };
const ready = { ...idle, status: "ready", spec: { recipe_revision: 2 }, output: { output_id: id, spec: { recipe_revision: 2 }, width: 320, height: 240, duration_seconds: 2, size_bytes: 30000 } };
const ok = (value: unknown) => ({ ok: true, json: async () => value } as Response);
afterEach(() => vi.unstubAllGlobals());

it("requires a saved valid recipe and sends only its revision, then restores native playback", async () => {
  let result = idle as unknown;
  const mock = vi.fn(async (_url: string, options: RequestInit) => {
    if (options.method === "POST") { expect(JSON.parse(options.body as string)).toEqual({ expected_revision: 2 }); result = ready; }
    return ok(result);
  });
  vi.stubGlobal("fetch", mock);
  const view = render(<RenderVideo projectId="project" revision={2} recipeReady dirty busy={false} />);
  expect(screen.getByRole("button", { name: "Render video" })).toBeDisabled();
  expect(screen.getByText(/Save your unsaved/)).toBeInTheDocument();
  view.rerender(<RenderVideo projectId="project" revision={2} recipeReady dirty={false} busy={false} />);
  fireEvent.click(screen.getByRole("button", { name: "Render video" }));
  expect(await screen.findByLabelText("Rendered video, recipe revision 2")).toHaveAttribute("controls");
  expect(screen.getByRole("link", { name: "Download MP4" })).toHaveAttribute("href", expect.stringContaining(`/outputs/${id}/download`));
  view.unmount();
  render(<RenderVideo projectId="project" revision={3} recipeReady dirty={false} busy={false} />);
  expect(await screen.findByText(/Outdated output/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Render video" })).toBeEnabled();
});

it("shows running and failed replacement while retaining the completed video", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => ok({ ...ready, status: "running", spec: { recipe_revision: 3 }, outdated: true })));
  const view = render(<RenderVideo projectId="project" revision={3} recipeReady dirty={false} busy={false} />);
  expect(await screen.findByText(/Rendering saved recipe revision 3/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Render video" })).toBeDisabled();
  view.unmount();
  vi.stubGlobal("fetch", vi.fn(async () => ok({ ...ready, status: "failed", outdated: true, message: "Encoder failed safely." })));
  render(<RenderVideo projectId="project" revision={3} recipeReady dirty={false} busy={false} />);
  expect(await screen.findByText("Encoder failed safely.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Retry render" })).toBeEnabled();
  expect(screen.getByLabelText("Rendered video, recipe revision 2")).toBeInTheDocument();
});

it("blocks stale recipes and rejects malformed output instead of creating file links", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => ok({ ...ready, output: { ...ready.output, output_id: "../other" } })));
  render(<RenderVideo projectId="project" revision={2} recipeReady={false} dirty={false} busy={false} />);
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Invalid rendered video response."));
  expect(screen.getByRole("button", { name: "Render video" })).toBeDisabled();
  expect(screen.queryByRole("link", { name: "Download MP4" })).not.toBeInTheDocument();
});
