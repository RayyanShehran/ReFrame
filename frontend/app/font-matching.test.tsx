import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { FontMatching, RegionSelection, initialRectangle } from "./font-matching";
import { readStyle, validFont } from "./caption-style-controls";
const image = { width: 640, height: 360, png_base64: "iVBORw0KGgoAAA==" };
const font = { kind: "builtin" as const, candidate_id: "anton-regular" as const, font_id: null, sha256: "a".repeat(64), family: "Anton", style: "Regular (400)" };
const frame = { project_id: "one", reference_operation_id: "22222222-2222-4222-8222-222222222222", source: { media_sha256: "b".repeat(64) }, requested_timestamp_seconds: 1, timestamp_seconds: 1, image };
const selection = { schema_version: 1, method: "glyph-mask-dice-v1", ...frame, rectangle: initialRectangle, text: "EXACT Caption!", polarity: "light", reviewed_font: null };
const result = { project_id: "one", revision: 1, selection, token: "c".repeat(64), crop: image, ranked: [{ font, visual_similarity: 80, image }, { font: { ...font, sha256: "d".repeat(64), candidate_id: "amiri-bold", family: "Amiri", style: "Bold (700)" }, visual_similarity: 40, image }], skipped: [], warnings: [] };
const ok = (data: unknown) => ({ ok: true, json: async () => data } as Response);
afterEach(() => vi.unstubAllGlobals());
it("restores the crop/text and explicitly reviews a rendered candidate, rejecting outdated text results", async () => {
 const choose = vi.fn(), rectangle = vi.fn(), inspect = vi.fn();
 const fetcher = vi.fn(async (url: string, options: RequestInit) => {
   if (url.endsWith("/review")) { expect(JSON.parse(options.body as string)).toEqual({expected_revision: 1, token: result.token, choice: "anton-regular", expected_font_hash: font.sha256}); return ok({choice: "anton-regular", font}); }
   if (options.method === "POST") { expect(JSON.parse(options.body as string).text).toBe(selection.text); return ok(result); }
   return ok({revision: 0, status: "empty", selection: null});
 });
 vi.stubGlobal("fetch", fetcher);
 render(<FontMatching projectId="one" frame={frame} rectangle={initialRectangle} onRectangle={rectangle} onInspectTime={inspect} onChoose={choose} disabled={false} />);
 await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
 fireEvent.change(screen.getByLabelText("Exact visible caption text"), {target: {value: selection.text}});
 await waitFor(() => expect(screen.getByRole("button", {name: "Find similar fonts"})).toBeEnabled());
 fireEvent.click(screen.getByRole("button", {name: "Find similar fonts"}));
 await screen.findByText(/Visual similarity 80.0/);
 expect(choose).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole("button", {name: "Choose Anton Regular (400)"}));
 await waitFor(() => expect(choose).toHaveBeenCalledWith("anton-regular"));
 expect(screen.getByText(/Suggested font, user selected/)).toBeVisible();
 fireEvent.change(screen.getByLabelText("Exact visible caption text"), {target: {value: "CHANGED Caption!"}});
 expect(screen.getByText(/Outdated font comparison/)).toBeVisible();
 expect(screen.getByRole("button", {name: "Choose Anton Regular (400)"})).toBeDisabled();
 expect(screen.getByRole("img", {name: "Anton Regular (400) rendered with confirmed text"})).toBeVisible();
});
it("restores saved selections without decoding automatically and discards late comparisons after source changes", async () => {
 let release!: (r: Response) => void;
 vi.stubGlobal("fetch", vi.fn(async (_url: string, options: RequestInit) => options.method === "POST" ? new Promise<Response>(resolve => {release = resolve;}) : ok({revision: 1, status: "ready", selection: {...selection, reviewed_font: font}})));
 const choose = vi.fn(), rectangle = vi.fn(), inspect = vi.fn();
 const props = {projectId: "one", frame, rectangle: initialRectangle, onRectangle: rectangle, onInspectTime: inspect, onChoose: choose, disabled: false};
 const view = render(<FontMatching {...props} />);
 await waitFor(() => expect(screen.getByLabelText("Exact visible caption text")).toHaveValue(selection.text));
 expect(rectangle).toHaveBeenCalledWith(initialRectangle);
 fireEvent.click(screen.getByRole("button", {name: "Inspect saved selection frame"}));
 expect(inspect).toHaveBeenCalledWith(1);
 fireEvent.click(screen.getByRole("button", {name: "Find similar fonts"}));
 view.rerender(<FontMatching {...props} frame={{...frame, source: {media_sha256: "e".repeat(64)}}} />);
 await act(async () => release(ok({...result, revision: 2})));
 expect(screen.queryByText(/Visual similarity 80.0/)).not.toBeInTheDocument();
 expect(choose).not.toHaveBeenCalled();
});
it("supports numeric-independent keyboard movement and resizing of decoded-frame coordinates", () => {
 const change = vi.fn(); render(<RegionSelection frame={frame} rectangle={initialRectangle} onChange={change} disabled={false} />);
 const region = screen.getByRole("group", {name: "Caption region selection"});
 fireEvent.keyDown(region, {key: "ArrowRight"}); expect(change).toHaveBeenLastCalledWith({...initialRectangle, x: .11});
 fireEvent.keyDown(region, {key: "ArrowDown", shiftKey: true}); expect(change).toHaveBeenLastCalledWith({...initialRectangle, height: .41});
 expect(validFont(font)).toBe(true); expect(readStyle({font: "anton-regular", font_origin: "assisted"}).font).toBe("anton-regular");
 expect(() => readStyle({font: "invented"})).toThrow();
});

