"use client";

import Image from "next/image";
import { useEffect, useRef, useState } from "react";
import { readStyle, validFont, fontChoice, type CaptionStyle, type FontBinding } from "./caption-style-controls";
import type { Picture, Selection } from "./font-matching";

export type AppearancePatch = Partial<Pick<CaptionStyle, "size_percent" | "horizontal" | "vertical" | "color" | "outline_color" | "outline_percent">>;
type Field = keyof AppearancePatch;
type Values = Record<Field, number | string | null>;
type Suggestion = { schema_version: 1; method: string; selection: Selection; selection_revision: number; base_style: CaptionStyle; values: Values; outline_status: "measured" | "none_detected" | "not_estimated"; notes: string[]; fitting_attempts: number };
type Saved = { revision: number; status: "empty" | "ready" | "stale"; suggestion: Suggestion | null; token: string | null; current_selection: {revision: number; token: string; font: FontBinding} | null; message: string | null; reference?: Picture; reconstruction?: Picture };
const apiBase = (process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
const labels: Record<Field,string> = {size_percent: "Text size (% of canvas height)", horizontal: "Horizontal anchor (%)", vertical: "Vertical anchor (%)", color: "Text fill color", outline_color: "Outline color", outline_percent: "Outline width (% of canvas height)"};
const fields = Object.keys(labels) as Field[];
const finite = (v: unknown,min:number,max:number) => typeof v === "number" && Number.isFinite(v) && v>=min && v<=max;
const hash = (v: unknown) => typeof v === "string" && /^[0-9a-f]{64}$/.test(v);
const picture = (v: Picture) => v && [v.width,v.height].every(n=>Number.isInteger(n)&&n>=2&&n<=960) && typeof v.png_base64==="string" && v.png_base64.length<=4194304 && /^iVBORw0KGgo[A-Za-z0-9+/]*={0,2}$/.test(v.png_base64);
export const selectionKey = (s: Selection | null, revision: number) => s?.reviewed_font ? JSON.stringify([revision,s.source.media_sha256,s.reference_operation_id,s.requested_timestamp_seconds,s.rectangle,s.text,s.polarity,s.reviewed_font.sha256]) : null;
const manualKey = (s: CaptionStyle) => JSON.stringify([s.font,s.bold,s.italic,s.alignment,s.placement,s.shadow_color,s.shadow_percent]);
function parse(data: unknown): Saved {
 const r=data as Saved,s=r?.suggestion,v=s?.values;
 if(!r || !Number.isSafeInteger(r.revision) || r.revision<0 || !["empty","ready","stale"].includes(r.status) || (r.token!==null&&!hash(r.token)) || (r.current_selection&&(!hash(r.current_selection.token)||!validFont(r.current_selection.font)))) throw new Error("Invalid appearance response.");
 if(s) {
  if(s.schema_version!==1 || !s.selection?.reviewed_font || !validFont(s.selection.reviewed_font) || !Number.isSafeInteger(s.selection_revision) || !v || !Array.isArray(s.notes)||s.notes.length>12||s.notes.some(n=>typeof n!=="string"||n.length>2000)|| !["measured","none_detected","not_estimated"].includes(s.outline_status)|| !Number.isInteger(s.fitting_attempts)||s.fitting_attempts<1||s.fitting_attempts>4) throw new Error("Invalid appearance suggestion.");
  for(const f of fields) if(v[f]!==null && (["color","outline_color"].includes(f) ? typeof v[f]!=="string" || !/^#[0-9a-f]{6}$/i.test(v[f] as string) : !finite(v[f],f==="size_percent"?2:0,f==="size_percent"?15:f==="outline_percent"?2:1))) throw new Error("Invalid suggested value.");
  s.base_style=readStyle(s.base_style);
 }
 if((r.reference&&!picture(r.reference))||(r.reconstruction&&!picture(r.reconstruction))) throw new Error("Invalid reconstruction image.");
 return r;
}

export function AppearanceSuggestions({projectId,currentKey,baseStyle,disabled,onApply,onBusy}:{projectId:string;currentKey:string|null;baseStyle:CaptionStyle;disabled:boolean;onApply:(patch:AppearancePatch)=>void;onBusy:(busy:boolean)=>void}) {
 const [saved,setSaved]=useState<Saved|null>(null),[images,setImages]=useState<Saved|null>(null),[selected,setSelected]=useState<Field[]>([]),[busy,setBusy]=useState(false),[error,setError]=useState("");
 const action=useRef<AbortController|null>(null),generation=useRef(0);
 const styleKey=manualKey(baseStyle), contextKey=`${projectId}:${currentKey}:${styleKey}`;
 const current=!!saved?.suggestion&&saved.status==="ready"&&selectionKey(saved.suggestion.selection,saved.suggestion.selection_revision)===currentKey&&manualKey(saved.suggestion.base_style)===styleKey;
 const picturesCurrent=current&&images?.token===saved?.token;
 const prerequisites=!!currentKey&&!!saved?.current_selection&&saved.current_selection.font.sha256===JSON.parse(currentKey)[7]&&fontChoice(saved.current_selection.font)===baseStyle.font;
 useEffect(()=>{onBusy(busy);return()=>onBusy(false);},[busy,onBusy]);
 useEffect(()=>{function cancel(){generation.current++;action.current?.abort();action.current=null;}cancel();return cancel;},[contextKey]);
 useEffect(()=>{
  const c=new AbortController(),timer=setTimeout(()=>c.abort(),15000);
  void fetch(`${apiBase}/api/projects/${encodeURIComponent(projectId)}/caption-appearance`,{signal:c.signal}).then(async response=>{const data=await response.json();if(!response.ok)throw new Error(data?.error?.message||"Appearance suggestions could not be loaded.");const result=parse(data);if(!c.signal.aborted){setSaved(result);setSelected(result.suggestion?fields.filter(f=>result.suggestion!.values[f]!==null):[]);}}).catch(e=>{if(!c.signal.aborted)setError(e instanceof Error?e.message:"Load failed.");}).finally(()=>clearTimeout(timer));
  return()=>{clearTimeout(timer);c.abort();};
 },[projectId,currentKey]);
 async function run(kind:"suggest"|"apply"|"reload") {
  if(action.current||disabled||(kind==="suggest"&&!prerequisites)||(kind==="apply"&&(!current||!selected.length)))return;
  const c=new AbortController(),version=++generation.current;action.current=c;setBusy(true);setError("");const timer=setTimeout(()=>c.abort(),45000);
  try {
   const body=kind==="reload"?null:kind==="apply"?{expected_revision:saved!.revision,token:saved!.token,fields:selected,base_style:baseStyle}:{expected_revision:saved!.revision,expected_selection_revision:saved!.current_selection!.revision,expected_selection_token:saved!.current_selection!.token,base_style:baseStyle};
   const response=await fetch(`${apiBase}/api/projects/${encodeURIComponent(projectId)}/caption-appearance${kind==="apply"?"/apply":""}`,{method:kind==="reload"?"GET":"POST",signal:c.signal,...(kind!=="reload"?{headers:{"Content-Type":"application/json"},body:JSON.stringify(body)}:{})});
   const data=await response.json();if(!response.ok){if(response.status===409)setSaved(v=>v?{...v,status:"stale"}:v);throw new Error(data?.error?.message||"Appearance request failed. Previous successful comparison remains.");}
   if(version!==generation.current||c.signal.aborted)return;
   if(kind==="apply") {
    if(!data.patch||data.font_hash!==saved!.suggestion!.selection.reviewed_font!.sha256||Object.keys(data.patch).some(f=>!selected.includes(f as Field)||data.patch[f]!==saved!.suggestion!.values[f as Field]))throw new Error("Invalid appearance application.");
    onApply(data.patch);
   } else {
    const result=parse(data);if(kind==="suggest"&&(result.revision!==saved!.revision+1||!result.reference||!result.reconstruction||!result.suggestion||selectionKey(result.suggestion.selection,result.suggestion.selection_revision)!==currentKey||manualKey(result.suggestion.base_style)!==styleKey))throw new Error("Suggestion does not match this selection/draft.");
    setSaved(result);if(kind==="suggest")setImages(result);setSelected(result.suggestion?fields.filter(f=>result.suggestion!.values[f]!==null):[]);
   }
  } catch(e){if(version===generation.current)setError(e instanceof Error&&e.name!=="AbortError"?e.message:"Appearance request timed out. Reload explicitly; previous comparison remains.");}
  finally{clearTimeout(timer);if(action.current===c)action.current=null;setBusy(false);}
 }
 const suggestion=saved?.suggestion;
 return <section className="caption-cue" aria-label="Reference-caption appearance suggestions"><h4>Suggest caption appearance</h4>
  <p className="hint">Uses the saved reference region, confirmed text and reviewed font. No OCR or animation matching. Suggestions never change your captions automatically.</p>
  {!currentKey&&<p>Confirm the current crop/text and choose a font in Find similar fonts above. Compare again after changing that selection.</p>}
  {currentKey && saved?.current_selection && fontChoice(saved.current_selection.font) !== baseStyle.font && <p>Choose the reviewed face in Caption font before suggesting. Font choice is manually confirmed.</p>}
  {saved?.message&&<p role="note">{saved.message}</p>}
  <button disabled={busy||disabled||!prerequisites} onClick={()=>void run("suggest")}>{suggestion?"Update appearance suggestion":"Suggest appearance"}</button>
  <button disabled={busy||disabled} onClick={()=>void run("reload")}>Reload saved appearance suggestion</button>
  {busy&&<p role="status">Fitting actual rendered glyphs… At most four fitting attempts and 30 seconds. Previous comparison remains visible.</p>}{error&&<p role="alert">{error}</p>}
  {suggestion&&<><p role="status">{current?"Measured appearance suggestions":"Outdated appearance suggestions — suggest again before applying"} · Revision {saved!.revision}</p>
   <p>Outline: {suggestion.outline_status==="measured"?"Measured suggestion":suggestion.outline_status==="none_detected"?"No outline detected":"Outline could not be estimated"}.</p>
   <fieldset disabled={busy||disabled||!current}><legend>Choose properties to apply</legend>{fields.map(f=>{const value=suggestion.values[f];return <label key={f}><input type="checkbox" aria-label={`Apply ${labels[f]}`} disabled={value===null} checked={selected.includes(f)&&value!==null} onChange={e=>setSelected(v=>e.target.checked?[...v,f]:v.filter(k=>k!==f))}/>{labels[f]}: {value===null?"Not estimated":typeof value==="number"?["horizontal","vertical"].includes(f)?`${(value*100).toFixed(2)}%`:`${value.toFixed(3)}%`:value}</label>;})}
    <button disabled={!selected.length} onClick={()=>void run("apply")}>Apply appearance to draft</button>
   </fieldset><p className="hint">Applying changes only checked properties in your draft. Save captions, then use the existing caption preview to inspect your own words on your footage before exporting.</p>{suggestion.notes.map(n=><p role="note" key={n}>{n}</p>)}</>}
  {images?.reference&&images.reconstruction&&<><p role="status">{picturesCurrent?"Reconstruction comparison":"Outdated reconstruction comparison"} · Confirmed reference text: <span className="caption-text" dir="auto">{images.suggestion?.selection.text}</span></p><div className="frame-preview-images"><figure><figcaption>Full normalized reference canvas · Selected caption</figcaption><Image unoptimized src={`data:image/png;base64,${images.reference.png_base64}`} width={images.reference.width} height={images.reference.height} alt="Reference caption on the full normalized canvas"/></figure><figure><figcaption>Real subtitle reconstruction · Neutral gray background is a comparison aid</figcaption><Image unoptimized src={`data:image/png;base64,${images.reconstruction.png_base64}`} width={images.reconstruction.width} height={images.reconstruction.height} alt="Actual proposed caption reconstruction on a neutral gray full canvas"/></figure></div></>}
  {suggestion&&!images&&<p>Saved values and bindings restored. Update explicitly to recreate transient comparison images.</p>}
 </section>;
}
