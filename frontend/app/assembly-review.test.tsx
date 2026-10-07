import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AssemblyReview } from "./assembly-review";
import type { SourceClip } from "./clip-library";

const slot = { id: "11111111-1111-4111-8111-111111111111", clip_id: "22222222-2222-4222-8222-222222222222", duration_frames: 30, source_start_frame: 30 };
const settings = { expected_sequence_revision: 2, allow_reused_ranges: false, locked_slot_ids: [slot.id] };
const proposal = { id: "33333333-3333-4333-8333-333333333333", settings, sequence: { revision: 2 }, choices: [{ ...slot, explanation: "Locked saved assignment; unchanged.", warnings: [] }], warnings: ["No subject recognition."], cached_sources: 1 };
const status = { status: "ready", proposal, proposal_stale: false, completed_sources: 2, total_sources: 2, message: null, failure_code: null };
const ok = (data: unknown) => ({ ok: true, json: async () => data } as Response);
const props = { projectId: "one", revision: 2, savedSlots: [slot], clips: [{ id: slot.clip_id, name: "Camera A" } as SourceClip], dirty: false, disabled: false, onApply: vi.fn(), onPreview: vi.fn() };
afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); });

it("restores saved locks, previews without editing and confirms draft replacement before explicit apply", async () => {
  const fetcher = vi.fn(async (url: string, options: RequestInit) => {
    if (url.endsWith("/apply")) { expect(JSON.parse(options.body as string)).toEqual({ ...settings, proposal_id: proposal.id }); return ok({ expected_revision: 2, slots: [slot] }); }
    return ok(status);
  });
  vi.stubGlobal("fetch", fetcher);
  render(<AssemblyReview {...props} dirty />);
  await screen.findByText(/Saved proposal/);
  expect(screen.getByLabelText("Lock slot 1")).toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "Review proposed range 1" }));
  expect(props.onPreview).toHaveBeenCalledWith(proposal.choices[0]); expect(props.onApply).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Apply proposal to draft" }));
  expect(screen.getByRole("group", { name: "Confirm proposal replacement" })).toBeVisible();
  expect(fetcher.mock.calls.filter(c => c[0].endsWith("/apply"))).toHaveLength(0);
  fireEvent.click(screen.getByRole("button", { name: "Confirm replace draft" }));
  await waitFor(() => expect(props.onApply).toHaveBeenCalledWith([slot]));
  expect(screen.getByText(/Review or adjust it, then Save sequence/)).toBeVisible();
});

it("requires a new proposal after options change and preserves old choices during failure", async () => {
  vi.stubGlobal("fetch", vi.fn(async (_url: string, options: RequestInit) => options.method === "POST" ? ok({ ...status, status: "failed", message: "Decode failed; retry." }) : ok(status)));
  render(<AssemblyReview {...props} />);
  await screen.findByText(/Saved proposal/);
  fireEvent.click(screen.getByLabelText("Allow reused ranges"));
  expect(screen.getByRole("button", { name: "Apply proposal to draft" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Suggest an assembly" }));
  await screen.findByRole("button", { name: "Retry assembly suggestion" });
  expect(screen.getByText(proposal.choices[0].explanation)).toBeVisible();
});

it("ignores an apply response after the project panel is unmounted", async () => {
  let release!: (value: Response) => void;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => url.endsWith("/apply") ? new Promise<Response>(resolve => { release = resolve; }) : ok(status)));
  const view = render(<AssemblyReview {...props} />);
  await screen.findByText(/Saved proposal/);
  fireEvent.click(screen.getByRole("button", { name: "Apply proposal to draft" }));
  view.unmount();
  await act(async () => release(ok({ expected_revision: 2, slots: [slot] })));
  expect(props.onApply).not.toHaveBeenCalled();
});
