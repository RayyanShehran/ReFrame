import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { GuidedWorkspace, WorkspaceSection, useWorkspaceNavigation, useWorkspaceReport } from "./guided-workspace";
import { ColorRecipe } from "./color-recipe";
import { RenderVideo } from "./render-video";

vi.mock("./edit-plan", () => ({ EditPlan: () => null }));
vi.mock("./audio-choices", async (original) => ({ ...await original<typeof import("./audio-choices")>(), AudioChoices: () => null }));
vi.mock("./caption-editor", () => ({ CaptionEditor: () => null }));
vi.mock("./framing-controls", async (original) => ({ ...await original<typeof import("./framing-controls")>(), FramingControls: () => null }));
const values = { brightness: .1, contrast: 1, saturation: 1 };
const saved = { status: "ready", recipe: { schema_version: 1, suggestion_algorithm_version: "sampled-color-ratios-v1", suggested: values, selected: values, strength: .5, revision: 1, updated_at: "today", explanations: [] }, effective: { ...values, brightness: .05 }, message: null };
const idle = { status: "idle", output: null, spec: null, outdated: false, message: null, failure_code: null };
afterEach(() => vi.unstubAllGlobals());

function SourceStatus({ working = false }: { working?: boolean }) {
  useWorkspaceReport("reference-media", "reference", "Ready");
  useWorkspaceReport("style-blueprint", "style", "Ready");
  useWorkspaceReport("footage-color", "style", working ? "Working" : "Ready", "Analyzing footage colors…");
  useWorkspaceReport("audio-settings", "audio", "Ready");
  useWorkspaceReport("caption-settings", "audio", "Ready");
  useWorkspaceReport("transcription", "audio", "Needs input");
  return null;
}

it("keeps a real unsaved recipe across sections without fetching or saving it again", async () => {
  const fetcher = vi.fn(async (url: string) => ({ ok: true, json: async () => url.endsWith("/color-recipe") ? saved : idle } as Response));
  vi.stubGlobal("fetch", fetcher);
  render(<GuidedWorkspace hasFootage><ColorRecipe projectId="one" analysesReady /></GuidedWorkspace>);
  fireEvent.click(screen.getByRole("button", { name: "Color" }));
  const slider = await screen.findByRole("slider", { name: /Brightness offset/ });
  fireEvent.change(slider, { target: { value: "-.15" } });
  const count = fetcher.mock.calls.length;
  fireEvent.click(screen.getByRole("button", { name: "Audio" }));
  expect(slider).not.toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Color" }));
  expect(slider).toBeVisible();
  expect(slider).toHaveValue("-0.15");
  expect(screen.getByRole("button", { name: "Save recipe" })).toBeEnabled();
  expect(fetcher).toHaveBeenCalledTimes(count);
});

it("does not require optional cuts or captions to render a whole clip", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => idle } as Response)));
  render(<GuidedWorkspace hasFootage><SourceStatus /><WorkspaceSection section="export"><RenderVideo projectId="one" revision={1} recipeReady dirty={false} busy={false} planState={{ revision: null, ready: false, dirty: true, busy: false }} /></WorkspaceSection></GuidedWorkspace>);
  fireEvent.click(screen.getByRole("button", { name: "Export" }));
  await screen.findByText(/Whole clip · Cuts optional/);
  expect(screen.getByRole("button", { name: "Render video" })).toBeEnabled();
  expect(screen.getByText(/Saved captions: disabled/)).toBeVisible();
  expect(screen.getByRole("button", { name: "Audio" })).toHaveAccessibleDescription("Ready");
  fireEvent.change(screen.getByLabelText("Render mode"), { target: { value: "cuts" } });
  expect(screen.getByRole("button", { name: "Render video" })).toBeDisabled();
});

