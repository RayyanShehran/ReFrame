import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { OptionalFeatures } from "./optional-features";
import { GuidedWorkspace, WorkspaceSection, useWorkspaceReport } from "./guided-workspace";
import { RenderVideo } from "./render-video";
const feature = {available: false, message: "Set up optionally; manual controls remain usable.", details: {setup: "Explicit setup only"}};
afterEach(() => vi.unstubAllGlobals());
it("checks optional features only explicitly and keeps details collapsed", async () => {
 const fetcher=vi.fn(async()=>({ok:true,json:async()=>({captions:feature,transcription:feature,ocr:feature})} as Response)); vi.stubGlobal("fetch",fetcher);
 const view=render(<OptionalFeatures projectId="one"/>); expect(fetcher).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole("button",{name:"Check optional features"}));
 await screen.findByText("Reference OCR: Setup needed"); expect(fetcher).toHaveBeenCalledTimes(1);
 expect(screen.getAllByText("Explicit setup only")[0]).not.toBeVisible();
 view.rerender(<OptionalFeatures projectId="one"/>); expect(fetcher).toHaveBeenCalledTimes(1);
});
function OptionalFailure(){useWorkspaceReport("audio-settings","audio","Ready");useWorkspaceReport("caption-settings","audio","Ready");useWorkspaceReport("transcription","audio","Needs attention");useWorkspaceReport("pacing","style","Needs attention");useWorkspaceReport("recipe","style","Ready");return null;}
it("optional readiness and transcription/pacing failures do not block a basic export", async()=>{
 vi.stubGlobal("fetch",vi.fn(async(url:string)=>url.endsWith("optional-features")?({ok:false,json:async()=>({error:{message:"Check after current job"}})} as Response):({ok:true,json:async()=>({status:"idle",output:null,spec:null,outdated:false,message:null,failure_code:null})} as Response)));
 render(<GuidedWorkspace hasFootage><OptionalFailure/><WorkspaceSection section="audio"><OptionalFeatures projectId="one"/></WorkspaceSection><WorkspaceSection section="export"><RenderVideo projectId="one" revision={1} recipeReady dirty={false} busy={false}/></WorkspaceSection></GuidedWorkspace>);
 fireEvent.click(screen.getByRole("button",{name:"Audio & captions"}));fireEvent.click(screen.getByRole("button",{name:"Check optional features"}));await screen.findByRole("alert");
 expect(screen.getByRole("button",{name:"Audio & captions"})).toHaveAccessibleDescription("Ready");expect(screen.getByRole("button",{name:"Style & cuts"})).toHaveAccessibleDescription("Ready");
 fireEvent.click(screen.getByRole("button",{name:"Export"}));await waitFor(()=>expect(screen.getByRole("button",{name:"Render video"})).toBeEnabled());
});
