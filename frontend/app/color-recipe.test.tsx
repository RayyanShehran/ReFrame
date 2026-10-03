import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ColorRecipe } from "./color-recipe";

vi.mock("./render-video", () => ({ RenderVideo: () => null }));

const values = { brightness: .1, contrast: 1.2, saturation: .8 };
const saved = { status: "ready", recipe: { schema_version: 1, suggestion_algorithm_version: "sampled-color-ratios-v1", suggested: values, selected: values, strength: .5, revision: 1, updated_at: "today", explanations: ["Insufficient footage contrast variation; multiplier is neutral."] }, effective: { brightness: .05, contrast: 1.1, saturation: .9 }, message: null };
const ok = (data: unknown) => ({ ok: true, json: async () => data } as Response);
afterEach(() => vi.unstubAllGlobals());

it("generates, adjusts, saves and restores without sending trusted source fields", async () => {
  let result: typeof saved | { status: string; recipe: null } = { status: "empty", recipe: null };
  const mock = vi.fn(async (url: string, options: RequestInit) => {
    if (options.method === "POST") {
      const body = JSON.parse(options.body as string);
      if (url.endsWith("/generate")) { expect(body).toEqual({ expected_revision: 0, replace: false }); result = saved; }
      else { expect(body).toEqual({ expected_revision: 1, selected: { ...values, brightness: -.15 }, strength: .8 }); result = { ...saved, recipe: { ...saved.recipe, selected: body.selected, strength: body.strength, revision: 2 } }; }
    }
    return ok(result);
  });
  vi.stubGlobal("fetch", mock);
  const view = render(<ColorRecipe projectId="first" analysesReady />);
  fireEvent.click(await screen.findByRole("button", { name: "Generate suggestion" }));
  await screen.findByText(/Recipe saved/);
  fireEvent.change(screen.getByRole("slider", { name: /Brightness offset/ }), { target: { value: "-.15" } });
  fireEvent.change(screen.getByRole("slider", { name: /Strength/ }), { target: { value: "80" } });
  expect(screen.getByText(/Unsaved changes/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Save recipe" }));
  await screen.findByText(/Recipe saved.*Revision 2/);
  view.unmount(); render(<ColorRecipe projectId="first" analysesReady />);
  expect(await screen.findByRole("slider", { name: /Brightness offset/ })).toHaveValue("-0.15");
  expect(screen.getByRole("slider", { name: /Strength/ })).toHaveValue("80");
  expect(screen.getByText(/does not produce a live video preview/)).toBeInTheDocument();
});

it("resets drafts locally and regenerates only after explicit replacement", async () => {
  const mock = vi.fn<(url: string, options: RequestInit) => Promise<Response>>(async () => ok(saved)); vi.stubGlobal("fetch", mock);
  render(<ColorRecipe projectId="first" analysesReady />);
  fireEvent.click(await screen.findByRole("button", { name: "Reset to neutral" }));
  expect(screen.getByRole("slider", { name: /Brightness offset/ })).toHaveValue("0");
  expect(screen.getByRole("slider", { name: /Contrast multiplier/ })).toHaveValue("1");
  expect(screen.getByRole("slider", { name: /Saturation multiplier/ })).toHaveValue("1");
  expect(screen.getByRole("slider", { name: /Strength/ })).toHaveValue("0");
  fireEvent.click(screen.getByRole("button", { name: "Reset to suggestion" }));
  expect(screen.getByRole("slider", { name: /Strength/ })).toHaveValue("50");
  expect(mock).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole("button", { name: "Regenerate suggestion" }));
  expect(mock).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole("button", { name: "Replace saved recipe" }));
  await waitFor(() => expect(mock).toHaveBeenCalledTimes(2));
  expect(JSON.parse(mock.mock.calls[1][1].body as string)).toEqual({ expected_revision: 1, replace: true });
});

it("shows stale settings for inspection and disables saving", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => ok({ ...saved, status: "stale", effective: null, message: "Recipe is stale. Source changed." })));
  render(<ColorRecipe projectId="first" analysesReady={false} />);
  expect(await screen.findByRole("slider", { name: /Brightness offset/ })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Save recipe" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Regenerate suggestion" })).toBeDisabled();
  expect(screen.getByRole("alert")).toHaveTextContent("Source changed");
});

it("preserves unsaved settings on revision conflicts and allows explicit reload", async () => {
  const mock = vi.fn(async (_url: string, options: RequestInit) => options.method === "POST" ? { ok: false, json: async () => ({ error: { code: "revision_conflict", message: "Recipe changed elsewhere. Reload it." } }) } as Response : ok(saved));
  vi.stubGlobal("fetch", mock);
  render(<ColorRecipe projectId="first" analysesReady />);
  fireEvent.change(await screen.findByRole("slider", { name: /Brightness offset/ }), { target: { value: "-.2" } });
  fireEvent.click(screen.getByRole("button", { name: "Save recipe" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("changed elsewhere");
  expect(screen.getByRole("slider", { name: /Brightness offset/ })).toHaveValue("-0.2");
  fireEvent.click(screen.getByRole("button", { name: "Discard changes and reload" }));
  await waitFor(() => expect(screen.getByRole("slider", { name: /Brightness offset/ })).toHaveValue("0.1"));
});

it("ignores late responses after project navigation", async () => {
  let late!: (r: Response) => void; let oldSignal!: AbortSignal;
  vi.stubGlobal("fetch", vi.fn((url: string, options: RequestInit) => {
    if (url.includes("/first/")) { oldSignal = options.signal as AbortSignal; return new Promise<Response>(resolve => { late = resolve; }); }
    return Promise.resolve(ok({ status: "empty", recipe: null }));
  }));
  const view = render(<ColorRecipe key="first" projectId="first" analysesReady />);
  await waitFor(() => expect(late).toBeDefined());
  view.rerender(<ColorRecipe key="second" projectId="second" analysesReady />);
  await screen.findByRole("button", { name: "Generate suggestion" });
  expect(oldSignal.aborted).toBe(true);
  await act(async () => late(ok(saved)));
  expect(screen.queryByRole("slider")).not.toBeInTheDocument();
});

it("rejects invalid saved values and blocks generation without valid analyses", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => ok({ ...saved, recipe: { ...saved.recipe, strength: 2 } })));
  const view = render(<ColorRecipe projectId="first" analysesReady />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Invalid recipe response");
  expect(screen.queryByRole("slider")).not.toBeInTheDocument();
  view.unmount(); vi.stubGlobal("fetch", vi.fn(async () => ok({ status: "empty", recipe: null })));
  render(<ColorRecipe projectId="second" analysesReady={false} />);
  expect(await screen.findByRole("button", { name: "Generate suggestion" })).toBeDisabled();
});
