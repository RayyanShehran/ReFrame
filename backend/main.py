"""ReFrame API."""

import asyncio
import os
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import assembly
import audio_settings
import caption_appearance
import caption_motion
import caption_ocr
import caption_preview
import captions
import clip_library
import color_analysis
import color_recipe
import edit_plan
import font_assets
import font_match
import footage_analysis
import frame_preview
import framing
import grading
import pacing_analysis
import projects
import reference_jobs
import reference_motion
import sequence
import transcription
import video_render
import visual_matching
import workflow_capabilities
from clips import ClipDetails, cleanup_stale_files, inspect_clip
from references import ReferenceDetails, ReferenceError, ReferenceRequest, inspect_reference


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: Literal["reframe-api"]


allowed_origins = [
    origin.strip()
    for origin in os.getenv(
        "REFRAME_ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
    ).split(",")
    if origin.strip()
]


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await projects.storage_call(cleanup_stale_files)
    await projects.storage_call(projects.initialize)
    reference_jobs.stopping = False
    await projects.storage_call(reference_jobs.recover)
    await projects.storage_call(color_analysis.recover)
    await projects.storage_call(assembly.recover)
    await projects.storage_call(pacing_analysis.recover)
    await projects.storage_call(footage_analysis.recover)
    await projects.storage_call(video_render.recover)
    await projects.storage_call(transcription.recover)
    await projects.storage_call(grading.recover)
    try:
        yield
    finally:
        await reference_jobs.shutdown()


app = FastAPI(title="ReFrame API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
)


@app.exception_handler(ReferenceError)
async def reference_error(_request: Request, exc: ReferenceError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code, content={"error": {"code": exc.code, "message": exc.message}}
    )


@app.exception_handler(RequestValidationError)
async def validation_error(_request: Request, _exc: RequestValidationError) -> JSONResponse:
    if _request.url.path.endswith("/captions"):
        details = []
        for error in _exc.errors()[:10]:
            location = ".".join(str(part) for part in error["loc"] if part != "body")
            details.append(f"{location or 'Captions'}: {error['msg']}")
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "invalid_caption",
                    "message": "; ".join(details),
                }
            },
        )
    return JSONResponse(
        status_code=422,
        content={"error": {"code": "invalid_request", "message": "The request is invalid."}},
    )


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="reframe-api")


@app.post("/api/references/inspect", response_model=ReferenceDetails)
async def inspect(request: ReferenceRequest) -> ReferenceDetails:
    return await inspect_reference(request.url)


@app.post("/api/clips/inspect", response_model=ClipDetails)
async def inspect_uploaded_clip(request: Request) -> ClipDetails:
    return await inspect_clip(request)


@app.post("/api/projects", response_model=projects.ProjectDetails, status_code=201)
async def create_project(request: projects.ProjectRequest):
    return await projects.create_project(request)


@app.get("/api/projects", response_model=list[projects.ProjectDetails])
async def list_projects():
    return await projects.storage_call(projects.list_projects)


@app.get("/api/projects/{project_id}", response_model=projects.ProjectDetails)
async def get_project(project_id: str):
    return await projects.storage_call(projects.get_project, project_id)


@app.delete("/api/projects/{project_id}", status_code=204)
async def delete_project(project_id: str):
    await reference_jobs.delete(project_id)


@app.post(
    "/api/projects/{project_id}/reference-media",
    response_model=reference_jobs.Operation,
    status_code=202,
)
async def retrieve_project_reference(project_id: str):
    return await reference_jobs.start(project_id)


@app.get("/api/projects/{project_id}/reference-media", response_model=reference_jobs.Operation)
async def get_project_reference(project_id: str):
    return await projects.storage_call(reference_jobs.get_operation, project_id)


@app.post(
    "/api/projects/{project_id}/style-blueprint",
    response_model=color_analysis.Operation,
    status_code=202,
)
async def analyze_project_reference(project_id: str):
    return await reference_jobs.start(project_id, analysis=True)


@app.get("/api/projects/{project_id}/style-blueprint", response_model=color_analysis.Operation)
async def get_style_blueprint(project_id: str):
    return await projects.storage_call(color_analysis.get_operation, project_id)


@app.post("/api/projects/{project_id}/clip", response_model=projects.ProjectDetails)
async def upload_project_clip(project_id: str, request: Request):
    return await projects.upload_clip(project_id, request)


