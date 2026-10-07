import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { RenderVideo } from "./render-video";

const id = "11111111-1111-4111-8111-111111111111";
const idle = { status: "idle", output: null, spec: null, outdated: false, message: null, failure_code: null };
const ready = { ...idle, status: "ready", spec: { recipe_revision: 2 }, output: { output_id: id, spec: { recipe_revision: 2 }, width: 320, height: 240, duration_seconds: 2, size_bytes: 30000 } };
const ok = (value: unknown) => ({ ok: true, json: async () => value } as Response);
afterEach(() => vi.unstubAllGlobals());

it("requires saved framing, binds its revision and retains older playable output", async () => {
  const fetcher = vi.fn(async (_url: string, options: RequestInit) => {
    if (options.method === "POST") expect(JSON.parse(options.body as string)).toEqual({ expected_revision: 2, expected_audio_revision: 0, expected_caption_revision: 0, expected_framing_revision: 3 });
    return ok(ready);
  });
  vi.stubGlobal("fetch", fetcher);
  const framing = { revision: 3, ready: true, busy: false, dirty: true, format: "square" as const, dimensions: "720 × 720" };
  const view = render(<RenderVideo projectId="one" revision={2} recipeReady dirty={false} busy={false} framingState={framing} />);
  expect(await screen.findByText(/Outdated output/)).toBeInTheDocument();
  expect(screen.getByText("Saved format: Square · 720 × 720")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Render video" })).toBeDisabled();
  expect(screen.getByLabelText("Rendered video, recipe revision 2")).toBeInTheDocument();
  view.rerender(<RenderVideo projectId="one" revision={2} recipeReady dirty={false} busy={false} framingState={{ ...framing, dirty: false }} />);
  fireEvent.click(screen.getByRole("button", { name: "Render video" }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ method: "POST" })));
});

it("requires saved captions for the selected timeline and binds their revision", async () => {
  const fetcher = vi.fn(async (_url: string, options: RequestInit) => {
    if (options.method === "POST") expect(JSON.parse(options.body as string)).toEqual({ expected_revision: 2, expected_audio_revision: 0, expected_caption_revision: 4, expected_framing_revision: 0, expected_plan_revision: 3 });
    return ok(idle);
  });
  vi.stubGlobal("fetch", fetcher);
  const plan = { revision: 3, ready: true, dirty: false, busy: false };
  const captions = { revision: 4, ready: true, dirty: true, busy: false, enabled: true, mode: "cuts" as const, planRevision: 3 };
  const view = render(<RenderVideo projectId="one" revision={2} recipeReady dirty={false} busy={false} planState={plan} captionState={captions} />);
  await screen.findByText(/Save your unsaved caption changes/);
  expect(screen.getByRole("button", { name: "Render video" })).toBeDisabled();
  view.rerender(<RenderVideo projectId="one" revision={2} recipeReady dirty={false} busy={false} planState={plan} captionState={{ ...captions, dirty: false }} />);
  expect(screen.getByRole("button", { name: "Render video" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Render mode"), { target: { value: "cuts" } });
  fireEvent.click(screen.getByRole("button", { name: "Render video" }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ method: "POST" })));
});

it("binds saved plan and recipe revisions and blocks unsaved cuts only in cut mode", async () => {
  const mock = vi.fn(async (_url: string, options: RequestInit) => {
    if (options.method === "POST") expect(JSON.parse(options.body as string)).toEqual({ expected_revision: 2, expected_audio_revision: 0, expected_caption_revision: 0, expected_plan_revision: 3 });
    return ok(idle);
  });
  vi.stubGlobal("fetch", mock);
  const plan = { revision: 3, ready: true, dirty: true, busy: false };
  const view = render(<RenderVideo projectId="project" revision={2} recipeReady dirty={false} busy={false} planState={plan} />);
  await screen.findByRole("combobox", { name: "Render mode" });
  expect(screen.getByRole("button", { name: "Render video" })).toBeEnabled();
  fireEvent.change(screen.getByRole("combobox"), { target: { value: "cuts" } });
  expect(screen.getByRole("button", { name: "Render video" })).toBeDisabled();
  view.rerender(<RenderVideo projectId="project" revision={2} recipeReady dirty={false} busy={false} planState={{ ...plan, dirty: false }} />);
  fireEvent.click(screen.getByRole("button", { name: "Render video" }));
  await waitFor(() => expect(mock).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ method: "POST" })));
});

it("requires a saved valid recipe and sends only its revision, then restores native playback", async () => {
  let result = idle as unknown;
  const mock = vi.fn(async (_url: string, options: RequestInit) => {
    if (options.method === "POST") { expect(JSON.parse(options.body as string)).toEqual({ expected_revision: 2, expected_audio_revision: 0, expected_caption_revision: 0, expected_framing_revision: 0 }); result = ready; }
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
  expect(await screen.findByText(/Rendering saved color settings/)).toBeInTheDocument();
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

it("blocks unsaved audio and binds its saved revision while preserving outdated playback", async () => {
  vi.stubGlobal("fetch", vi.fn(async (_url: string, options: RequestInit) => {
    if (options.method === "POST") expect(JSON.parse(options.body as string)).toEqual({ expected_revision: 2, expected_audio_revision: 3, expected_caption_revision: 0, expected_framing_revision: 0 });
    return ok({ ...ready, output: { ...ready.output, spec: { recipe_revision: 2, audio: { revision: 2, mode: "reference" } } } });
  }));
  const state = { revision: 3, ready: true, dirty: true, busy: false };
  const view = render(<RenderVideo projectId="project" revision={2} recipeReady dirty={false} busy={false} audioState={state} />);
  expect(await screen.findByText(/Reference audio.*Audio revision 2/)).toBeInTheDocument();
  expect(screen.getByText(/Outdated output/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Render video" })).toBeDisabled();
  view.rerender(<RenderVideo projectId="project" revision={2} recipeReady dirty={false} busy={false} audioState={{ ...state, dirty: false }} />);
  fireEvent.click(screen.getByRole("button", { name: "Render video" }));
  await waitFor(() => expect(fetch).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ method: "POST" })));
});