it("links an actual render blocker to its section without starting work", async () => {
  const fetcher = vi.fn<(url: string, options: RequestInit) => Promise<Response>>(async () => ({ ok: true, json: async () => idle } as Response));
  vi.stubGlobal("fetch", fetcher);
  render(<GuidedWorkspace hasFootage><SourceStatus /><WorkspaceSection section="export"><RenderVideo projectId="one" revision={1} recipeReady dirty={false} busy={false} audioState={{ revision: 2, ready: true, dirty: true, busy: false }} /></WorkspaceSection><WorkspaceSection section="audio"><p>Audio draft editor</p></WorkspaceSection></GuidedWorkspace>);
  fireEvent.click(screen.getByRole("button", { name: "Export" }));
  expect(await screen.findByRole("button", { name: "Render video" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Open Audio" }));
  expect(screen.getByText("Audio draft editor")).toBeVisible();
  await waitFor(() => expect(screen.getByRole("heading", { name: "Audio" })).toHaveFocus());
  expect(fetcher.mock.calls.every(call => (call[1] as RequestInit | undefined)?.method !== "POST")).toBe(true);
});

it("resets section, draft and running status when switching project identity", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => ({ ok: true, json: async () => url.endsWith("/color-recipe") ? saved : idle } as Response)));
  const view = render(<GuidedWorkspace key="one" hasFootage><SourceStatus working /><ColorRecipe projectId="one" analysesReady /></GuidedWorkspace>);
  fireEvent.click(screen.getByRole("button", { name: "Color" }));
  fireEvent.change(await screen.findByRole("slider", { name: /Brightness offset/ }), { target: { value: "-.2" } });
  expect(screen.getByText("Analyzing footage colors…")).toBeVisible();
  view.rerender(<GuidedWorkspace key="two" hasFootage><SourceStatus /><ColorRecipe projectId="two" analysesReady /></GuidedWorkspace>);
  expect(screen.getByRole("button", { name: "Cuts" })).toHaveAttribute("aria-current", "page");
  expect(screen.queryByText("Analyzing footage colors…")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Color" }));
  expect(await screen.findByRole("slider", { name: /Brightness offset/ })).toHaveValue("0.1");
  expect(screen.getByRole("button", { name: "Save recipe" })).toBeDisabled();
});

it("uses the compact section picker without remounting drafts and focuses the selected heading", async () => {
 vi.stubGlobal("fetch", vi.fn(async (url: string) => ({ok: true, json: async () => url.endsWith("/color-recipe") ? saved : idle} as Response)));
 render(<GuidedWorkspace hasFootage><SourceStatus /><ColorRecipe projectId="one" analysesReady /></GuidedWorkspace>);
 const picker = screen.getByLabelText("Editing section");
 fireEvent.change(picker, {target: {value: "style"}});
 const slider = await screen.findByRole("slider", {name: /Brightness offset/});
 fireEvent.change(slider, {target: {value: "-.15"}});
 fireEvent.change(picker, {target: {value: "audio"}});
 await waitFor(() => expect(screen.getByRole("heading", {name: "Audio"})).toHaveFocus());
 fireEvent.change(picker, {target: {value: "style"}});
 expect(slider).toHaveValue("-0.15");
 expect(screen.getByRole("button", {name: "Save recipe"})).toBeEnabled();
 expect(picker).toHaveValue("style");
 expect(screen.getByRole("option", {name: "Audio · Ready"})).toBeInTheDocument();
});


function SelectionProbe() {
  const navigation = useWorkspaceNavigation();
  return <><button onClick={() => navigation?.select({kind: "slot", id: "slot-one", label: "Sequence slot 1", time: 2})}>Select test slot</button><button onClick={() => navigation?.select({kind: "cue", id: "0", label: "Caption cue 1", time: 3})}>Select test cue</button><output aria-label="Selection">{navigation?.selection?.id}</output></>;
}
it("retains independent slot and cue selection while categories change", () => {
 render(<GuidedWorkspace hasFootage><SelectionProbe /></GuidedWorkspace>);
 fireEvent.click(screen.getByRole("button", {name: "Select test slot"}));
 fireEvent.click(screen.getByRole("button", {name: "Color"}));
 expect(screen.getByLabelText("Selection")).toHaveTextContent("slot-one");
 fireEvent.click(screen.getByRole("button", {name: "Select test cue"}));
 expect(screen.getByLabelText("Selection")).toHaveTextContent("0");
 fireEvent.click(screen.getByRole("button", {name: "Color"}));
 expect(screen.getByLabelText("Selection")).toHaveTextContent("slot-one");
 fireEvent.click(screen.getByRole("button", {name: "Captions"}));
 expect(screen.getByLabelText("Selection")).toHaveTextContent("0");
});


function PartialCaptionStatus() {
 useWorkspaceReport("caption-settings", "captions", "Ready");
 useWorkspaceReport("caption-proposal", "captions", "Needs attention");
 return null;
}
it("flags incomplete recognition even when the separate saved caption track is valid", () => {
 render(<GuidedWorkspace hasFootage><PartialCaptionStatus /></GuidedWorkspace>);
 expect(screen.getByRole("button", {name:"Captions"})).toHaveAccessibleDescription("Needs attention");
});