@app.post(
    "/api/projects/{project_id}/pacing", response_model=pacing_analysis.Operation, status_code=202
)
async def analyze_pacing(project_id: str):
    return await reference_jobs.start(project_id, analysis="pacing")


@app.get("/api/projects/{project_id}/pacing", response_model=pacing_analysis.Operation)
async def get_pacing(project_id: str):
    return await projects.storage_call(pacing_analysis.get_operation, project_id)


@app.post(
    "/api/projects/{project_id}/footage-color",
    response_model=footage_analysis.Operation,
    status_code=202,
)
async def analyze_footage(project_id: str):
    return await reference_jobs.start(project_id, analysis="footage")


@app.get("/api/projects/{project_id}/footage-color", response_model=footage_analysis.Operation)
async def get_footage_color(project_id: str):
    return await projects.storage_call(footage_analysis.get_operation, project_id)


@app.get("/api/projects/{project_id}/color-recipe", response_model=color_recipe.Result)
async def read_color_recipe(project_id: str):
    async with projects.operation_lock:
        return await projects.storage_call(color_recipe.read, project_id)


@app.post("/api/projects/{project_id}/color-recipe/generate", response_model=color_recipe.Result)
async def generate_color_recipe(project_id: str, request: color_recipe.GenerateRequest):
    async with projects.operation_lock:
        return await projects.storage_call(color_recipe.generate, project_id, request)


@app.post("/api/projects/{project_id}/color-recipe", response_model=color_recipe.Result)
async def save_color_recipe(project_id: str, request: color_recipe.SaveRequest):
    async with projects.operation_lock:
        return await projects.storage_call(color_recipe.save, project_id, request)


@app.get("/api/projects/{project_id}/render", response_model=video_render.Operation)
async def read_render(project_id: str):
    async with projects.operation_lock:
        return await projects.storage_call(video_render.get_operation, project_id)


@app.get("/api/projects/{project_id}/audio", response_model=audio_settings.Result)
async def read_audio_settings(project_id: str):
    async with projects.operation_lock:
        return await projects.storage_call(audio_settings.read, project_id)


@app.post("/api/projects/{project_id}/audio", response_model=audio_settings.Result)
async def save_audio_settings(project_id: str, request: audio_settings.SaveRequest):
    async with projects.operation_lock:
        return await projects.storage_call(audio_settings.save, project_id, request)


@app.get("/api/projects/{project_id}/framing", response_model=framing.Result)
async def read_framing(project_id: str):
    async with projects.operation_lock:
        return await projects.storage_call(framing.read, project_id)


@app.post("/api/projects/{project_id}/framing", response_model=framing.Result)
async def save_framing(project_id: str, request: framing.SaveRequest):
    async with projects.operation_lock:
        return await projects.storage_call(framing.save, project_id, request)


@app.get("/api/projects/{project_id}/framing/source", response_model=framing.SourceGeometry)
async def framing_source(project_id: str):
    async with reference_jobs.start_lock:
        if reference_jobs.active:
            raise ReferenceError(
                409,
                "reference_busy",
                "Wait for the current media job, then retry framing inspection.",
            )
        async with projects.operation_lock:
            await projects.storage_call(reference_jobs.check_quarantine)
            return await projects.storage_call(framing.inspect_source, project_id)


@app.get("/api/projects/{project_id}/captions", response_model=captions.Result)
async def read_captions(project_id: str):
    async with projects.operation_lock:
        return await projects.storage_call(captions.read, project_id)


@app.get("/api/projects/{project_id}/caption-font", response_model=font_assets.Result)
async def read_caption_font(project_id: str):
    async with projects.operation_lock:
        return await projects.storage_call(font_assets.read, project_id)


