"use client";
import { emptySequence, type SequenceState } from "./sequence-editor";
import { useEffect, useRef, useState } from "react";
import type { AppearanceContext, PreviewTrack } from "./caption-appearance-preview";
import { validFont, type FontBinding } from "./caption-style-controls";
import type { PlanState } from "./edit-plan";
import { useWorkspaceReport } from "./guided-workspace";
export type Animation = {schema_version:1;mode:"none"|"fade"|"pop"|"slide-up";entrance_seconds:number;exit_seconds:number;initial_scale:number;displacement:number};
export const defaultAnimation:Animation={schema_version:1,mode:"none",entrance_seconds:.25,exit_seconds:.25,initial_scale:.7,displacement:.08};
const bounded=(v:unknown,min:number,max:number)=>typeof v==="number"&&Number.isFinite(v)&&v>=min&&v<=max;
export function readAnimation(value:unknown):Animation{
 if(value!==undefined&&(!value||typeof value!=="object"||Array.isArray(value)))throw new Error("Invalid caption animation.");
 const a={...defaultAnimation,...(value as Partial<Animation>)};
 if(a.schema_version!==1||!["none","fade","pop","slide-up"].includes(a.mode)||!bounded(a.entrance_seconds,0,1)||!bounded(a.exit_seconds,0,1)||!bounded(a.initial_scale,.5,1)||!bounded(a.displacement,0,.25))throw new Error("Invalid caption animation.");return a;
}
export function AnimationControls({value,onChange}:{value:Animation;onChange:(a:Animation)=>void}){
 function control(label:string,key:"entrance_seconds"|"exit_seconds"|"initial_scale"|"displacement",min:number,max:number,step:number,multiplier=1,suffix="s"){
  return <label>{label} · {(value[key]*multiplier).toFixed(multiplier===100?0:2)}{suffix}<input aria-label={label} type="range" min={min} max={max} step={step} value={value[key]} onChange={e=>onChange({...value,[key]:Number(e.target.value)})}/></label>;
 }
 return <fieldset className="caption-cue"><legend>Caption animation</legend>
 <label>Animation<select value={value.mode} onChange={e=>onChange({...value,mode:e.target.value as Animation["mode"]})}><option value="none">None</option><option value="fade">Fade</option><option value="pop">Pop</option><option value="slide-up">Slide up</option></select></label>
 <p className="hint">Editable animation. Choose manually or review an assisted reference suggestion. Save captions, then preview actual motion.</p>
 {value.mode!=="none"&&control("Entrance duration (seconds)","entrance_seconds",0,1,.05)}
 {value.mode==="fade"&&control("Exit duration (seconds)","exit_seconds",0,1,.05)}
 {value.mode==="pop"&&control("Starting size (% of saved size)","initial_scale",.5,1,.05,100,"%")}
 {value.mode==="slide-up"&&control("Slide distance (% of canvas height)","displacement",0,.25,.01,100,"%")}
 {value.mode!=="none"&&<p className="hint">Each effect is capped at half the visible cue interval. Pop/Slide settle during the first half; Fade exits inside the cue. Cues shorter than three 30 fps frames stay static. Zero entrance keeps the saved geometry.</p>}
 </fieldset>;
}
const apiBase=(process.env.NEXT_PUBLIC_API_BASE_URL||"http://127.0.0.1:8000").replace(/\/$/,"");
type Motion={schema_version:1;source:{project_id:string;media_sha256:string};spec:{recipe_revision:number;framing:{revision:number};footage:{source:{media_sha256:string}};captions:{revision:number;font_binding:FontBinding;animation?:Animation;timeline:{mode:string;plan_revision:number|null;sequence_revision?:number|null}}};cue_index:number;start_frame:number;end_frame:number;width:number;height:number;duration_seconds:number;decoded_frames:number;size_bytes:number;sha256:string;video_base64:string;silent:true};
export function MotionPreview({projectId,track,cue,context,plan,sequence=emptySequence,ready,dirty,disabled,onBusy}:{projectId:string;track:PreviewTrack;cue:number;context:AppearanceContext;plan:PlanState;sequence?:SequenceState;ready:boolean;dirty:boolean;disabled:boolean;onBusy?:(v:boolean)=>void}){
 const [saved,setSaved]=useState<{data:Motion;key:string}|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState("");const action=useRef<AbortController|null>(null),generation=useRef(0);
 const key=JSON.stringify([projectId,track.revision,track.font_binding?.sha256,track.timeline,context.recipeRevision,context.framing.revision,ready,context.recipeReady,cue]);
 const unsaved=dirty||context.recipeDirty||context.framing.dirty||(track.timeline?.mode==="cuts"&&plan.dirty)||(track.timeline?.mode==="sequence"&&sequence.dirty);
 const canRun=ready&&track.enabled&&!!track.cues[cue]&&context.recipeReady&&context.recipeRevision!==null&&context.framing.ready&&context.framing.revision!==null&&!context.recipeBusy&&!context.framing.busy&&!plan.busy&&!(track.timeline?.mode==="sequence"&&sequence.busy)&&!disabled&&!unsaved;
 const outdated=!!saved&&(saved.key!==key||unsaved);
 useEffect(()=>{function cancel(){generation.current++;action.current?.abort();}return cancel;},[key]);
 useEffect(()=>{onBusy?.(busy);return()=>onBusy?.(false);},[busy,onBusy]);
 useWorkspaceReport("caption-motion","audio",busy?"Working":error?"Needs attention":"Ready","Generating silent caption motion preview…");
 async function run(){
  if(action.current||!canRun)return;const c=new AbortController(),version=++generation.current;action.current=c;setBusy(true);setError("");const timer=setTimeout(()=>c.abort(),45000);
  try{
   const response=await fetch(`${apiBase}/api/projects/${encodeURIComponent(projectId)}/caption-motion-preview`,{method:"POST",signal:c.signal,headers:{"Content-Type":"application/json"},body:JSON.stringify({cue_index:cue,expected_recipe_revision:context.recipeRevision,expected_framing_revision:context.framing.revision,expected_caption_revision:track.revision,expected_plan_revision:track.timeline?.plan_revision??null,expected_sequence_revision:track.timeline?.sequence_revision??null})});
   const data=await response.json();if(!response.ok)throw new Error(data?.error?.message||"Motion preview failed. Previous preview is retained; retry explicitly.");if(version!==generation.current||c.signal.aborted)return;
   const m=data as Motion,s=m?.spec;
   if(!m||m.schema_version!==1||m.source?.project_id!==projectId||!/^([0-9a-f]{64})$/.test(m.source.media_sha256)||s?.footage?.source?.media_sha256!==m.source.media_sha256||s.recipe_revision!==context.recipeRevision||s.framing?.revision!==context.framing.revision||s.captions?.revision!==track.revision||s.captions.timeline?.mode!==track.timeline?.mode||s.captions.timeline?.plan_revision!==track.timeline?.plan_revision||(s.captions.timeline?.sequence_revision??null)!==(track.timeline?.sequence_revision??null)||!validFont(s.captions.font_binding)||(track.font_binding&&s.captions.font_binding.sha256!==track.font_binding.sha256)||m.cue_index!==cue||!Number.isSafeInteger(m.start_frame)||m.start_frame<0||!Number.isSafeInteger(m.end_frame)||m.end_frame<=m.start_frame||m.end_frame-m.start_frame>120||m.decoded_frames!==m.end_frame-m.start_frame||!bounded(m.duration_seconds,.001,4)||Math.abs(m.duration_seconds-m.decoded_frames/30)>.05||![m.width,m.height].every(n=>Number.isInteger(n)&&n>=2&&n<=640)||!Number.isInteger(m.size_bytes)||m.size_bytes<=0||m.size_bytes>4194304||!/^([0-9a-f]{64})$/.test(m.sha256)||m.silent!==true||typeof m.video_base64!=="string"||m.video_base64.length>5592408||!/^AAAA[A-Za-z0-9+/]+={0,2}$/.test(m.video_base64))throw new Error("Invalid motion preview response. Previous preview is retained.");
   if(JSON.stringify(readAnimation(s.captions.animation))!==JSON.stringify(readAnimation(track.animation)))throw new Error("Motion animation changed. Reload saved captions.");setSaved({data:m,key});
  }catch(e){if(version===generation.current)setError(e instanceof Error&&e.name!=="AbortError"?e.message:"Motion preview timed out. Previous preview is retained; retry explicitly.");}finally{clearTimeout(timer);if(action.current===c){action.current=null;setBusy(false);}}
 }
 return <section className="caption-cue" aria-label="Caption motion preview"><h4>Real caption motion</h4>
 <p className="hint">Silent, up to four seconds around the selected saved cue, at a 640-pixel maximum edge. Long cues show their entrance; their exit may be outside this preview. No automatic playback. Stills above cannot demonstrate motion.</p>
 <button disabled={!canRun||busy} onClick={()=>void run()}>{saved?"Update caption motion preview":"Preview caption motion"}</button>
 {unsaved&&<p role="status">Save caption, color, framing and cut-plan changes before updating motion.</p>}
 {busy&&<p role="status">Generating actual motion… Processing is capped at 30 seconds; previous preview remains available.</p>}{error&&<p role="alert">{error}</p>}
 {saved&&<><p role="status">{outdated?"Outdated motion preview":"Caption motion preview ready"} · Silent · Cue {saved.data.cue_index+1} · Output {(saved.data.start_frame/30).toFixed(3)}–{(saved.data.end_frame/30).toFixed(3)}s</p>
 <video className="rendered-video" key={saved.data.sha256} aria-label="Silent caption motion preview" controls playsInline preload="metadata" width={saved.data.width} height={saved.data.height} src={`data:video/mp4;base64,${saved.data.video_base64}`}/>
 <p>Caption revision {saved.data.spec.captions.revision} · {saved.data.spec.captions.animation?.mode??"none"} · Recipe {saved.data.spec.recipe_revision} · Framing {saved.data.spec.framing.revision}{saved.data.spec.captions.timeline.plan_revision!==null?` · Cut plan ${saved.data.spec.captions.timeline.plan_revision}`:" · Whole clip"}</p></>}
 </section>;
}
