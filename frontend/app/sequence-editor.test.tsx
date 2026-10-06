import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { SequenceEditor } from "./sequence-editor";
import { RenderVideo } from "./render-video";

const a = "11111111-1111-4111-8111-111111111111", b = "22222222-2222-4222-8222-222222222222";
const clip = (id: string, name: string) => ({ id, name, primary: id === a, available: true, sha256: "a".repeat(64), metadata: { filename: `${name}.mp4`, size_bytes: 1000, duration_seconds: 5, width: 320, height: 180, video_codec: "h264", has_audio: id === a, audio_codec: id === a ? "aac" : null, frame_rate: 30, validation_status: "accepted", storage_status: "not_retained" } });
const library = { clips: [clip(a, "Camera A"), clip(b, "Camera B")], total_bytes: 2000, max_clips: 10, max_bytes: 500 * 1024 * 1024 };
const slots = [a,b,a].map((id,i) => ({ id: `00000000-0000-4000-8000-00000000000${i}`, clip_id: id, duration_frames: 30, source_start_frame: 0, source_end_frame: 30, output_start_frame: i*30, output_end_frame: (i+1)*30, source_hash: "a".repeat(64) }));
const saved = { status: "ready", message: null, sequence: { schema_version: 1, revision: 1, slots, output_frames: 90 } };
const ok = (data: unknown) => ({ ok: true, json: async () => data } as Response);
vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
afterEach(() => vi.unstubAllGlobals());

it("keeps trimmed drafts across slots, rejects short ranges, saves frames and restores", async () => {
  let result = saved;
  const onState = vi.fn();
  vi.stubGlobal("fetch", vi.fn(async (url: string, options: RequestInit) => {
    if (url.endsWith("/clips")) return ok(library);
    if (options.method === "POST") {
      const body = JSON.parse(options.body as string);
      expect(body.expected_revision).toBe(1);
      expect(body.slots[0]).toEqual({ id: slots[0].id, clip_id: a, duration_frames: 30, source_start_frame: 60 });
      expect(body.slots[2].clip_id).toBe(a);
      result = { ...saved, sequence: { ...saved.sequence, revision: 2, slots: slots.map((s,i) => i === 0 ? { ...s, source_start_frame: 60, source_end_frame: 90 } : s) } };
    }
    return ok(result);
  }));
  const view = render(<SequenceEditor projectId="project" onState={onState} />);
  const start = await screen.findByRole("spinbutton", { name: "Start (seconds)" });
  fireEvent.change(start, { target: { value: "4.5" } });
  expect(screen.getByRole("button", { name: "Save sequence" })).toBeDisabled();
  fireEvent.change(start, { target: { value: "2" } });
  expect(screen.getByRole("spinbutton", { name: "End (seconds)" })).toHaveValue(3);
  fireEvent.click(screen.getByRole("button", { name: /Slot 2 ·/ }));
  fireEvent.click(screen.getByRole("button", { name: /Slot 1 ·/ }));
  expect(screen.getByRole("spinbutton", { name: "Start (seconds)" })).toHaveValue(2);
  fireEvent.click(screen.getByRole("button", { name: "Save sequence" }));
  await waitFor(() => expect(onState).toHaveBeenLastCalledWith({ revision: 2, ready: true, dirty: false, busy: false, duration: 3 }));
  view.unmount();
  render(<SequenceEditor projectId="project" onState={onState} />);
  expect(await screen.findByRole("spinbutton", { name: "Start (seconds)" })).toHaveValue(2);
});

it("retains edits on conflict and confirms regeneration", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string, options: RequestInit) => url.endsWith("/clips") ? ok(library) : options.method === "POST" ? { ok: false, json: async () => ({ error: { message: "Sequence changed elsewhere." } }) } as Response : ok(saved)));
  render(<SequenceEditor projectId="project" onState={vi.fn()} />);
  fireEvent.change(await screen.findByRole("spinbutton", { name: "Start (seconds)" }), { target: { value: "2" } });
  fireEvent.click(screen.getByRole("button", { name: "Save sequence" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Sequence changed elsewhere.");
  expect(screen.getByRole("spinbutton", { name: "Start (seconds)" })).toHaveValue(2);
  fireEvent.click(screen.getByRole("button", { name: "Regenerate slots from reference pacing" }));
  expect(screen.getByRole("group", { name: "Confirm slot replacement" })).toBeInTheDocument();
});

it("sends the saved sequence revision and blocks unsaved edits", async () => {
  const fetch = vi.fn(async () => ok({ status: "idle", output: null, spec: null, outdated: false }));
  vi.stubGlobal("fetch", fetch);
  const state = { revision: 3, ready: true, dirty: false, busy: false, duration: 3 };
  const view = render(<RenderVideo projectId="project" revision={1} recipeReady dirty={false} busy={false} sequenceState={state} />);
  await screen.findByRole("combobox", { name: "Render mode" });
  fireEvent.change(screen.getByRole("combobox", { name: "Render mode" }), { target: { value: "sequence" } });
  fireEvent.click(screen.getByRole("button", { name: "Render video" }));
  await waitFor(() => expect(fetch.mock.calls.some(call => JSON.stringify(call).includes("expected_sequence_revision"))).toBe(true));
  view.rerender(<RenderVideo projectId="project" revision={1} recipeReady dirty={false} busy={false} sequenceState={{ ...state, dirty: true }} />);
  expect(screen.getByRole("button", { name: "Render video" })).toBeDisabled();
});
