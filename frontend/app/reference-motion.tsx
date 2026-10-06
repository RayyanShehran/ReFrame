"use client";
import { useEffect, useRef, useState } from "react";
import { readAnimation, defaultAnimation, type Animation } from "./caption-animation";
import { selectionKey } from "./caption-appearance-suggestions";
import { RegionSelection, type Selection, type Rectangle, type MatchFrame } from "./font-matching";
import { readStyle, validFont, fontChoice, type CaptionStyle } from "./caption-style-controls";
import { useWorkspaceReport } from "./guided-workspace";
type Patch=Partial<Omit<Animation,"schema_version">>;
type Inputs={expected_revision:number;expected_selection_revision:number;expected_selection_token:string;start:number;end:number;region:Rectangle;base_style:CaptionStyle};
type Suggestion={schema_version:1;method:string;request:Inputs;selection:Selection;fitting_style:CaptionStyle;outcome:"static"|"supported"|"partial"|"inconclusive";values:Record<keyof Patch,number|string|null>;notes:string[];cue_start:number|null;cue_end:number|null};
type Saved={revision:number;status:"empty"|"ready"|"stale";suggestion:Suggestion|null;token:string|null;message:string|null;current_selection:{revision:number;token:string;selection:Selection}|null};
type Clip={width:number;height:number;duration_seconds:number;decoded_frames:number;size_bytes:number;video_base64:string;silent:true};
type Videos={reference:Clip;reconstruction?:Clip;revision?:number;token?:string};
const apiBase=(process.env.NEXT_PUBLIC_API_BASE_URL||"http://127.0.0.1:8000").replace(/\/$/,"");
const finite=(n:unknown,min:number,max:number)=>typeof n==="number"&&Number.isFinite(n)&&n>=min&&n<=max;
const hash=(s:unknown)=>typeof s==="string"&&/^[0-9a-f]{64}$/.test(s);
const rectangle=(r:Rectangle)=>r&&[r.x,r.y,r.width,r.height].every(n=>finite(n,0,1))&&r.width>0&&r.height>0&&r.x+r.width<=1.000001&&r.y+r.height<=1.000001;
const inputsKey=(r:Pick<Inputs,"start"|"end"|"region"|"base_style">)=>JSON.stringify([r.start,r.end,[r.region.x,r.region.y,r.region.width,r.region.height],readStyle(r.base_style)]);
const patchOf=(s:Suggestion)=>Object.fromEntries(Object.entries(s.values).filter(([,v])=>v!==null)) as Patch;
function parse(value:unknown):Saved{
 const r=value as Saved,s=r?.suggestion,c=r?.current_selection;
 if(!r||!Number.isSafeInteger(r.revision)||r.revision<0||!["empty","ready","stale"].includes(r.status)||(r.token!==null&&!hash(r.token))||(c&&(!hash(c.token)||!Number.isSafeInteger(c.revision)||c.revision<1||!c.selection?.reviewed_font||!validFont(c.selection.reviewed_font))))throw new Error("Invalid saved motion response.");
 if(s){if(s.schema_version!==1||s.method!=="temporal-glyph-fit-v1"||!["static","supported","partial","inconclusive"].includes(s.outcome)||!s.request||!rectangle(s.request.region)||!finite(s.request.start,0,120)||!finite(s.request.end,0,120)||s.request.end-s.request.start<.4||s.request.end-s.request.start>4||!s.selection?.reviewed_font||!validFont(s.selection.reviewed_font)||!Array.isArray(s.notes)||s.notes.length>16||s.notes.some(v=>typeof v!=="string"||v.length>2000)||!s.values||Object.keys(s.values).some(k=>!["mode","entrance_seconds","exit_seconds","initial_scale","displacement"].includes(k)))throw new Error("Invalid motion suggestion.");readStyle(s.fitting_style);readAnimation({...defaultAnimation,...patchOf(s)});}
 return r;
}
function clip(v:Clip){if(!v||!v.silent||![v.width,v.height].every(n=>Number.isInteger(n)&&n>=2&&n<=640)||!finite(v.duration_seconds,.001,4)||!Number.isInteger(v.decoded_frames)||v.decoded_frames<1||v.decoded_frames>120||Math.abs(v.duration_seconds-v.decoded_frames/30)>.05||!Number.isInteger(v.size_bytes)||v.size_bytes<1||v.size_bytes>3145728||typeof v.video_base64!=="string"||v.video_base64.length>4194304||!/^AAAA[A-Za-z0-9+/]+={0,2}$/.test(v.video_base64))throw new Error("Invalid motion comparison video.");}
export function ReferenceMotion({projectId,currentKey,baseStyle,frame,disabled,onBusy,onApply,duration}:{projectId:string;currentKey:string|null;baseStyle:CaptionStyle;frame:MatchFrame|null;disabled:boolean;onBusy?:(v:boolean)=>void;onApply:(patch:Patch)=>void;duration?:number|null}){
 const [saved,setSaved]=useState<Saved|null>(null),[start,setStart]=useState("0"),[end,setEnd]=useState("2"),[region,setRegion]=useState<Rectangle>({x:.1,y:.3,width:.8,height:.4}),[busy,setBusy]=useState(false),[error,setError]=useState(""),[reviewed,setReviewed]=useState(""),[videos,setVideos]=useState<{value:Videos;key:string;token:string|null}|null>(null);
 const action=useRef<AbortController|null>(null),generation=useRef(0),restored=useRef(false);
 const input={start:Number(start),end:Number(end),region,base_style:baseStyle};const inputKey=inputsKey(input),binding=JSON.stringify([projectId,currentKey,inputKey]);
 const selection=saved?.current_selection,prerequisite=!!selection&&!!currentKey&&selectionKey(selection.selection,selection.revision)===currentKey&&fontChoice(selection.selection.reviewed_font!)===baseStyle.font;
 const valid=start.trim()!==""&&end.trim()!==""&&finite(input.start,0,120)&&finite(input.end,0,duration??120)&&input.end-input.start>=.4&&input.end-input.start<=4&&rectangle(region);
 const suggestion=saved?.suggestion,current=!!suggestion&&saved?.status==="ready"&&prerequisite&&inputsKey(suggestion.request)===inputKey;
 const supported=current&&suggestion.outcome!=="inconclusive"&&suggestion.values.mode!==null;
 const videoCurrent=!!videos&&videos.key===binding&&(videos.token===null||videos.token===saved?.token);
 useEffect(()=>{onBusy?.(busy);return()=>onBusy?.(false);},[busy,onBusy]);
 useEffect(()=>{generation.current++;action.current?.abort();},[binding]);
 useEffect(()=>{
  const c=new AbortController(),timer=setTimeout(()=>c.abort(),15000);
  void fetch(`${apiBase}/api/projects/${encodeURIComponent(projectId)}/reference-motion`,{signal:c.signal}).then(async response=>{const data=await response.json();if(!response.ok)throw new Error(data?.error?.message||"Motion settings could not load.");const r=parse(data);if(c.signal.aborted)return;setSaved(r);if(!restored.current){restored.current=true;if(r.suggestion){setStart(String(r.suggestion.request.start));setEnd(String(r.suggestion.request.end));setRegion(r.suggestion.request.region);}else if(r.current_selection)setRegion(r.current_selection.selection.rectangle);}}).catch(e=>{if(!c.signal.aborted)setError(e instanceof Error?e.message:"Load failed.");}).finally(()=>clearTimeout(timer));
  return()=>{c.abort();clearTimeout(timer);};
 },[projectId,currentKey]);
 useWorkspaceReport("reference-motion","audio",busy?"Working":error?"Needs attention":"Ready","Comparing reference caption motion…");
 async function run(kind:"analyze"|"preview"|"compare"|"apply"|"reload"){
  if(action.current||disabled||(!prerequisite&&kind!=="reload")||(!valid&&kind!=="reload"))return;
  const c=new AbortController(),version=++generation.current;action.current=c;setBusy(true);setError("");const timer=setTimeout(()=>c.abort(),45000);
  try{
   const body={...input,expected_revision:saved?.revision??0,expected_selection_revision:selection?.revision,expected_selection_token:selection?.token,...(["compare","apply"].includes(kind)?{token:saved?.token}:{})};
   const response=await fetch(`${apiBase}/api/projects/${encodeURIComponent(projectId)}/reference-motion${kind==="analyze"||kind==="reload"?"":"/"+kind}`,{method:kind==="reload"?"GET":"POST",signal:c.signal,...(kind!=="reload"?{headers:{"Content-Type":"application/json"},body:JSON.stringify(body)}:{})});const data=await response.json();if(!response.ok)throw new Error(data?.error?.message||"Motion request failed. Previous results remain; retry explicitly.");if(version!==generation.current||c.signal.aborted)return;
   if(kind==="apply"){
    const expected=patchOf(suggestion!);if(data.font_hash!==suggestion!.selection.reviewed_font!.sha256||!data.patch||Object.keys(data.patch).length!==Object.keys(expected).length||Object.entries(data.patch).some(([k,v])=>expected[k as keyof Patch]!==v))throw new Error("Invalid animation application.");readAnimation({...defaultAnimation,...data.patch});onApply(data.patch);
   }else if(kind==="compare"||kind==="preview"){
    clip(data.reference);if(kind==="compare"){clip(data.reconstruction);if(data.revision!==saved?.revision||data.token!==saved?.token)throw new Error("Comparison changed; reload.");}else if(inputsKey(data.request)!==inputKey||selectionKey(data.selection,selection!.revision)!==currentKey)throw new Error("Reference interval changed.");
    setVideos({value:data,key:binding,token:kind==="compare"?saved!.token:null});setReviewed("");
   }else{
    const r=parse(data);if(kind==="analyze"&&(!r.suggestion||r.revision!==(saved?.revision??0)+1||inputsKey(r.suggestion.request)!==inputKey||selectionKey(r.suggestion.selection,r.suggestion.request.expected_selection_revision)!==currentKey))throw new Error("Suggestion binding changed; reload.");setSaved(r);setReviewed("");
   }
  }catch(e){if(version===generation.current)setError(e instanceof Error&&e.name!=="AbortError"?e.message:"Motion processing timed out; previous results remain. Retry explicitly.");}finally{clearTimeout(timer);if(action.current===c){action.current=null;setBusy(false);}}
 }
 return <section className="caption-cue" aria-label="Assisted reference-caption motion"><h4>Compare reference caption motion</h4><p className="hint">Select a short interval with an entrance, settled text and exit when possible. This estimates supported motion from your confirmed text and reviewed font; it can be unsupported or inconclusive.</p>
 {!prerequisite&&<p>Inspect a reference frame, confirm text and review a face in Find similar fonts above. Choose that face in Caption font. Manual animation controls remain available.</p>}{saved?.message&&<p role="note">{saved.message}</p>}
 <fieldset disabled={disabled||busy}><legend>Reference interval and separate motion region</legend><label>Interval start (seconds)<input type="number" min="0" max={duration??120} step=".1" value={start} onChange={e=>setStart(e.target.value)}/></label><label>Interval end (seconds)<input type="number" min="0" max={duration??120} step=".1" value={end} onChange={e=>setEnd(e.target.value)}/></label><p>0.4–4 seconds inside the reference{duration!==null&&duration!==undefined?` (${duration.toFixed(3)} seconds)`:""}. Expand this region for moving letters; the saved static crop is unchanged.</p>
 {(["x","y","width","height"] as const).map(field=><label key={field}>Motion region {field} (%)<input type="number" min="0" max="100" step=".1" value={Number((region[field]*100).toFixed(3))} onChange={e=>setRegion(r=>({...r,[field]:Number(e.target.value)/100}))}/></label>)}<button type="button" disabled={!selection} onClick={()=>selection&&setRegion(selection.selection.rectangle)}>Use saved static region</button>
 {frame&&rectangle(region)&&<RegionSelection frame={frame} rectangle={region} onChange={setRegion} disabled={disabled||busy}/>}
 <button disabled={!valid||!prerequisite} onClick={()=>void run("preview")}>Preview reference interval</button><button disabled={!valid||!prerequisite} onClick={()=>void run("analyze")}>{suggestion?"Update caption motion analysis":"Analyze caption motion"}</button></fieldset><button disabled={disabled||busy} onClick={()=>void run("reload")}>Reload saved motion suggestion</button>
 {!valid&&<p role="alert">Select a finite 0.4–4 second interval and keep the motion region inside the frame.</p>}{busy&&<p role="status">Processing at most 40 sampled frames with a 30-second deadline. Previous results remain available.</p>}{error&&<p role="alert">{error}</p>}
 {suggestion&&<><p role="status">{current?"Current motion suggestion":"Outdated motion suggestion — analyze again before applying"} · {suggestion.outcome==="inconclusive"?"Unsupported or inconclusive":suggestion.outcome==="static"?"No animation observed (stable text)":suggestion.outcome==="partial"?"Partial supported suggestion":"Supported animation suggestion"}</p><dl>{Object.entries(suggestion.values).map(([k,v])=><div key={k}><dt>{({mode:"Animation",entrance_seconds:"Entrance (seconds)",exit_seconds:"Exit (seconds)",initial_scale:"Starting scale",displacement:"Slide / canvas height"} as Record<string,string>)[k]}</dt><dd>{v??"Not estimated — keep unchanged"}</dd></div>)}</dl><ul>{suggestion.notes.map((note,i)=><li key={i}>{note}</li>)}</ul><button disabled={!supported||busy||disabled} onClick={()=>void run("compare")}>Compare actual motion reconstruction</button></>}
 {videos&&<><p role="status">{videoCurrent?"Current silent comparison":"Outdated comparison videos"} · Explicit playback only</p><div className="frame-comparison">{(["reference","reconstruction"] as const).map(kind=>{const v=videos.value[kind];return v&&<figure key={kind}><figcaption>{kind==="reference"?"Selected reference interval · silent":"Actual subtitle reconstruction · neutral background · silent"}</figcaption><video className="rendered-video" aria-label={kind==="reference"?"Selected reference interval":"Reference motion reconstruction"} controls playsInline preload="metadata" width={v.width} height={v.height} src={`data:video/mp4;base64,${v.video_base64}`}/></figure>;})}</div></>}
 {supported&&<><label><input type="checkbox" checked={reviewed===binding} disabled={!videoCurrent||!videos?.value.reconstruction||busy||disabled} onChange={e=>setReviewed(e.target.checked?binding:"")}/>I reviewed the reference and reconstruction</label><button disabled={reviewed!==binding||!videoCurrent||!videos?.value.reconstruction||busy||disabled} onClick={()=>void run("apply")}>Apply animation to draft</button><p className="hint">Only estimated animation fields change; missing settings stay unchanged. Words, times, font, appearance and provenance are preserved. Save captions, then use Preview caption motion on your footage. Your shorter cues may shorten effects or stay static under three frames.</p></>}
 </section>;
}
