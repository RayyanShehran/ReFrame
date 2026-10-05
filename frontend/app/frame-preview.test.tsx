import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { FramePreview } from "./frame-preview";
import { GuidedWorkspace, WorkspaceSection } from "./guided-workspace";

const props = { projectId: "one", duration: 3, revision: 2, recipeReady: true, recipeDirty: false, recipeBusy: false, framing: { ready: true, dirty: false, busy: false, revision: 0 } };
const image = { width: 640, height: 360, png_base64: "iVBORw0KGgoAAA==" };
const result = (time = 1.5, recipe = 2, framing = 0) => ({ schema_version: 1, source: { project_id: "one", clip_id: `clip-${"a".repeat(32)}.mp4`, media_sha256: "b".repeat(64) }, requested_timestamp_seconds: time, timestamp_seconds: time, recipe_revision: recipe, framing_revision: framing, original: image, edited: image, warnings: [], ffmpeg_version: "9.0.2" });
const ok = (data: unknown) => ({ ok: true, json: async () => data } as Response);
afterEach(() => vi.unstubAllGlobals());

it("waits for an explicit action and picks the midpoint when footage becomes available", async () => {
  const fetcher = vi.fn<(url: string, options: RequestInit) => Promise<Response>>(async () => ok(result(2)));
  vi.stubGlobal("fetch", fetcher);
  const view = render(<FramePreview {...props} duration={null} />);
  expect(fetcher).not.toHaveBeenCalled();
  view.rerender(<FramePreview {...props} />);
  expect(screen.getByLabelText("Source time (seconds)")).toHaveValue(1.5);
  fireEvent.change(screen.getByLabelText("Source time (seconds)"), { target: { value: "2" } });
  expect(fetcher).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Update preview" }));
  await screen.findByRole("img", { name: /Original footage/ });
  expect(JSON.parse(fetcher.mock.calls[0][1].body as string)).toEqual({ expected_recipe_revision: 2, expected_framing_revision: 0, timestamp_seconds: 2 });
  expect(screen.getByRole("img", { name: /Edited footage/ })).toBeVisible();
});

it("retains previous images during replacement and failure, and marks saved revisions outdated", async () => {
  let resolve!: (response: Response) => void;
  const fetcher = vi.fn().mockResolvedValueOnce(ok(result())).mockImplementationOnce(() => new Promise<Response>(r => { resolve = r; })).mockResolvedValueOnce(ok(result(1.5, 3, 1)));
  vi.stubGlobal("fetch", fetcher);
  const view = render(<FramePreview {...props} />);
  fireEvent.click(screen.getByRole("button", { name: "Update preview" }));
  const original = await screen.findByRole("img", { name: /Original footage/ });
  view.rerender(<FramePreview {...props} revision={3} framing={{ ...props.framing, revision: 1 }} recipeDirty />);
  expect(screen.getByText(/Outdated preview/)).toBeVisible();
  expect(screen.getByRole("button", { name: "Review and save recipe" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Update preview" }));
  expect(original).toBeVisible();
  await act(async () => resolve({ ok: false, json: async () => ({ error: { message: "Media worker is busy." } }) } as Response));
  expect(screen.getByRole("alert")).toHaveTextContent("Media worker is busy.");
  expect(original).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Retry preview" }));
  await waitFor(() => expect(screen.getByText(/Frame preview · Source/)).toHaveTextContent("Recipe revision 3 · Framing revision 1"));
});

it("ignores a late older request when the selected time changes", async () => {
  let first!: (response: Response) => void;
  const fetcher = vi.fn().mockImplementationOnce(() => new Promise<Response>(r => { first = r; })).mockResolvedValueOnce(ok(result(2)));
  vi.stubGlobal("fetch", fetcher);
  render(<FramePreview {...props} />);
  fireEvent.click(screen.getByRole("button", { name: "Update preview" }));
  fireEvent.change(screen.getByLabelText("Source time (seconds)"), { target: { value: "2" } });
  fireEvent.click(screen.getByRole("button", { name: "Update preview" }));
  await screen.findByRole("img", { name: /Original footage.*source 2.000/ });
  await act(async () => first(ok(result())));
  expect(screen.getAllByRole("img").every(img => img.getAttribute("alt")?.includes("2.000"))).toBe(true);
  expect(fetcher.mock.calls[0][1].signal.aborted).toBe(true);
});

it("rejects a response for a different project and invalid source times", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => ok({ ...result(), source: { ...result().source, project_id: "other" } })));
  render(<FramePreview {...props} />);
  fireEvent.change(screen.getByLabelText("Source time (seconds)"), { target: { value: "4" } });
  expect(screen.getByRole("button", { name: "Update preview" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Source time (seconds)"), { target: { value: "1.5" } });
  fireEvent.click(screen.getByRole("button", { name: "Update preview" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Invalid preview response");
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
});

it("keeps its preview across workspace sections without decoding again", async () => {
  const fetcher = vi.fn(async () => ok(result())); vi.stubGlobal("fetch", fetcher);
  render(<GuidedWorkspace hasFootage><WorkspaceSection section="style"><FramePreview {...props} /></WorkspaceSection></GuidedWorkspace>);
  fireEvent.click(screen.getByRole("button", { name: "Style & cuts" }));
  fireEvent.click(screen.getByRole("button", { name: "Update preview" }));
  const original = await screen.findByRole("img", { name: /Original footage/ });
  fireEvent.click(screen.getByRole("button", { name: "Export" }));
  expect(original).not.toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Style & cuts" }));
  expect(original).toBeVisible(); expect(fetcher).toHaveBeenCalledTimes(1);
});
