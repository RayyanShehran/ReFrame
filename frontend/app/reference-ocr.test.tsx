import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ReferenceOcr } from "./reference-ocr";
const cap = {available: true, method: "tesseract-fast-block-v1", token: "a".repeat(64), engine_version: "tesseract 5.5.3", assets_commit: "b".repeat(40), message: "Local OCR ready"};
const frame = {project_id: "one", reference_operation_id: "22222222-2222-4222-8222-222222222222", source: {media_sha256: "c".repeat(64)}, requested_timestamp_seconds: 1, timestamp_seconds: 1, image: {width: 640, height: 360, png_base64: "iVBORw0KGgoAAA=="}};
const rectangle = {x: .1, y: .4, width: .8, height: .4};
const proposal = {schema_version: 1, project_id: "one", source: frame.source, reference_operation_id: frame.reference_operation_id, requested_timestamp_seconds: 1, timestamp_seconds: 1, rectangle, language: "english", polarity: "light", ...cap, capability_token: cap.token, text: "Known caption", token: "d".repeat(64), processing_width: 640, processing_height: 200, preprocessing_attempts: 1};
const ok = (data: unknown) => ({ok: true, json: async () => data} as Response);
const props = () => ({projectId: "one", frame, rectangle, polarity: "light" as const, disabled: false, onApply: vi.fn(), onBusy: vi.fn()});
afterEach(() => vi.unstubAllGlobals());
it("requires explicit extraction and review, allowing correction rather than truncation", async () => {
 const p = props(); const fetcher = vi.fn(async (url: string, options?: RequestInit) => options?.method !== "POST" ? ok(cap) : url.endsWith("/apply") ? ok({text: JSON.parse(options.body as string).text}) : ok({...proposal, text: "a".repeat(81)}));
 vi.stubGlobal("fetch", fetcher); render(<ReferenceOcr {...p}/>);
 const extract = screen.getByRole("button", {name: "Extract text from this region"});
 await waitFor(() => expect(extract).toBeEnabled()); expect(fetcher).toHaveBeenCalledTimes(1);
 fireEvent.click(extract); const field = await screen.findByLabelText("Review extracted reference text");
 expect(field).toHaveValue("a".repeat(81)); expect(p.onApply).not.toHaveBeenCalled();
 expect(screen.getByRole("button", {name: "Apply text to matching field"})).toBeDisabled();
 fireEvent.change(field, {target: {value: "Caption {literal}"}});
 fireEvent.click(screen.getByRole("button", {name: "Apply text to matching field"}));
 await waitFor(() => expect(p.onApply).toHaveBeenCalledWith("Caption {literal}"));
 expect(fetcher.mock.calls.filter(([,options]) => options?.method === "POST").map(([url]) => url)).toEqual(["http://127.0.0.1:8000/api/projects/one/caption-ocr", "http://127.0.0.1:8000/api/projects/one/caption-ocr/apply"]);
});
it("ignores late extraction after crop changes and blocks older language proposals", async () => {
 let release!: (r: Response) => void; const p = props();
 vi.stubGlobal("fetch", vi.fn(async (_url: string, options?: RequestInit) => options?.method === "POST" ? new Promise<Response>(resolve => {release = resolve;}) : ok(cap)));
 const view = render(<ReferenceOcr {...p}/>);
 await waitFor(() => expect(screen.getByRole("button", {name: "Extract text from this region"})).toBeEnabled());
 fireEvent.click(screen.getByRole("button", {name: "Extract text from this region"})); view.rerender(<ReferenceOcr {...p} rectangle={{...rectangle, x: .05}}/>);
 await act(async () => release(ok(proposal)));
 expect(screen.queryByLabelText("Review extracted reference text")).not.toBeInTheDocument(); expect(p.onApply).not.toHaveBeenCalled();
 view.rerender(<ReferenceOcr {...p}/>); fireEvent.click(screen.getByRole("button", {name: "Extract text from this region"})); await act(async () => release(ok(proposal)));
 fireEvent.change(screen.getByLabelText("OCR language"), {target: {value: "arabic"}});
 expect(screen.getByText(/Outdated OCR proposal/)).toBeVisible(); expect(screen.getByRole("button", {name: "Apply text to matching field"})).toBeDisabled();
});
it("retains manual workflow on missing assets/timeout and permits explicit retry", async () => {
 const p = props(); let available = false, fail = true;
 vi.stubGlobal("fetch", vi.fn(async (_url: string, options?: RequestInit) => {
 if(options?.method !== "POST")return ok({...cap, available, message: available ? "Ready" : "Install local assets; manual entry remains available"});
 if(fail)throw Object.assign(new Error("timeout"), {name: "AbortError"}); return ok(proposal);
 })); render(<ReferenceOcr {...p}/>);
 await screen.findByText(/Install local assets/); expect(screen.getByRole("button", {name: "Extract text from this region"})).toBeDisabled();
 available = true; fireEvent.click(screen.getByRole("button", {name: "Reload OCR status"})); await waitFor(() => expect(screen.getByRole("button", {name: "Extract text from this region"})).toBeEnabled());
 fireEvent.click(screen.getByRole("button", {name: "Extract text from this region"})); await screen.findByText(/OCR timed out/); expect(p.onApply).not.toHaveBeenCalled(); fail = false;
 fireEvent.click(screen.getByRole("button", {name: "Extract text from this region"})); await screen.findByLabelText("Review extracted reference text"); fireEvent.click(screen.getByRole("button", {name: "Discard OCR proposal"})); expect(p.onApply).not.toHaveBeenCalled();
});
