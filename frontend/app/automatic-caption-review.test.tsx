import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AutomaticCaptionReview } from "./automatic-caption-review";
afterEach(() => vi.unstubAllGlobals());
it("shows a source-bound reference frame, saves literal three-line corrections and explicit verification", async () => {
  const onSaved = vi.fn(), onDirty = vi.fn(), calls: Record<string, unknown>[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit) => {
    const body = JSON.parse(init.body as string);
    if (url.endsWith("/reference-frame")) return { ok: true, json: async () => ({ reference_operation_id: "reference-id", image: { png_base64: "ZmFrZQ==" } }) };
    calls.push(body); return { ok: true, json: async () => ({ revision: 3 }) };
  }));
  render(<AutomaticCaptionReview projectId="project-id" revision={2} review={{ cues: [{ start: 1, end: 2, text: "Uncertain words", appearance: { style: { color: "yellow" } } }], unreadable_samples: 1, evidence: { reference_operation_id: "reference-id" } }} disabled={false} onSaved={onSaved} onDirty={onDirty} />);
  fireEvent.click(screen.getByText("Correct uncertain words beside the reference"));
  expect(await screen.findByAltText("Retained reference at this cue’s midpoint")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Visible words"), { target: { value: "Literal {\\b1}\nمرحبا\nThird line" } });
  fireEvent.click(screen.getByLabelText("I verified these words and timing against the reference"));
  fireEvent.click(screen.getByRole("button", { name: "Save corrections" }));
  await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
  expect(calls[0]).toMatchObject({ expected_revision: 2, verified_indices: [0], cues: [{ start: 1, end: 2, text: "Literal {\\b1}\nمرحبا\nThird line", appearance: { style: { color: "yellow" } } }] });
  expect(onDirty).toHaveBeenLastCalledWith(false);
});
it("keeps the edited draft on a revision conflict", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, json: async () => ({ error: { message: "Caption reading changed. Reload before correcting." } }) })));
  render(<AutomaticCaptionReview projectId="project-id" revision={2} review={{ cues: [{ start: 1, end: 2, text: "Old words" }], unreadable_samples: 1 }} disabled={false} onSaved={vi.fn()} onDirty={vi.fn()} />);
  fireEvent.click(screen.getByText("Correct uncertain words beside the reference"));
  fireEvent.change(screen.getByLabelText("Visible words"), { target: { value: "My correction" } });
  fireEvent.click(screen.getByRole("button", { name: "Save corrections" }));
  await screen.findByRole("alert"); expect(screen.getByLabelText("Visible words")).toHaveValue("My correction");
  expect(screen.getByRole("status")).toHaveTextContent("Unsaved corrections");
});


it("keeps correction drafts when moving between result and Customize surfaces", () => {
 const target = document.createElement("div"); document.body.append(target);
 const props = {projectId:"one",revision:1,review:{cues:[{start:1,end:2,text:"Original"}],unreadable_samples:1},disabled:false,onSaved:vi.fn(),onDirty:vi.fn()};
 const view=render(<AutomaticCaptionReview {...props} />);
 fireEvent.click(screen.getByText("Correct uncertain words beside the reference"));
 fireEvent.change(screen.getByLabelText("Visible words"),{target:{value:"My retained correction"}});
 view.rerender(<AutomaticCaptionReview {...props} portalTarget={target} />);
 fireEvent.click(screen.getByText("Correct uncertain words beside the reference"));
 expect(screen.getByLabelText("Visible words")).toHaveValue("My retained correction");
 view.rerender(<AutomaticCaptionReview {...props} />);
 expect(screen.getByLabelText("Visible words")).toHaveValue("My retained correction");
 view.unmount(); target.remove();
});
