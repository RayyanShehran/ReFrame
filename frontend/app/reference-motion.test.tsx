import {act,fireEvent,render,screen,waitFor} from "@testing-library/react";
import {afterEach,expect,it,vi} from "vitest";
import {ReferenceMotion} from "./reference-motion";
import {selectionKey} from "./caption-appearance-suggestions";
import {defaultStyle} from "./caption-style-controls";
const font={kind:"builtin" as const,candidate_id:"anton-regular" as const,font_id:null,sha256:"a".repeat(64),family:"Anton",style:"Regular (400)"};
const selection={schema_version:1 as const,method:"glyph-mask-dice-v1",source:{media_sha256:"b".repeat(64)},reference_operation_id:"op",timestamp_seconds:1,requested_timestamp_seconds:1,rectangle:{x:.1,y:.3,width:.8,height:.4},text:"Confirmed TEXT",polarity:"light" as const,reviewed_font:font};
const style={...defaultStyle,font:"anton-regular" as const,placement:"center" as const};
const request={expected_revision:0,expected_selection_revision:1,expected_selection_token:"d".repeat(64),start:0,end:2,region:selection.rectangle,base_style:style};
const suggestion={schema_version:1,method:"temporal-glyph-fit-v1",request,selection,fitting_style:style,outcome:"partial",values:{mode:"fade",entrance_seconds:.4,exit_seconds:null,initial_scale:null,displacement:null},notes:["Exit was not observed; its duration remains unchanged."],cue_start:.3,cue_end:null};
const saved={revision:1,status:"ready",suggestion,token:"c".repeat(64),current_selection:{revision:1,token:"d".repeat(64),selection},message:null};
const video={width:512,height:288,duration_seconds:2,decoded_frames:60,size_bytes:100,video_base64:"AAAAIGZ0eXA=",silent:true};
const ok=(data:unknown)=>({ok:true,json:async()=>data} as Response);
const props={projectId:"one",currentKey:selectionKey(selection,1),baseStyle:style,frame:null,disabled:false};
afterEach(()=>vi.unstubAllGlobals());
it("restores a partial suggestion, compares explicitly and applies only measured animation fields",async()=>{
 const apply=vi.fn(),fetcher=vi.fn(async(url:string)=>url.endsWith("/compare")?ok({revision:1,token:saved.token,reference:video,reconstruction:video}):url.endsWith("/apply")?ok({patch:{mode:"fade",entrance_seconds:.4},font_hash:font.sha256}):ok(saved));vi.stubGlobal("fetch",fetcher);
 render(<ReferenceMotion {...props} onApply={apply}/>);await screen.findByText(/Partial supported suggestion/);
 expect(fetcher).toHaveBeenCalledTimes(1);expect(screen.queryByLabelText("Reference motion reconstruction")).not.toBeInTheDocument();expect(apply).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole("button",{name:"Compare actual motion reconstruction"}));await screen.findByLabelText("Reference motion reconstruction");expect(screen.getByLabelText("Selected reference interval")).not.toHaveAttribute("autoplay");expect(screen.getByRole("button",{name:"Apply animation to draft"})).toBeDisabled();
 fireEvent.click(screen.getByLabelText("I reviewed the reference and reconstruction"));fireEvent.click(screen.getByRole("button",{name:"Apply animation to draft"}));await waitFor(()=>expect(apply).toHaveBeenCalledWith({mode:"fade",entrance_seconds:.4}));
 expect(screen.getAllByText("Not estimated — keep unchanged")).toHaveLength(3);
});
it("retains successful videos on replacement failure and invalidates changed interval/font inputs",async()=>{
 let failure=false;vi.stubGlobal("fetch",vi.fn(async(url:string)=>url.endsWith("/compare")?failure?({ok:false,json:async()=>({error:{message:"Deadline reached"}})} as Response):ok({revision:1,token:saved.token,reference:video,reconstruction:video}):ok(saved)));
 const view=render(<ReferenceMotion {...props} onApply={vi.fn()}/>);await screen.findByText(/Partial supported suggestion/);fireEvent.click(screen.getByRole("button",{name:"Compare actual motion reconstruction"}));await screen.findByLabelText("Reference motion reconstruction");failure=true;fireEvent.click(screen.getByRole("button",{name:"Compare actual motion reconstruction"}));await screen.findByText("Deadline reached");expect(screen.getByLabelText("Reference motion reconstruction")).toBeVisible();
 fireEvent.change(screen.getByLabelText("Interval end (seconds)"),{target:{value:"1.5"}});expect(screen.getByText(/Outdated motion suggestion/)).toBeVisible();expect(screen.getByText(/Outdated comparison videos/)).toBeVisible();expect(screen.queryByRole("button",{name:"Apply animation to draft"})).not.toBeInTheDocument();
 view.rerender(<ReferenceMotion {...props} currentKey={null} onApply={vi.fn()}/>);expect(screen.getByRole("button",{name:"Update caption motion analysis"})).toBeDisabled();
});
it("ignores late analysis after input change and keeps inconclusive results unappliable",async()=>{
 let release!:(r:Response)=>void;vi.stubGlobal("fetch",vi.fn(async(_url:string,options:RequestInit)=>options.method==="POST"?new Promise<Response>(resolve=>{release=resolve;}):ok(saved)));
 render(<ReferenceMotion {...props} onApply={vi.fn()}/>);await screen.findByText(/Partial supported suggestion/);fireEvent.click(screen.getByRole("button",{name:"Update caption motion analysis"}));fireEvent.change(screen.getByLabelText("Interval end (seconds)"),{target:{value:"1.5"}});await act(async()=>release(ok({...saved,revision:2,suggestion:{...suggestion,outcome:"inconclusive",values:{mode:null,entrance_seconds:null,exit_seconds:null,initial_scale:null,displacement:null}}})));expect(screen.getByText(/Outdated motion suggestion/)).toBeVisible();expect(screen.queryByText(/Unsupported or inconclusive ·/)).not.toBeInTheDocument();
});
