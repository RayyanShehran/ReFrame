import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { GradingControls, defaultControls } from "./grading-controls";
const id = "11111111-1111-4111-8111-111111111111", hash = "a".repeat(64), key = `clip:${id}`;
const metadata = { filename: "texture.mp4", size_bytes: 1000, duration_seconds: 3, width: 320, height: 180, video_codec: "h264", has_audio: true, audio_codec: "aac", frame_rate: 30, validation_status: "accepted", storage_status: "not_retained" };
const library = { clips: [{ id, name: "Texture", primary: true, available: true, sha256: hash, metadata }], total_bytes: 1000, max_clips: 10, max_bytes: 500 * 1024 * 1024 };
const empty = { status: "idle", settings: { schema_version: 1, revision: 0, mode: "basic", controls: {}, shot_slots: [], updated_at: null }, entries: [], completed: 0, total: 0, message: null, failure_code: null };
const entry = { valid: true, message: null, match: { key, clip_id: id, slot_id: null, sequence_revision: null, source_hash: hash, reference: { source: { canonical_url: "https://www.tiktok.com/@fixture/video/1", media_sha256: hash } }, model: { algorithm: "regularized-encoded-tone-chroma-v1", warnings: ["Different content can bias matching."] }, prepared_at: "2026-10-07" } };
const sequence = { revision: null, ready: false, dirty: false, busy: false };
const ok = (data: unknown) => ({ ok: true, json: async () => data } as Response);
afterEach(() => vi.unstubAllGlobals());
it("prepares independently, explicitly saves transfer, preserves refinements and restores after remount", async () => {
  let saved: Omit<typeof empty, "entries"> & { entries: typeof entry[] } = empty; const posted: Record<string, unknown>[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, options: RequestInit) => {
    if (url.endsWith("/clips")) return ok(library);
    if (url.endsWith("/sequence")) return ok({ status: "empty", sequence: null });
    if (url.endsWith("/prepare")) { saved = { ...saved, status: "ready", entries: [entry] }; return ok(saved); }
    if (options.method === "POST") {
      const body = JSON.parse(String(options.body)); posted.push(body);
      saved = { ...saved, settings: { ...saved.settings, mode: body.mode, controls: body.controls, shot_slots: body.shot_slots, revision: saved.settings.revision + 1 } };
    }
    return ok(saved);
  }));
  const state = vi.fn(); const props = { projectId: id, sourceKey: "one", analysesReady: true, sequence, onState: state };
  const view = render(<GradingControls {...props} />);
  await waitFor(() => expect(screen.getByRole("button", { name: "Match reference colors" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Match reference colors" }));
  await screen.findByText("Prepared match");
  expect(screen.getByText(/Unsaved color changes/)).toBeInTheDocument();
  expect(posted).toHaveLength(0);
  await waitFor(() => expect(screen.getByLabelText(/Match strength/)).toBeEnabled());
  fireEvent.change(screen.getByLabelText(/Match strength/), { target: { value: "70" } });
  fireEvent.click(screen.getByRole("button", { name: "Save color mode and matches" }));
  await waitFor(() => expect(posted).toHaveLength(1));
  expect(posted[0]).toMatchObject({ expected_revision: 0, mode: "transfer", controls: { [key]: { ...defaultControls, strength: .7 } } });
  await waitFor(() => expect(screen.getByRole("button", { name: "Save color mode and matches" })).toBeDisabled());
  view.unmount(); render(<GradingControls {...props} />);
  await waitFor(() => expect(screen.getByLabelText(/Match strength/)).toHaveValue("70"));
  fireEvent.click(screen.getByRole("button", { name: "Match reference colors" }));
  expect(screen.getByRole("group", { name: "Confirm color rematching" })).toBeInTheDocument();
});
it("saves Original without reference analysis and keeps an unsaved draft after a conflict", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string, options: RequestInit) => {
    if (url.endsWith("/clips")) return ok(library);
    if (url.endsWith("/sequence")) return ok({ status: "empty", sequence: null });
    if (options.method === "POST") return { ok: false, json: async () => ({ error: { message: "Saved color mode changed. Reload; your draft is retained." } }) } as Response;
    return ok(empty);
  }));
  render(<GradingControls projectId={id} sourceKey="" analysesReady={false} sequence={sequence} onState={vi.fn()} />);
  await screen.findByLabelText("Color mode");
  fireEvent.change(screen.getByLabelText("Color mode"), { target: { value: "original" } });
  fireEvent.click(screen.getByRole("button", { name: "Save color mode and matches" }));
  await screen.findByRole("alert");
  expect(screen.getByLabelText("Color mode")).toHaveValue("original");
  expect(screen.getByText(/Unsaved color changes/)).toBeInTheDocument();
});