function pointerSurface() {
 class TouchPointer extends MouseEvent {
  pointerId: number; pointerType: string;
  constructor(type: string, options: PointerEventInit) { super(type, options); this.pointerId = options.pointerId ?? 1; this.pointerType = options.pointerType ?? "touch"; }
 }
 vi.stubGlobal("PointerEvent", TouchPointer);
 const change = vi.fn(); render(<RegionSelection frame={frame} rectangle={initialRectangle} onChange={change} disabled={false} />);
 const element = screen.getByRole("group", {name: "Caption region selection"});
 const bounds = {left: 10, top: 20, width: 320, height: 180};
 vi.spyOn(element, "getBoundingClientRect").mockImplementation(() => ({...bounds}) as DOMRect);
 element.setPointerCapture = vi.fn(); element.hasPointerCapture = vi.fn(() => true); element.releasePointerCapture = vi.fn();
 return {element, bounds, change};
}
it("draws touch coordinates against displayed dimensions, ignores other fingers and rolls cancellation back", () => {
 const {element, change} = pointerSurface();
 fireEvent.pointerDown(element, {pointerId: 3, clientX: 42, clientY: 56, button: 0});
 fireEvent.pointerMove(element, {pointerId: 4, clientX: 170, clientY: 110}); expect(change).not.toHaveBeenCalled();
 fireEvent.pointerMove(element, {pointerId: 3, clientX: 170, clientY: 110});
 expect(change.mock.lastCall![0]).toEqual({x: .1, y: .2, width: .4, height: .3});
 fireEvent.pointerCancel(element, {pointerId: 3}); expect(change).toHaveBeenLastCalledWith(initialRectangle);
 fireEvent.pointerMove(element, {pointerId: 3, clientX: 250, clientY: 140}); expect(change).toHaveBeenCalledTimes(2);
 expect(element.releasePointerCapture).toHaveBeenCalledWith(3);
});
it("moves and resizes through touch handles with bounds, cancelling an in-progress gesture after resizing", () => {
 const {element, bounds, change} = pointerSurface();
 fireEvent.pointerDown(screen.getByRole("button", {name: "Move selected caption region"}), {pointerId: 1, clientX: 170, clientY: 110, button: 0});
 fireEvent.pointerMove(element, {pointerId: 1, clientX: 330, clientY: 200});
 expect(change.mock.lastCall![0]).toMatchObject({x: .19999999999999996, y: .6, width: .8, height: .4});
 fireEvent.pointerUp(element, {pointerId: 1});
 fireEvent.pointerDown(screen.getByRole("button", {name: "Resize selected caption region"}), {pointerId: 2, clientX: 170, clientY: 110, button: 0});
 fireEvent.pointerMove(element, {pointerId: 2, clientX: 330, clientY: 200});
 expect(change.mock.lastCall![0]).toMatchObject({width: .9, height: .7});
 bounds.width = 640;
 fireEvent.pointerMove(element, {pointerId: 2, clientX: 330, clientY: 200});
 expect(change).toHaveBeenLastCalledWith(initialRectangle);
 fireEvent.pointerMove(element, {pointerId: 2, clientX: 400, clientY: 200}); expect(change).toHaveBeenCalledTimes(3);
});