@app.post("/api/projects/{project_id}/caption-font", response_model=font_assets.Result)
async def upload_caption_font(
    project_id: str,
    request: Request,
    expected_revision: int = Query(ge=0),
    expected_caption_revision: int = Query(ge=0),
    replace: bool = False,
):
    if reference_jobs.start_lock.locked() or reference_jobs.active or reference_jobs.stopping:
        raise ReferenceError(
            409, "reference_busy", "Wait for the current media job before uploading a font."
        )
    async with reference_jobs.start_lock:
        async with projects.operation_lock:
            await projects.storage_call(reference_jobs.check_quarantine)
            await projects.storage_call(font_assets.row, project_id)
            raw = bytearray()
            try:
                async with asyncio.timeout(15):
                    async for chunk in request.stream():
                        if len(raw) + len(chunk) > font_assets.MAX_BYTES:
                            raise ReferenceError(
                                413, "font_size_limit", "Font upload is limited to 2 MiB."
                            )
                        raw.extend(chunk)
            except TimeoutError:
                raise ReferenceError(
                    408, "font_upload_timeout", "Font upload exceeded 15 seconds."
                ) from None
            return await projects.storage_call(
                font_assets.upload,
                project_id,
                bytes(raw),
                expected_revision,
                expected_caption_revision,
                replace,
            )


@app.post("/api/projects/{project_id}/frame-preview", response_model=frame_preview.Preview)
async def preview_frame(project_id: str, request: frame_preview.PreviewRequest):
    return await saved_still(project_id, request, frame_preview.generate)


@app.post(
    "/api/projects/{project_id}/reference-frame", response_model=caption_preview.ReferenceFrame
)
async def inspect_reference_frame(project_id: str, request: caption_preview.ReferenceRequest):
    return await saved_still(project_id, request, caption_preview.reference_frame)


@app.post("/api/projects/{project_id}/caption-preview", response_model=caption_preview.CaptionFrame)
async def preview_caption(project_id: str, request: caption_preview.CaptionRequest):
    return await saved_still(project_id, request, caption_preview.caption_frame)


@app.post("/api/projects/{project_id}/caption-motion-preview", response_model=caption_motion.Motion)
async def preview_caption_motion(project_id: str, request: caption_preview.CaptionRequest):
    return await saved_still(project_id, request, caption_motion.generate)


@app.get(
    "/api/projects/{project_id}/optional-features", response_model=workflow_capabilities.Readiness
)
async def optional_features(project_id: str):
    return await saved_still(project_id, None, workflow_capabilities.inspect)


@app.get("/api/projects/{project_id}/caption-ocr")
async def caption_ocr_capability(project_id: str):
    return await saved_still(project_id, None, caption_ocr.capability)


@app.post("/api/projects/{project_id}/caption-ocr", response_model=caption_ocr.Proposal)
async def extract_caption_text(project_id: str, request: caption_ocr.Request):
    return await saved_still(project_id, request, caption_ocr.generate)


@app.post("/api/projects/{project_id}/caption-ocr/apply")
async def apply_caption_text(project_id: str, request: caption_ocr.Apply):
    return await saved_still(project_id, request, caption_ocr.apply)


@app.get("/api/projects/{project_id}/caption-appearance")
async def read_caption_appearance(project_id: str):
    async with projects.operation_lock:
        return await projects.storage_call(caption_appearance.read, project_id)


@app.post("/api/projects/{project_id}/caption-appearance")
async def suggest_caption_appearance(project_id: str, request: caption_appearance.Request):
    return await saved_still(project_id, request, caption_appearance.generate)


@app.post("/api/projects/{project_id}/caption-appearance/apply")
async def apply_caption_appearance(project_id: str, request: caption_appearance.Apply):
    async with projects.operation_lock:
        return await projects.storage_call(caption_appearance.apply, project_id, request)


@app.get("/api/projects/{project_id}/reference-motion")
async def read_reference_motion(project_id: str):
    async with projects.operation_lock:
        return await projects.storage_call(reference_motion.read, project_id)


@app.post("/api/projects/{project_id}/reference-motion")
async def analyze_reference_motion(project_id: str, request: reference_motion.Request):
    return await saved_still(project_id, request, reference_motion.analyze)


@app.post("/api/projects/{project_id}/reference-motion/preview")
async def preview_reference_interval(project_id: str, request: reference_motion.Request):
    return await saved_still(project_id, request, reference_motion.interval_preview)


@app.post("/api/projects/{project_id}/reference-motion/compare")
async def compare_reference_motion(project_id: str, request: reference_motion.Apply):
    return await saved_still(project_id, request, reference_motion.comparison)


@app.post("/api/projects/{project_id}/reference-motion/apply")
async def apply_reference_motion(project_id: str, request: reference_motion.Apply):
    async with projects.operation_lock:
        return await projects.storage_call(reference_motion.apply, project_id, request)


