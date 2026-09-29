"""Reframe API foundation."""

import os
from typing import Literal

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


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
app.add_middleware(CORSMiddleware, allow_origins=allowed_origins, allow_methods=["GET"])


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="reframe-api")
