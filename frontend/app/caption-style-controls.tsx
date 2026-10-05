"use client";

import { useEffect, useRef, useState } from "react";

export type FontBinding = { kind: "default" | "custom"; font_id: string | null; sha256: string; family: string; style: string };
export type CaptionStyle = { color: string; size: "small" | "medium" | "large"; placement: "bottom-center" | "center"; font: "default" | "custom"; size_percent: number | null; outline_color: string; outline_percent: number | null; shadow_color: string; shadow_percent: number; alignment: "left" | "center" | "right"; horizontal: number | null; vertical: number | null; bold: boolean; italic: boolean };
export const defaultStyle: CaptionStyle = { color: "white", size: "medium", placement: "bottom-center", font: "default", size_percent: null, outline_color: "#000000", outline_percent: null, shadow_color: "#000000", shadow_percent: 0, alignment: "center", horizontal: null, vertical: null, bold: false, italic: false };
const color = (v: unknown) => typeof v === "string" && /^(white|yellow|#[0-9a-f]{6})$/i.test(v);
const bounded = (v: unknown, min: number, max: number, nullable = false) => (nullable && v === null) || (typeof v === "number" && Number.isFinite(v) && v >= min && v <= max);
export function readStyle(value: unknown): CaptionStyle {
  if (!value || typeof value !== "object") throw new Error("Invalid caption style.");
  const s = { ...defaultStyle, ...value } as CaptionStyle;
  if (!color(s.color) || !/^#[0-9a-f]{6}$/i.test(s.outline_color) || !/^#[0-9a-f]{6}$/i.test(s.shadow_color) || !["small", "medium", "large"].includes(s.size) || !["bottom-center", "center"].includes(s.placement) || !["default", "custom"].includes(s.font) || !["left", "center", "right"].includes(s.alignment) || !bounded(s.size_percent, 2, 15, true) || !bounded(s.outline_percent, 0, 2, true) || !bounded(s.shadow_percent, 0, 3) || !bounded(s.horizontal, 0, 1, true) || !bounded(s.vertical, 0, 1, true) || typeof s.bold !== "boolean" || typeof s.italic !== "boolean") throw new Error("Invalid caption style.");
  return s;
}
export function validFont(f: FontBinding): boolean {
  return !!f && ["default", "custom"].includes(f.kind) && /^[0-9a-f]{64}$/.test(f.sha256) && typeof f.family === "string" && f.family.length > 0 && f.family.length <= 128 && typeof f.style === "string" && f.style.length <= 128 && (f.kind === "default" ? f.font_id === null : typeof f.font_id === "string" && /^[0-9a-f-]{36}$/.test(f.font_id));
}
export const fontLabel = (f?: FontBinding) => !f || f.kind === "default" ? "Default font · DejaVu Sans" : `Manually selected font · ${f.family} (${f.style})`;
const apiBase = (process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
type Asset = { revision: number; available: boolean; message: string | null; font: FontBinding | null };
function asset(data: unknown): Asset {
  const a = data as Asset;
  if (!a || !Number.isSafeInteger(a.revision) || a.revision < 0 || typeof a.available !== "boolean" || (a.font && (!validFont(a.font) || a.font.kind !== "custom"))) throw new Error("Invalid font response.");
  return a;
}
export function FontPicker({ projectId, revision, value, dirty, disabled, onChange, onUploaded, onBusy }: { projectId: string; revision: number; value: CaptionStyle["font"]; dirty: boolean; disabled: boolean; onChange: (value: CaptionStyle["font"]) => void; onUploaded: () => Promise<void>; onBusy?: (busy: boolean) => void }) {
  const [saved, setSaved] = useState<Asset | null>(null), [file, setFile] = useState<File | null>(null), [replace, setReplace] = useState(false);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const action = useRef<AbortController | null>(null);
  useEffect(() => { onBusy?.(busy); return () => onBusy?.(false); }, [busy, onBusy]);
  async function run(upload = false) {
    if (action.current || (upload && (!saved || !file || dirty || (saved.font && !replace)))) return;
    const controller = new AbortController(); action.current = controller; setBusy(true); setError("");
    const timer = setTimeout(() => controller.abort(), 45000);
    try {
      const query = upload ? `?expected_revision=${saved!.revision}&expected_caption_revision=${revision}&replace=${replace}` : "";
      const response = await fetch(`${apiBase}/api/projects/${encodeURIComponent(projectId)}/caption-font${query}`, { method: upload ? "POST" : "GET", signal: controller.signal, ...(upload ? { body: file!, headers: { "Content-Type": "application/octet-stream" } } : {}) });
      const data = await response.json();
      if (!response.ok) throw new Error(data?.error?.message || "Font request failed; the previous font is retained.");
      const result = asset(data);
      if (!controller.signal.aborted) { setSaved(result); if (upload) { setFile(null); setReplace(false); await onUploaded(); } }
    } catch (cause) { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "Font request failed."); else if (action.current === controller) setError("Font request timed out. Reload its status explicitly."); }
    finally { clearTimeout(timer); if (action.current === controller) { action.current = null; setBusy(false); } }
  }
  useEffect(() => {
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 10000);
    void fetch(`${apiBase}/api/projects/${encodeURIComponent(projectId)}/caption-font`, { signal: controller.signal }).then(async response => {
      const data = await response.json();
      if (!response.ok) throw new Error(data?.error?.message || "Font status could not be loaded.");
      const result = asset(data); if (!controller.signal.aborted) setSaved(result);
    }).catch(cause => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "Font status could not be loaded."); })
      .finally(() => clearTimeout(timer));
    return () => { clearTimeout(timer); controller.abort(); action.current?.abort(); action.current = null; };
  }, [projectId]);
  return <fieldset className="caption-cue" disabled={disabled || busy}><legend>Caption font</legend>
    <label>Font <select value={value} onChange={e => onChange(e.target.value as CaptionStyle["font"])}><option value="default">Default font · DejaVu Sans</option><option value="custom" disabled={!saved?.available || !saved.font}>{saved?.font ? `Manually selected font · ${saved.font.family} (${saved.font.style})` : "Upload a custom font first"}</option></select></label>
    {saved?.font && <p>Saved custom font: {saved.font.family} ({saved.font.style}) · Font revision {saved.revision}{!saved.available && " · Unavailable"}</p>}
    <p className="hint">Reference appearance not automatically verified. Choose a font you have rights to use. Static TTF/OTF only, up to 2 MiB; variable, collection and color fonts are unsupported. Unsupported custom-font characters are rejected.</p>
    <label>Upload custom font<input type="file" accept=".ttf,.otf" onChange={e => { const chosen = e.target.files?.[0]; e.target.value = ""; setReplace(false); if (!chosen) return; if (!chosen.size || chosen.size > 2 * 1024 * 1024) { setError("Choose a nonempty font no larger than 2 MiB."); return; } setFile(chosen); setError(""); }} /></label>
    {file && <p>Selected file: {file.name}</p>}
    {file && saved?.font && <label><input type="checkbox" checked={replace} onChange={e => setReplace(e.target.checked)} /> Replace the saved custom font; captions using it keep their text and timing and get a new revision.</label>}
    {dirty && <p>Save or discard caption edits before uploading a font.</p>}
    <button disabled={!file || !saved || dirty || (!!saved.font && !replace)} onClick={() => void run(true)}>{saved?.font ? "Replace custom font" : "Upload font"}</button>
    <button onClick={() => void run()}>Reload font status</button>
    {busy && <p role="status">Validating or loading font…</p>}{error && <p role="alert">{error}</p>}{saved?.message && <p role="alert">{saved.message}</p>}
  </fieldset>;
}
export function StyleControls({ value, onChange }: { value: CaptionStyle; onChange: (value: CaptionStyle) => void }) {
  const s = value, set = <K extends keyof CaptionStyle>(key: K, v: CaptionStyle[K]) => onChange({ ...s, [key]: v });
  const range = (key: "size_percent" | "outline_percent" | "shadow_percent" | "horizontal" | "vertical", label: string, min: number, max: number, step: number, fallback: number, percent = false) => {
    const v = s[key] ?? fallback;
    return <label>{label} · {(v * (percent ? 100 : 1)).toFixed(percent ? 0 : 1)}%<input aria-label={label} type="range" min={min} max={max} step={step} value={v} onChange={e => set(key, Number(e.target.value))} /></label>;
  };
  return <fieldset className="caption-cue"><legend>Caption appearance</legend>
    <label>Text color<input type="color" value={s.color === "white" ? "#ffffff" : s.color === "yellow" ? "#ffff00" : s.color} onChange={e => set("color", e.target.value)} /></label>
    <label>Caption size <select value={s.size} onChange={e => onChange({ ...s, size: e.target.value as CaptionStyle["size"], size_percent: null })}>{["small", "medium", "large"].map(v => <option key={v} value={v}>{v}</option>)}</select></label>
    {range("size_percent", "Text size (% of output height)", 2, 15, .1, { small: 3.5, medium: 5, large: 7 }[s.size])}
    <label>Outline color<input type="color" value={s.outline_color} onChange={e => set("outline_color", e.target.value)} /></label>
    {range("outline_percent", "Outline width (% of output height)", 0, 2, .1, .3)}
    <label>Shadow color<input type="color" value={s.shadow_color} onChange={e => set("shadow_color", e.target.value)} /></label>
    {range("shadow_percent", "Shadow offset (% of output height)", 0, 3, .1, 0)}
    <label>Caption placement <select value={s.placement} onChange={e => onChange({ ...s, placement: e.target.value as CaptionStyle["placement"], horizontal: null, vertical: null })}><option value="bottom-center">Bottom-center</option><option value="center">Center</option></select></label>
    <label>Text alignment <select value={s.alignment} onChange={e => set("alignment", e.target.value as CaptionStyle["alignment"])}>{["left", "center", "right"].map(v => <option key={v} value={v}>{v}</option>)}</select></label>
    {range("horizontal", "Horizontal position (left to right)", 0, 1, .01, .5, true)}
    {range("vertical", "Vertical position (top to bottom)", 0, 1, .01, s.placement === "center" ? .5 : .94, true)}
    <label><input type="checkbox" checked={s.bold} onChange={e => set("bold", e.target.checked)} /> Bold</label>
    <label><input type="checkbox" checked={s.italic} onChange={e => set("italic", e.target.checked)} /> Italic</label>
    <p className="hint">Position anchors the chosen alignment. Size, outline and shadow use final output height. Bold/italic may be synthesized for a static face; review the real preview and export. Very short cues can fall between 30 fps frames. Default-font unsupported glyphs may use system fallback.</p>
  </fieldset>;
}
