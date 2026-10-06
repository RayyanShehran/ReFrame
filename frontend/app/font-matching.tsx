"use client";

import Image from "next/image";
import { AppearanceSuggestions, selectionKey, type AppearancePatch } from "./caption-appearance-suggestions";
import { useEffect, useRef, useState } from "react";
import { fontChoice, fontLabel, validFont, type CaptionStyle, type FontBinding, type FontChoice } from "./caption-style-controls";

export type Picture = { width: number; height: number; png_base64: string };
export type MatchFrame = { project_id: string; reference_operation_id: string; source: { media_sha256: string }; requested_timestamp_seconds: number; timestamp_seconds: number; image: Picture };
export type Rectangle = { x: number; y: number; width: number; height: number };
export const initialRectangle: Rectangle = { x: .1, y: .3, width: .8, height: .4 };
export type Selection = { schema_version: 1; method: string; source: { media_sha256: string }; reference_operation_id: string; timestamp_seconds: number; requested_timestamp_seconds: number; rectangle: Rectangle; text: string; polarity: "light" | "dark"; reviewed_font: FontBinding | null };
type Saved = { revision: number; status: "empty" | "ready" | "stale"; selection: Selection | null };
type Ranked = { project_id: string; revision: number; token: string; selection: Selection; crop: Picture; ranked: { font: FontBinding; visual_similarity: number; image: Picture }[]; skipped: string[]; warnings: string[] };
const apiBase = (process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
const finite = (n: unknown, min = 0, max = 1): n is number => typeof n === "number" && Number.isFinite(n) && n >= min && n <= max;
const hash = (s: unknown) => typeof s === "string" && /^[0-9a-f]{64}$/.test(s);
const validRectangle = (r: Rectangle) => r && [r.x, r.y, r.width, r.height].every(v => finite(v)) && r.width > 0 && r.height > 0 && r.x + r.width <= 1.000001 && r.y + r.height <= 1.000001;
const validPicture = (p: Picture) => p && [p.width, p.height].every(n => Number.isInteger(n) && n >= 2 && n <= 960) && typeof p.png_base64 === "string" && p.png_base64.length <= 4194304 && /^iVBORw0KGgo[A-Za-z0-9+/]*={0,2}$/.test(p.png_base64);
function validSelection(s: Selection) {
  return s && s.schema_version === 1 && typeof s.method === "string" && validRectangle(s.rectangle) && hash(s.source?.media_sha256) && typeof s.reference_operation_id === "string" && finite(s.timestamp_seconds, 0, 120.1) && finite(s.requested_timestamp_seconds, 0, 120.1) && typeof s.text === "string" && Array.from(s.text).length <= 80 && !!s.text.trim() && ["light", "dark"].includes(s.polarity) && (!s.reviewed_font || validFont(s.reviewed_font));
}
function readSaved(value: unknown): Saved {
  const s = value as Saved;
  if (!s || !Number.isSafeInteger(s.revision) || s.revision < 0 || !["empty", "ready", "stale"].includes(s.status) || (s.status === "empty" ? s.selection !== null : !validSelection(s.selection!))) throw new Error("Invalid saved font selection.");
  return s;
}
function readRanked(value: unknown, projectId: string): Ranked {
  const r = value as Ranked;
  if (!r || r.project_id !== projectId || !Number.isSafeInteger(r.revision) || r.revision < 1 || !hash(r.token) || !validSelection(r.selection) || !validPicture(r.crop) || !Array.isArray(r.ranked) || r.ranked.length < 2 || r.ranked.length > 4 || r.ranked.some(c => !validFont(c.font) || !finite(c.visual_similarity, 0, 100) || !validPicture(c.image)) || ![r.skipped, r.warnings].every(list => Array.isArray(list) && list.length <= 12 && list.every(v => typeof v === "string" && v.length <= 2000))) throw new Error("Invalid font comparison.");
  return r;
}

export function RegionSelection({ frame, rectangle, onChange, disabled }: { frame: MatchFrame; rectangle: Rectangle; onChange: (r: Rectangle) => void; disabled: boolean }) {
  const drag = useRef<{ x: number; y: number } | null>(null);
  const point = (e: React.PointerEvent<HTMLDivElement>) => { const b = e.currentTarget.getBoundingClientRect(); return { x: Math.max(0, Math.min(1, (e.clientX - b.left) / b.width)), y: Math.max(0, Math.min(1, (e.clientY - b.top) / b.height)) }; };
  return <div className="font-region" role="group" aria-label="Caption region selection" tabIndex={disabled ? -1 : 0} aria-disabled={disabled}
    onPointerDown={e => { if (disabled) return; e.preventDefault(); drag.current = point(e); e.currentTarget.setPointerCapture(e.pointerId); }}
    onPointerMove={e => { if (!drag.current || disabled) return; const p = point(e), a = drag.current; onChange({ x: Math.min(a.x, p.x), y: Math.min(a.y, p.y), width: Math.max(.001, Math.abs(p.x - a.x)), height: Math.max(.001, Math.abs(p.y - a.y)) }); }}
    onPointerUp={e => { drag.current = null; if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId); }} onPointerCancel={() => { drag.current = null; }}
    onKeyDown={e => { if (disabled || !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(e.key)) return; e.preventDefault(); const horizontal = ["ArrowLeft", "ArrowRight"].includes(e.key), direction = ["ArrowRight", "ArrowDown"].includes(e.key) ? 1 : -1, key = e.shiftKey ? horizontal ? "width" : "height" : horizontal ? "x" : "y", max = key === "x" ? 1 - rectangle.width : key === "y" ? 1 - rectangle.height : key === "width" ? 1 - rectangle.x : 1 - rectangle.y; onChange({ ...rectangle, [key]: Math.max(e.shiftKey ? .001 : 0, Math.min(max, Math.round((rectangle[key] + direction * .01) * 1000) / 1000)) }); }}>
    <Image unoptimized draggable={false} src={`data:image/png;base64,${frame.image.png_base64}`} width={frame.image.width} height={frame.image.height} alt={`Retained reference frame at ${frame.timestamp_seconds.toFixed(3)} seconds`} />
    <div className="font-region-box" style={{ left: `${rectangle.x * 100}%`, top: `${rectangle.y * 100}%`, width: `${rectangle.width * 100}%`, height: `${rectangle.height * 100}%` }} />
  </div>;
}

