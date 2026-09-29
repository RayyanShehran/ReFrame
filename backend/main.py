"""Reframe API."""

import os
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

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

app = FastAPI(title="Reframe API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["GET", "POST"],
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
        content={"error": {"code": "invalid_request", "message": "Provide a TikTok URL as text."}},
    )


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="reframe-api")


@app.post("/api/references/inspect", response_model=ReferenceDetails)
async def inspect(request: ReferenceRequest) -> ReferenceDetails:
    return await inspect_reference(request.url)
