import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AutomaticMaker } from "./automatic-maker";
vi.mock("./workspace", () => ({ Workspace: () => <div>Advanced editor</div> }));
const pid = "11111111-1111-4111-8111-111111111111";
const metadata = { filename: "clip.mp4", size_bytes: 1000, duration_seconds: 3, width: 64, height: 64, video_codec: "h264", has_audio: false, audio_codec: null, frame_rate: 30, validation_status: "accepted", storage_status: "not_retained" };
const library = (count: number) => ({ clips: Array.from({ length: count }, (_, i) => ({ id: `11111111-1111-4111-8111-${String(i).padStart(12,"0")}`, name: `Clip ${i}`, primary: i===0, available: true, sha256: "a".repeat(64), metadata })), total_bytes: count*1000, max_clips: 10, max_bytes: 500*1024*1024 });
const ok = (value: unknown) => ({ ok: true, json: async () => value } as Response);
afterEach(() => { vi.unstubAllGlobals(); window.history.replaceState(null,"","/"); });
it("accepts multiple clips before a link, uploads sequentially and starts one persisted generation", async () => {
  window.history.replaceState(null,"","/");
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(()=>"blob:fixture"), revokeObjectURL: vi.fn() }));
  let count = 0, running = false, revision = 0;
  const uploads: string[] = [], requests: string[] = [];
  class Upload {
    upload = { onprogress: null }; status=200; responseText=""; onload: (()=>void)|null=null; onabort:(()=>void)|null=null; onerror=null; ontimeout=null; timeout=0;
    open() {} abort() { this.onabort?.(); }
    send(body: FormData) { uploads.push((body.get("file") as File).name); count++; this.responseText=JSON.stringify(library(count)); queueMicrotask(()=>this.onload?.()); }
  }
  vi.stubGlobal("XMLHttpRequest", Upload);
  vi.stubGlobal("fetch", vi.fn(async (url: string, options: RequestInit={}) => {
    if (url.endsWith("/clips")) return ok(library(count));
    if (url.endsWith("/generation")) {
      if(options.method==="POST") { expect(uploads).toEqual(["first.mp4","second.mov"]); running=true; revision=1; requests.push(url); }
      return ok({project_id:pid, revision, operation_id:null,status:running?"running":"idle",stage:running?"Reading reference":null,output_id:null,notices:[],options:null,message:null,failure_code:null});
    }
    if (options.method==="POST") { requests.push(url); return ok({id:pid,name:"My ReFrame video",reference:{canonical_url:"https://www.tiktok.com/@fixture/video/6718335390845095173"}}); }
    return ok([]);
  }));
  render(<AutomaticMaker />);
  await waitFor(()=>expect(screen.getByLabelText("Choose your clips")).toBeEnabled());
  expect(screen.getByLabelText("Choose your clips")).toHaveAttribute("multiple");
  fireEvent.change(screen.getByLabelText("Choose your clips"),{target:{files:[new File(["fixture"],"first.mp4"),new File(["fixture"],"second.mov")]}});
  expect(screen.getByText("first.mp4")).toBeInTheDocument();
  expect(screen.queryByText("Advanced editor")).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("TikTok reference link"),{target:{value:"https://www.tiktok.com/@fixture/video/6718335390845095173"}});
  fireEvent.click(screen.getByRole("button",{name:"Generate video"}));
  await screen.findByText("Reading reference");
  expect(requests).toHaveLength(2); expect(uploads).toHaveLength(2);
});
it("restores the saved generation and playable output without opening Customize", async () => {
  window.history.replaceState(null,"",`/?project=${pid}`);
  vi.stubGlobal("fetch",vi.fn(async (url:string)=> {
    if(url.endsWith("/clips")) return ok(library(2));
    if(url.endsWith("/render")) return ok({output:{output_id:"saved-output"}});
    if(url.endsWith("/generation")) return ok({project_id:pid,revision:3,status:"ready",stage:"Rendering",output_id:"saved-output",options:{audio:"mute",captions:"off",allow_reused_ranges:false},notices:[],failure_code:null,message:null});
    if(url.endsWith(pid)) return ok({id:pid,reference:{canonical_url:"https://www.tiktok.com/@fixture/video/6718335390845095173"}});
    return ok([]);
  }));
  render(<AutomaticMaker />);
  await screen.findByLabelText("Generated video");
  await waitFor(()=>expect(screen.getByLabelText("Audio")).toHaveValue("mute"));
  expect(screen.getByLabelText("Captions")).toHaveValue("off");
  expect(screen.getByRole("link",{name:"Download video"})).toHaveAttribute("href",expect.stringContaining("saved-output/download"));
  expect(screen.queryByText("Advanced editor")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button",{name:"Customize"}));
  expect(screen.getByText("Advanced editor")).toBeInTheDocument();
});
