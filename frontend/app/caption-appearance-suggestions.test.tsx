import {act,fireEvent,render,screen,waitFor} from "@testing-library/react";
import {afterEach,expect,it,vi} from "vitest";
import {AppearanceSuggestions,selectionKey} from "./caption-appearance-suggestions";
import {defaultStyle} from "./caption-style-controls";
const font={kind:"builtin" as const,candidate_id:"anton-regular" as const,font_id:null,sha256:"a".repeat(64),family:"Anton",style:"Regular (400)"};
const selection={schema_version:1 as const,method:"glyph-mask-dice-v1",source:{media_sha256:"b".repeat(64)},reference_operation_id:"op",timestamp_seconds:1,requested_timestamp_seconds:1,rectangle:{x:.1,y:.3,width:.8,height:.4},text:"Confirmed TEXT",polarity:"light" as const,reviewed_font:font};
const style={...defaultStyle,font:"anton-regular" as const,placement:"center" as const};
const suggestion={schema_version:1,method:"static-fill-render-fit-v1",selection,selection_revision:1,base_style:style,values:{size_percent:7,horizontal:.36,vertical:.64,color:"#F3D848",outline_color:null,outline_percent:0},outline_status:"none_detected",notes:["Shadow and animation: Not estimated."],fitting_attempts:2};
const image={width:640,height:360,png_base64:"iVBORw0KGgoAAA=="};
const saved={revision:1,status:"ready",suggestion,token:"c".repeat(64),current_selection:{revision:1,token:"d".repeat(64),font},message:null};
const ok=(data:unknown)=>({ok:true,json:async()=>data} as Response);
afterEach(()=>vi.unstubAllGlobals());
it("restores measured values, reconstructs explicitly and applies only checked properties",async()=>{
 const apply=vi.fn(),busy=vi.fn();
 vi.stubGlobal("fetch",vi.fn(async(url:string,options:RequestInit)=>{
  if(url.endsWith("/apply")){const body=JSON.parse(options.body as string);expect(body.fields).toEqual(["color"]);return ok({patch:{color:"#F3D848"},font_hash:font.sha256});}
  return ok(options.method==="POST"?{...saved,revision:2,reference:image,reconstruction:image}:saved);
 }));
 render(<AppearanceSuggestions projectId="one" currentKey={selectionKey(selection,1)} baseStyle={style} disabled={false} onApply={apply} onBusy={busy}/>);
 await screen.findByText(/No outline detected/);
 expect(screen.getByText(/restored. Update explicitly/)).toBeVisible();
 expect(apply).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole("button",{name:"Update appearance suggestion"}));
 await screen.findByRole("img",{name:"Actual proposed caption reconstruction on a neutral gray full canvas"});
 for(const label of ["Text size (% of canvas height)","Horizontal anchor (%)","Vertical anchor (%)","Outline width (% of canvas height)"])fireEvent.click(screen.getByLabelText(`Apply ${label}`,{exact:true}));
 expect(screen.getByLabelText("Apply Outline color",{exact:true})).toBeDisabled();
 fireEvent.click(screen.getByRole("button",{name:"Apply appearance to draft"}));
 await waitFor(()=>expect(apply).toHaveBeenCalledWith({color:"#F3D848"}));
});
it("retains successful images on failure and rejects late or stale selection results",async()=>{
 let count=0,release!:(r:Response)=>void;
 vi.stubGlobal("fetch",vi.fn(async(_url:string,options:RequestInit)=>{
  if(options.method!=="POST")return ok(saved);
  count++;if(count===1)return ok({...saved,revision:2,reference:image,reconstruction:image});
  if(count===2)return {ok:false,status:422,json:async()=>({error:{message:"Crop is unusable; expand its edges."}})} as Response;
  return new Promise<Response>(resolve=>{release=resolve;});
 }));
 const props={projectId:"one",currentKey:selectionKey(selection,1),baseStyle:style,disabled:false,onApply:vi.fn(),onBusy:vi.fn()};
 const view=render(<AppearanceSuggestions {...props}/>);await screen.findByText(/No outline detected/);
 fireEvent.click(screen.getByRole("button",{name:"Update appearance suggestion"}));await screen.findByRole("img",{name:"Actual proposed caption reconstruction on a neutral gray full canvas"});
 fireEvent.click(screen.getByRole("button",{name:"Update appearance suggestion"}));await screen.findByText("Crop is unusable; expand its edges.");
 expect(screen.getByRole("img",{name:"Actual proposed caption reconstruction on a neutral gray full canvas"})).toBeVisible();
 fireEvent.click(screen.getByRole("button",{name:"Update appearance suggestion"}));view.rerender(<AppearanceSuggestions {...props} currentKey={null}/>);
 await act(async()=>release(ok({...saved,revision:3,reference:image,reconstruction:image})));
 expect(screen.getByText(/Outdated appearance suggestions/)).toBeVisible();
 expect(screen.getByRole("button",{name:"Apply appearance to draft"})).toBeDisabled();
});
