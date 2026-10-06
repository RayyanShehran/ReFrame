"""Explicit optional-feature checks using the existing owned preview operation."""

from importlib.metadata import PackageNotFoundError, version

from pydantic import Field

import caption_ocr
import captions
import color_analysis as color
import frame_preview as preview
import projects
import reference_engine as engine
import transcribe_local
import video_render as render
from references import ReferenceError


class Feature(color.Schema):
    available: bool
    message: str = Field(max_length=2000)
    details: dict[str, str] = Field(default_factory=dict, max_length=12)


class Readiness(color.Schema):
    captions: Feature
    transcription: Feature
    ocr: Feature


def inspect(project_id, _request=None):
    projects.get_project(project_id)
    with preview.operation() as (directory, deadline):
        try:
            # Reuse the export capability/font validation path; no video or model is decoded.
            render.subtitle(
                captions.Track(
                    enabled=True, cues=[captions.Cue(start=0.0, end=1.0, text="Hello مرحبا")]
                ),
                project_id,
                directory,
                640,
                360,
                deadline,
                temp_budget=preview.TEMP_BUDGET,
            )
            caption = Feature(
                available=True,
                message=(
                    "Caption rendering and the default English/Arabic font are "
                    "ready. Selected fonts and actual text are validated "
                    "separately when saving/rendering."
                ),
            )
        except (ReferenceError, engine.RetrievalFailure) as exc:
            if exc.code not in {"captions_unavailable", "caption_font_unavailable"}:
                raise
            caption = Feature(available=False, message=exc.message)
        except FileNotFoundError:
            caption = Feature(
                available=False,
                message=(
                    "Install FFmpeg with libass to render captions. "
                    "Captions-disabled exports remain available with ordinary "
                    "FFmpeg."
                ),
            )
        dependencies = {}
        for name in ("faster-whisper", "ctranslate2", "onnxruntime", "av"):
            try:
                dependencies[name] = version(name)
            except PackageNotFoundError:
                dependencies[name] = "not installed"
        ready = (
            all(v != "not installed" for v in dependencies.values())
            and transcribe_local.model_ready()
        )
        speech = Feature(
            available=ready,
            message=(
                "Local transcription setup is present. Render a video with "
                "audio first, then generate and review a proposal."
            )
            if ready
            else (
                "Automatic transcription needs optional dependencies and the "
                "pinned local model. Manual captions remain available; see "
                "setup details."
            ),
            details={
                **dependencies,
                "setup_dependencies": "In backend: uv sync --locked --extra transcription",
                "setup_model": (
                    "uv run --locked --extra transcription python transcribe_local.py setup"
                ),
                "model_revision": transcribe_local.REVISION,
                "check": "Manifest presence/sizes and versions only; no inference/download.",
            },
        )
        ocr = caption_ocr.inspect_capability(directory, deadline)
        return Readiness(
            captions=caption,
            transcription=speech,
            ocr=Feature(
                available=ocr["available"],
                message=ocr["message"],
                details={
                    "engine": ocr["engine_version"] or "not ready",
                    "assets_commit": ocr["assets_commit"],
                    "setup": "See docs/REFERENCE_CAPTION_OCR.md for pinned engine/assets setup.",
                },
            ),
        )