export function FontMatching({ projectId, frame, rectangle, onRectangle, onInspectTime, onChoose, onBusy, disabled, draftStyle, onAppearance }: { projectId: string; frame: MatchFrame | null; rectangle: Rectangle; onRectangle: (r: Rectangle) => void; onInspectTime: (seconds: number) => void; onChoose: (choice: FontChoice) => void; onBusy?: (busy: boolean) => void; disabled: boolean; draftStyle?: CaptionStyle; onAppearance?: (patch: AppearancePatch) => void }) {
  const [appearanceBusy, setAppearanceBusy] = useState(false);
  const [saved, setSaved] = useState<Saved | null>(null), [result, setResult] = useState<Ranked | null>(null);
  const [text, setText] = useState(""), [polarity, setPolarity] = useState<"light" | "dark">("light"), [matchingBusy, setBusy] = useState(false), [error, setError] = useState("");
  const busy = matchingBusy || appearanceBusy;
  const action = useRef<AbortController | null>(null), generation = useRef(0);
  const rectangleCallback = useRef(onRectangle);
  useEffect(() => { rectangleCallback.current = onRectangle; }, [onRectangle]);
  const token = JSON.stringify([projectId, frame?.reference_operation_id, frame?.source.media_sha256, frame?.requested_timestamp_seconds, rectangle, text, polarity]);
  const current = !!result && !!frame && result.selection.reference_operation_id === frame.reference_operation_id && result.selection.source.media_sha256 === frame.source.media_sha256 && result.selection.requested_timestamp_seconds === frame.requested_timestamp_seconds && result.selection.text === text && result.selection.polarity === polarity && JSON.stringify(result.selection.rectangle) === JSON.stringify(rectangle) && saved?.status === "ready";
  const useful = Array.from(text).length <= 80 && text.split("\n").length <= 2 && (text.match(/[\p{L}\p{N}]/gu)?.length ?? 0) >= 4 && !/[\x00-\x09\x0b-\x1f\x7f]/.test(text);
  useEffect(() => { onBusy?.(busy); return () => onBusy?.(false); }, [busy, onBusy]);
  useEffect(() => { function cancel() { generation.current++; action.current?.abort(); action.current = null; } cancel(); return cancel; }, [token]);
  useEffect(() => {
    const c = new AbortController(), timer = setTimeout(() => c.abort(), 15000);
    void fetch(`${apiBase}/api/projects/${encodeURIComponent(projectId)}/font-match`, { signal: c.signal }).then(async response => { const data = await response.json(); if (!response.ok) throw new Error(data?.error?.message || "Font selection could not be loaded."); const value = readSaved(data); if (!c.signal.aborted) { setSaved(value); if (value.selection) { setText(value.selection.text); setPolarity(value.selection.polarity); rectangleCallback.current(value.selection.rectangle); } } }).catch(e => { if (!c.signal.aborted) setError(e instanceof Error ? e.message : "Selection could not be loaded."); }).finally(() => clearTimeout(timer));
    return () => { clearTimeout(timer); c.abort(); };
  }, [projectId]);
  async function run(candidate?: Ranked["ranked"][number], reload = false) {
    if (action.current || (!reload && (!saved || !frame || !validRectangle(rectangle) || !useful || disabled || (candidate && !current)))) return;
    const controller = new AbortController(), version = ++generation.current; action.current = controller; setBusy(true); setError("");
    const timer = setTimeout(() => controller.abort(), 45000);
    try {
      const body = reload ? null : candidate ? { expected_revision: result!.revision, token: result!.token, choice: fontChoice(candidate.font), expected_font_hash: candidate.font.sha256 } : { expected_revision: saved!.revision, expected_reference_operation_id: frame!.reference_operation_id, expected_source_hash: frame!.source.media_sha256, timestamp_seconds: frame!.requested_timestamp_seconds, rectangle, text, polarity };
      const response = await fetch(`${apiBase}/api/projects/${encodeURIComponent(projectId)}/font-match${candidate ? "/review" : ""}`, { method: reload ? "GET" : "POST", signal: controller.signal, ...(!reload ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}) });
      const data = await response.json(); if (!response.ok) { if (response.status === 409) setSaved(v => v ? { ...v, status: "stale" } : v); throw new Error(data?.error?.message || "Comparison failed. Previous images are retained; retry explicitly."); }
      if (version !== generation.current || controller.signal.aborted) return;
      if (reload) setSaved(readSaved(data));
      else if (candidate) { if (!validFont(data.font) || data.font.sha256 !== candidate.font.sha256 || data.choice !== fontChoice(candidate.font)) throw new Error("Invalid reviewed font."); setSaved(v => v?.selection ? { ...v, selection: { ...v.selection, reviewed_font: data.font } } : v); onChoose(data.choice); }
      else { const ranked = readRanked(data, projectId); if (ranked.revision !== saved!.revision + 1 || ranked.selection.text !== text || ranked.selection.polarity !== polarity || ranked.selection.reference_operation_id !== frame!.reference_operation_id || ranked.selection.source.media_sha256 !== frame!.source.media_sha256 || ranked.selection.requested_timestamp_seconds !== frame!.requested_timestamp_seconds || JSON.stringify(ranked.selection.rectangle) !== JSON.stringify(rectangle)) throw new Error("Comparison does not match this selection."); setResult(ranked); setSaved({ revision: ranked.revision, status: "ready", selection: ranked.selection }); }
    } catch (e) { if (version === generation.current) setError(e instanceof Error && e.name !== "AbortError" ? e.message : "Comparison timed out. Reload saved selection before retrying; previous images remain."); }
    finally { clearTimeout(timer); if (action.current === controller) action.current = null; setBusy(false); }
  }
  return <section className="caption-cue" aria-label="Assisted font matching"><h4>Find similar caption fonts</h4>
    <p className="hint">Drag a tight rectangle around one caption in the reference frame above. Arrow keys move it; Shift + arrows resize it. Numeric coordinates are percentages of the decoded frame, independent of display size. Confirm the exact visible text; no OCR is used.</p>
    {saved?.selection && <p>Saved selection · Source {saved.selection.timestamp_seconds.toFixed(3)}s · Revision {saved.revision}{saved.status === "stale" && " · Outdated source or font assets"} <button disabled={busy || disabled} onClick={() => onInspectTime(saved.selection!.requested_timestamp_seconds)}>Inspect saved selection frame</button></p>}
    {!frame && <p>Inspect a current retained reference frame before comparing.</p>}
    <fieldset disabled={busy || disabled}><legend>Caption region and confirmed text</legend>
      <div className="font-region-numbers">{(["x", "y", "width", "height"] as const).map(k => <label key={k}>Region {k} (%)<input type="number" aria-label={`Region ${k} (%)`} min={k === "width" || k === "height" ? .1 : 0} max="100" step=".1" value={Number((rectangle[k] * 100).toFixed(3))} onChange={e => { const value = e.target.valueAsNumber / 100; if (finite(value)) onRectangle({ ...rectangle, [k]: value }); }} /></label>)}</div>
      {!validRectangle(rectangle) && <p role="alert">Keep a nonempty rectangle inside the frame.</p>}
      <label>Exact visible caption text<textarea aria-label="Exact visible caption text" dir="auto" rows={2} value={text} onChange={e => setText(e.target.value)} /></label>
      <p>{Array.from(text).length}/80 characters · At least four letters/numbers, at most two lines. Case, punctuation and line breaks are preserved.</p>
      <label>Reference text polarity<select value={polarity} onChange={e => setPolarity(e.target.value as "light" | "dark")}><option value="light">Light text on darker background</option><option value="dark">Dark text on lighter background</option></select></label>
      {frame && validRectangle(rectangle) && <figure><figcaption>Selected reference crop · Confirmed text: <span className="caption-text" dir="auto">{text || "Confirm the visible text"}</span></figcaption><svg className="font-crop" role="img" aria-label="Selected reference crop" viewBox={`${rectangle.x * frame.image.width} ${rectangle.y * frame.image.height} ${rectangle.width * frame.image.width} ${rectangle.height * frame.image.height}`}><image href={`data:image/png;base64,${frame.image.png_base64}`} width={frame.image.width} height={frame.image.height} /></svg></figure>}
      <button disabled={!saved || !frame || !useful || !validRectangle(rectangle)} onClick={() => void run()}>Find similar fonts</button>
    </fieldset>
    <button disabled={busy || disabled} onClick={() => void run(undefined, true)}>Reload saved font selection</button>
    {busy && <p role="status">Comparing actual rendered glyphs… Previous comparison remains visible. Processing is capped at 30 seconds.</p>}{error && <p role="alert">{error}</p>}
    {result && <><p role="status">{current ? "Font comparison" : "Outdated font comparison — compare the current region/text before choosing"} · Visual similarity is not the probability of exact font identity.</p><div className="font-candidates"><figure><figcaption>Compared reference crop · <span className="caption-text" dir="auto">{result.selection.text}</span></figcaption><Image unoptimized src={`data:image/png;base64,${result.crop.png_base64}`} width={result.crop.width} height={result.crop.height} alt="Compared reference crop" /></figure>{result.ranked.map((c, i) => <figure key={`${c.font.kind}:${c.font.sha256}`}><figcaption>{i + 1}. {c.font.family} ({c.font.style}) · Visual similarity {c.visual_similarity.toFixed(1)}/100</figcaption><Image unoptimized src={`data:image/png;base64,${c.image.png_base64}`} width={c.image.width} height={c.image.height} alt={`${c.font.family} ${c.font.style} rendered with confirmed text`} /><p className="hint">Normalized glyph mask · Face hash {c.font.sha256.slice(0, 12)}</p><button disabled={busy || disabled || !current} onClick={() => void run(c)}>Choose {c.font.family} {c.font.style}</button></figure>)}</div>{[...result.skipped, ...result.warnings].map(w => <p role="note" key={w}>{w}</p>)}</>}
    {saved?.selection?.reviewed_font && <p role="status">{fontLabel(saved.selection.reviewed_font, "assisted")}. This review is saved separately; Save captions persists your styled draft.</p>}
    <button disabled={busy || disabled} onClick={() => document.getElementById(`caption-font-upload-${projectId}`)?.focus()}>None match—upload another font</button>
    {draftStyle && onAppearance && <AppearanceSuggestions projectId={projectId} currentKey={saved?.status === "ready" && saved.selection && saved.selection.text === text && saved.selection.polarity === polarity && JSON.stringify(saved.selection.rectangle) === JSON.stringify(rectangle) && (!frame || (frame.source.media_sha256 === saved.selection.source.media_sha256 && frame.requested_timestamp_seconds === saved.selection.requested_timestamp_seconds)) ? selectionKey(saved.selection, saved.revision) : null} baseStyle={draftStyle} disabled={disabled || matchingBusy} onBusy={setAppearanceBusy} onApply={onAppearance} />}
    <p className="hint">Font comparison measures glyph shape only. Use appearance suggestions for supported size, color, outline and position estimates. Shadow and animation are not estimated. Refine the existing controls, Save captions, then review the real preview/export. Complex backgrounds, compression and outlines can mislead; exact identity remains unverified.</p>
  </section>;
}
