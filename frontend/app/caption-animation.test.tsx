import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AnimationControls, MotionPreview, defaultAnimation, readAnimation } from "./caption-animation";
const font={kind:"default" as const,font_id:null,sha256:"a".repeat(64),family:"DejaVu Sans",style:"Book"};
const track={revision:1,enabled:true,animation:{...defaultAnimation,mode:"fade" as const},cues:[{start:.3,end:1,text:"Saved words"}],font_binding:font,timeline:{mode:"whole" as const,plan_revision:null}};
const props={projectId:"one",track,cue:0,context:{recipeRevision:1,recipeReady:true,recipeDirty:false,recipeBusy:false,framing:{revision:0,ready:true,dirty:false,busy:false}},plan:{revision:null,ready:false,dirty:false,busy:false,duration:null},ready:true,dirty:false,disabled:false,onBusy:vi.fn()};
const result={schema_version:1,source:{project_id:"one",media_sha256:"b".repeat(64)},spec:{recipe_revision:1,framing:{revision:0},footage:{source:{media_sha256:"b".repeat(64)}},captions:{...track,font_binding:font}},cue_index:0,start_frame:3,end_frame:36,width:480,height:270,duration_seconds:1.1,decoded_frames:33,size_bytes:32,sha256:"c".repeat(64),video_base64:"AAAAIGZ0eXBpc29tAAAAAA==",silent:true};
const ok=(data:unknown)=>({ok:true,json:async()=>data} as Response);
afterEach(()=>vi.unstubAllGlobals());
it("defaults old settings to None and exposes only relevant bounded numeric controls",()=>{
 expect(readAnimation(undefined)).toEqual(defaultAnimation);expect(()=>readAnimation({entrance_seconds:NaN})).toThrow();expect(()=>readAnimation({initial_scale:true})).toThrow();
 const change=vi.fn();const view=render(<AnimationControls value={defaultAnimation} onChange={change}/>);
 expect(screen.queryByLabelText("Entrance duration (seconds)")).not.toBeInTheDocument();
 fireEvent.change(screen.getByLabelText("Animation"),{target:{value:"pop"}});expect(change).toHaveBeenCalledWith({...defaultAnimation,mode:"pop"});
 view.rerender(<AnimationControls value={{...defaultAnimation,mode:"slide-up"}} onChange={change}/>);
 expect(screen.getByLabelText("Slide distance (% of canvas height)")).toHaveAttribute("max","0.25");expect(screen.queryByLabelText("Exit duration (seconds)")).not.toBeInTheDocument();
});
it("generates only explicitly, does not autoplay, and retains old video during failure/revision changes",async()=>{
 let fail=false;const fetcher=vi.fn(async()=> fail ? {ok:false,json:async()=>({error:{message:"Timed out; retry explicitly"}})} as Response:ok(result));vi.stubGlobal("fetch",fetcher);
 const view=render(<MotionPreview {...props}/>);expect(fetcher).not.toHaveBeenCalled();fireEvent.click(screen.getByRole("button",{name:"Preview caption motion"}));
 await screen.findByText(/Caption motion preview ready/);const video=screen.getByLabelText("Silent caption motion preview");expect(video).not.toHaveAttribute("autoplay");expect(video).toHaveAttribute("src",`data:video/mp4;base64,${result.video_base64}`);
 fail=true;fireEvent.click(screen.getByRole("button",{name:"Update caption motion preview"}));await screen.findByText(/Timed out; retry/);expect(video).toHaveAttribute("src",`data:video/mp4;base64,${result.video_base64}`);
 view.rerender(<MotionPreview {...props} track={{...track,revision:2}} dirty/>);expect(screen.getByText(/Outdated motion preview/)).toBeVisible();expect(screen.getByRole("button",{name:"Update caption motion preview"})).toBeDisabled();
});
it("ignores late responses after changing saved bindings and rejects malformed output",async()=>{
 let release!:(r:Response)=>void;vi.stubGlobal("fetch",vi.fn(()=>new Promise<Response>(resolve=>{release=resolve;})));
 const view=render(<MotionPreview {...props}/>);fireEvent.click(screen.getByRole("button",{name:"Preview caption motion"}));view.rerender(<MotionPreview {...props} track={{...track,revision:2}}/>);
 await act(async()=>release(ok(result)));expect(screen.queryByLabelText("Silent caption motion preview")).not.toBeInTheDocument();
 view.rerender(<MotionPreview {...props}/>);await waitFor(()=>expect(screen.getByRole("button",{name:"Preview caption motion"})).toBeEnabled());fireEvent.click(screen.getByRole("button",{name:"Preview caption motion"}));await act(async()=>release(ok({...result,decoded_frames:0})));
 expect(screen.getByText(/Invalid motion preview response/)).toBeVisible();expect(screen.queryByLabelText("Silent caption motion preview")).not.toBeInTheDocument();
});