@app.get("/api/projects/{project_id}/font-match")
async def read_font_match(project_id: str):
    async with projects.operation_lock:
        return await projects.storage_call(font_match.read, project_id)


@app.post("/api/projects/{project_id}/font-match")
async def compare_fonts(project_id: str, request: font_match.Request):
    return await saved_still(project_id, request, font_match.generate)


@app.post("/api/projects/{project_id}/font-match/review")
async def review_font_match(project_id: str, request: font_match.Review):
    async with projects.operation_lock:
        return await projects.storage_call(font_match.review, project_id, request)


async def saved_still(project_id, request, generate):
    if reference_jobs.start_lock.locked() or reference_jobs.active or reference_jobs.stopping:
        raise ReferenceError(
            409, "reference_busy", "Wait for the current media job, then update preview."
        )
    async with reference_jobs.start_lock:
        async with projects.operation_lock:
            await projects.storage_call(reference_jobs.check_quarantine)
            return await projects.storage_call(generate, project_id, request)


@app.get("/api/projects/{project_id}/transcription", response_model=transcription.Operation)
async def read_transcription(project_id: str):
    async with projects.operation_lock:
        return await projects.storage_call(transcription.get_operation, project_id, True)


@app.post(
    "/api/projects/{project_id}/transcription",
    response_model=transcription.Operation,
    status_code=202,
)
async def generate_transcription(project_id: str, request: transcription.GenerateRequest):
    return await reference_jobs.start(
        project_id, analysis="transcription", transcription_request=request
    )


@app.post(
    "/api/projects/{project_id}/transcription/apply", response_model=transcription.Application
)
async def apply_transcription(project_id: str, request: transcription.ApplyRequest):
    async with projects.operation_lock:
        return await projects.storage_call(transcription.apply, project_id, request)


@app.delete("/api/projects/{project_id}/transcription", response_model=transcription.Operation)
async def discard_transcription(project_id: str):
    async with projects.operation_lock:
        return await projects.storage_call(transcription.discard, project_id)


@app.post("/api/projects/{project_id}/captions", response_model=captions.Result)
async def save_captions(project_id: str, request: captions.SaveRequest):
    async with projects.operation_lock:
        return await projects.storage_call(captions.save, project_id, request)


@app.post("/api/projects/{project_id}/captions/import", response_model=captions.ImportResult)
async def import_captions(project_id: str, request: Request):
    await projects.storage_call(captions.record, project_id)
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > captions.SRT_LIMIT:
            raise ReferenceError(413, "srt_too_large", "SRT import is limited to 128 KiB UTF-8.")
        raw.extend(chunk)
    return await projects.storage_call(captions.parse_srt, bytes(raw))


@app.get("/api/projects/{project_id}/edit-plan", response_model=edit_plan.Result)
async def read_edit_plan(project_id: str):
    async with projects.operation_lock:
        return await projects.storage_call(edit_plan.read, project_id)


@app.post("/api/projects/{project_id}/edit-plan/generate", response_model=edit_plan.Result)
async def generate_edit_plan(project_id: str, request: edit_plan.GenerateRequest):
    async with projects.operation_lock:
        return await projects.storage_call(edit_plan.generate, project_id, request)


@app.post("/api/projects/{project_id}/edit-plan", response_model=edit_plan.Result)
async def save_edit_plan(project_id: str, request: edit_plan.SaveRequest):
    async with projects.operation_lock:
        return await projects.storage_call(edit_plan.save, project_id, request)


@app.post(
    "/api/projects/{project_id}/render", response_model=video_render.Operation, status_code=202
)
async def start_render(project_id: str, request: video_render.RenderRequest):
    return await reference_jobs.start(
        project_id,
        analysis="render",
        expected_revision=request.expected_revision,
        expected_plan_revision=request.expected_plan_revision,
        expected_audio_revision=request.expected_audio_revision,
        expected_caption_revision=request.expected_caption_revision,
        expected_framing_revision=request.expected_framing_revision,
        expected_sequence_revision=request.expected_sequence_revision,
    )


@app.get("/api/projects/{project_id}/outputs/{output_id}/video")
async def play_output(project_id: str, output_id: str):
    return video_render.VideoResponse(project_id, output_id)


