"use client";

import { useEffect, useRef, useState } from "react";
import type { MatchFrame, Rectangle } from "./font-matching";

const apiBase = (process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
const method = "tesseract-fast-block-v1";
type Language = "english" | "arabic" | "combined";
type Capability = { available: boolean; method: string; token: string | null; engine_version: string | null; assets_commit: string; message: string };
type Proposal = { schema_version: 1; project_id: string; source: { media_sha256: string }; reference_operation_id: string; requested_timestamp_seconds: number; timestamp_seconds: number; rectangle: Rectangle; language: Language; polarity: "light" | "dark"; method: string; capability_token: string; engine_version: string; assets_commit: string; text: string; token: string; processing_width: number; processing_height: number; preprocessing_attempts: 1 };
const hash = (v: unknown) => typeof v === "string" && /^[0-9a-f]{64}$/.test(v);
const plain = (text: string) => Array.from(text).length <= 80 && text.split("\n").length <= 2 && (text.match(/[\p{L}\p{N}]/gu)?.length ?? 0) >= 4 && !/[\x00-\x09\x0b-\x1f\x7f]/.test(text);
function readCapability(value: unknown): Capability {
 const c = value as Capability;
 if (!c || typeof c.available !== "boolean" || c.method !== method || !/^[0-9a-f]{40}$/.test(c.assets_commit) || typeof c.message !== "string" || c.message.length > 2000 || (c.available && (!hash(c.token) || typeof c.engine_version !== "string" || c.engine_version.length > 256))) throw new Error("Invalid OCR capability response. Manual entry remains available.");
 return c;
}
export function ReferenceOcr({projectId, frame, rectangle, polarity, disabled, onApply, onBusy}: {projectId: string; frame: MatchFrame | null; rectangle: Rectangle; polarity: "light" | "dark"; disabled: boolean; onApply: (text: string) => void; onBusy: (busy: boolean) => void}) {
 const [capability, setCapability] = useState<Capability | null>(null), [language, setLanguage] = useState<Language>("english");
 const [proposal, setProposal] = useState<Proposal | null>(null), [edited, setEdited] = useState("");
 const [busy, setBusy] = useState(false), [error, setError] = useState("");
 const action = useRef<AbortController | null>(null), generation = useRef(0);
 const context = JSON.stringify([projectId,frame?.reference_operation_id,frame?.source.media_sha256,frame?.requested_timestamp_seconds,rectangle,language,polarity,capability?.token]);
 const current = !!proposal && !!frame && proposal.project_id === projectId && proposal.reference_operation_id === frame.reference_operation_id && proposal.source.media_sha256 === frame.source.media_sha256 && proposal.requested_timestamp_seconds === frame.requested_timestamp_seconds && JSON.stringify(proposal.rectangle) === JSON.stringify(rectangle) && proposal.language === language && proposal.polarity === polarity && proposal.capability_token === capability?.token;
 const validRegion = rectangle.width > 0 && rectangle.height > 0 && rectangle.x >= 0 && rectangle.y >= 0 && rectangle.x + rectangle.width <= 1.000001 && rectangle.y + rectangle.height <= 1.000001;
 useEffect(() => {onBusy(busy); return () => onBusy(false);},[busy,onBusy]);
 useEffect(() => {function cancel(){generation.current++; action.current?.abort();} cancel(); return cancel;},[context]);
 useEffect(() => {
  const c = new AbortController(), timer = setTimeout(() => c.abort(),10000);
  void fetch(`${apiBase}/api/projects/${encodeURIComponent(projectId)}/caption-ocr`,{signal:c.signal}).then(async response => {const data = await response.json(); if(!response.ok)throw new Error(data?.error?.message || "OCR status could not be loaded. Manual entry remains available.");if(!c.signal.aborted)setCapability(readCapability(data));}).catch(e => {if(!c.signal.aborted)setError(e instanceof Error?e.message:"OCR status failed.");}).finally(()=>clearTimeout(timer));
  return () => {clearTimeout(timer);c.abort();};
 },[projectId]);
 async function run(kind: "extract" | "apply" | "status") {
  if(action.current || disabled || (kind!=="status" && (!frame || !capability?.available || !validRegion)) || (kind==="apply" && (!current || !plain(edited))))return;
  const c = new AbortController(), version = ++generation.current; action.current=c;setBusy(true);setError("");
  const timer=setTimeout(()=>c.abort(),45000);
  const body=kind==="status"?null:{expected_reference_operation_id:frame!.reference_operation_id,expected_source_hash:frame!.source.media_sha256,timestamp_seconds:frame!.requested_timestamp_seconds,expected_capability_token:capability!.token,rectangle,language,polarity,...(kind==="apply"?{proposal,text:edited}:{})};
  try {
   const response=await fetch(`${apiBase}/api/projects/${encodeURIComponent(projectId)}/caption-ocr${kind==="apply"?"/apply":""}`,{method:kind==="status"?"GET":"POST",signal:c.signal,...(body?{headers:{"Content-Type":"application/json"},body:JSON.stringify(body)}:{})});
   const data=await response.json();if(!response.ok){throw new Error(data?.error?.message||"Extraction failed. Manual text and previous proposal remain; retry explicitly.");}
   if(version!==generation.current||c.signal.aborted)return;
   if(kind==="status")setCapability(readCapability(data));
   else if(kind==="apply"){if(data.text!==edited)throw new Error("Invalid OCR application.");onApply(edited);setProposal(null);setEdited("");}
   else {
    const p=data as Proposal;
    if(p.schema_version!==1||p.project_id!==projectId||p.reference_operation_id!==frame!.reference_operation_id||p.source?.media_sha256!==frame!.source.media_sha256||p.requested_timestamp_seconds!==frame!.requested_timestamp_seconds||!Number.isFinite(p.timestamp_seconds)||p.timestamp_seconds<0||p.timestamp_seconds>120.1||JSON.stringify(p.rectangle)!==JSON.stringify(rectangle)||p.language!==language||p.polarity!==polarity||p.method!==method||p.capability_token!==capability!.token||p.engine_version!==capability!.engine_version||p.assets_commit!==capability!.assets_commit||!hash(p.token)||typeof p.text!=="string"||!p.text.trim()||Array.from(p.text).length>1024||p.preprocessing_attempts!==1||![p.processing_width,p.processing_height].every(n=>Number.isInteger(n)&&n>=8&&n<=960))throw new Error("Invalid OCR proposal. Your matching text is unchanged.");
    setProposal(p);setEdited(p.text);
   }
  }catch(e){if(version===generation.current)setError(e instanceof Error&&e.name!=="AbortError"?e.message:"OCR timed out. Retry explicitly; manual text is retained.");}
  finally{clearTimeout(timer);if(action.current===c){action.current=null;setBusy(false);}}
 }
 return <section className="caption-cue" aria-label="Reference text extraction"><h5>Extract reference text locally</h5>
  <p className="hint">Optional OCR for this selected frame/region only. Review words and line order. Applying updates the reference matching field, never your video captions, style or timing.</p>
  {capability&&<p role="status">{capability.message}</p>}
  <label>OCR language<select value={language} onChange={e=>setLanguage(e.target.value as Language)} disabled={busy||disabled}><option value="english">English</option><option value="arabic">Arabic</option><option value="combined">English + Arabic</option></select></label>
  <button disabled={busy||disabled||!frame||!capability?.available||!validRegion} onClick={()=>void run("extract")}>{proposal?"Retry extraction":"Extract text from this region"}</button>
  <button disabled={busy||disabled} onClick={()=>void run("status")}>Reload OCR status</button>
  {!frame&&<p>Inspect the reference frame and select its caption region first. Manual entry remains available.</p>}
  {busy&&<p role="status">Extracting or validating local text… One attempt, up to 30 seconds overall. Previous matching text is unchanged.</p>}{error&&<p role="alert">{error}</p>}
  {proposal&&<><p role="status">{current?"OCR proposal — review before applying":"Outdated OCR proposal — extract the current region/language before applying"}</p>
   <label>Review extracted reference text<textarea dir="auto" rows={3} value={edited} disabled={busy||disabled} aria-invalid={!plain(edited)} aria-describedby={`ocr-review-limits-${projectId}`} onChange={e=>setEdited(e.target.value)}/></label>
   <p id={`ocr-review-limits-${projectId}`}>{Array.from(edited).length}/80 characters · At most two lines and four letters/numbers minimum for font matching. Correct an overlong result; it is not truncated.</p>
   <p className="hint">{proposal.engine_version} · {proposal.method} · No calibrated confidence. English/Arabic punctuation and reading order need review.</p>
   <button disabled={busy||disabled||!current||!plain(edited)} onClick={()=>void run("apply")}>Apply text to matching field</button><button disabled={busy||disabled} onClick={()=>{setProposal(null);setEdited("");setError("");}}>Discard OCR proposal</button>
  </>}
 </section>;
}
