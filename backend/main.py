"""ReFrame API."""

import os
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import audio_settings
import color_analysis
import color_recipe
import edit_plan
import footage_analysis
import pacing_analysis
import projects
import reference_jobs
import video_render
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
    await projects.storage_call(pacing_analysis.recover)
    await projects.storage_call(footage_analysis.recover)
    await projects.storage_call(video_render.recover)
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
    )


@app.get("/api/projects/{project_id}/outputs/{output_id}/video")
async def play_output(project_id: str, output_id: str):
    return video_render.VideoResponse(project_id, output_id)


@app.get("/api/projects/{project_id}/outputs/{output_id}/download")
async def download_output(project_id: str, output_id: str):
    return video_render.VideoResponse(project_id, output_id, download=True)