@app.get("/api/projects/{project_id}/outputs/{output_id}/download")
async def download_output(project_id: str, output_id: str):
    return video_render.VideoResponse(project_id, output_id, download=True)


@app.get("/api/projects/{project_id}/clips", response_model=clip_library.Library)
async def list_clips(project_id: str):
    async with projects.operation_lock:
        return await projects.storage_call(clip_library.read, project_id)


@app.post("/api/projects/{project_id}/clips", response_model=clip_library.Library)
async def upload_footage(project_id: str, request: Request):
    return await clip_library.upload(project_id, request)


@app.post("/api/projects/{project_id}/clips/{clip_id}", response_model=clip_library.Library)
async def rename_footage(project_id: str, clip_id: str, request: clip_library.Rename):
    async with projects.operation_lock:
        return await projects.storage_call(clip_library.rename, project_id, clip_id, request)


@app.delete("/api/projects/{project_id}/clips/{clip_id}", response_model=clip_library.Library)
async def remove_footage(project_id: str, clip_id: str):
    if reference_jobs.active or reference_jobs.start_lock.locked():
        raise ReferenceError(
            409, "reference_busy", "Wait for the media job before removing footage."
        )
    async with reference_jobs.start_lock:
        async with projects.operation_lock:
            return await projects.storage_call(clip_library.remove, project_id, clip_id)


@app.get("/api/projects/{project_id}/clips/{clip_id}/video")
async def play_footage(project_id: str, clip_id: str):
    return clip_library.VideoResponse(project_id, clip_id)


@app.get("/api/projects/{project_id}/sequence", response_model=sequence.Result)
async def read_sequence(project_id: str):
    async with projects.operation_lock:
        return await projects.storage_call(sequence.read, project_id)


@app.post("/api/projects/{project_id}/sequence/generate", response_model=sequence.Result)
async def generate_sequence(project_id: str, request: sequence.Generate):
    async with projects.operation_lock:
        return await projects.storage_call(sequence.generate, project_id, request)


@app.post("/api/projects/{project_id}/sequence", response_model=sequence.Result)
async def save_sequence(project_id: str, request: sequence.Save):
    async with projects.operation_lock:
        return await projects.storage_call(sequence.save, project_id, request)


@app.get("/api/visual-model")
async def visual_model_status():
    return await projects.storage_call(visual_matching.readiness)


@app.get("/api/projects/{project_id}/assembly", response_model=assembly.Operation)
async def get_assembly(project_id: str):
    return await projects.storage_call(assembly.get_operation, project_id)


@app.post("/api/projects/{project_id}/assembly", response_model=assembly.Operation, status_code=202)
async def suggest_assembly(project_id: str, request: assembly.Settings):
    return await reference_jobs.start(project_id, analysis="assembly", assembly_request=request)


@app.post("/api/projects/{project_id}/assembly/apply")
async def apply_assembly(project_id: str, request: assembly.Apply):
    async with projects.operation_lock:
        return await projects.storage_call(assembly.apply, project_id, request)


@app.post("/api/projects/{project_id}/assembly/cancel", response_model=assembly.Operation)
async def cancel_assembly(project_id: str):
    current = await projects.storage_call(assembly.get_operation, project_id)
    async with reference_jobs.start_lock:
        owned = reference_jobs.active
        if owned and owned[0] == project_id and owned[1] == current.operation_id:
            owned[2].set()
    if owned and owned[0] == project_id and owned[1] == current.operation_id:
        await asyncio.shield(owned[3])
    return await projects.storage_call(assembly.get_operation, project_id)


@app.get("/api/projects/{project_id}/grading", response_model=grading.Operation)
async def read_grading(project_id: str):
    async with projects.operation_lock:
        return await projects.storage_call(grading.get_operation, project_id)


@app.post("/api/projects/{project_id}/grading", response_model=grading.Operation)
async def save_grading(project_id: str, request: grading.Save):
    async with projects.operation_lock:
        return await projects.storage_call(grading.save, project_id, request)


@app.post(
    "/api/projects/{project_id}/grading/prepare", response_model=grading.Operation, status_code=202
)
async def prepare_grading(project_id: str, request: grading.Prepare):
    return await reference_jobs.start(project_id, analysis="grading", grading_request=request)
