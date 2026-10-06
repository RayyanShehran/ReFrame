from importlib.metadata import PackageNotFoundError

import reference_engine as engine
import workflow_capabilities as capabilities
from tests.test_color_recipe import local as local  # noqa: F401
from tests.test_color_recipe import seed_analyses


def test_real_caption_readiness_missing_optional_setup_preserves_settings(local, monkeypatch):
    client, directory, _ = local
    saved, _ = seed_analyses(client, directory)
    base = f"/api/projects/{saved['id']}"
    routes = ["color-recipe", "captions", "audio", "framing"]
    before = [client.get(f"{base}/{route}").json() for route in routes]
    monkeypatch.setattr(
        capabilities.caption_ocr,
        "inspect_capability",
        lambda *args: {
            "available": False,
            "message": "Install pinned OCR; manual entry is available.",
            "engine_version": None,
            "assets_commit": "a" * 40,
        },
    )

    def missing(_name):
        raise PackageNotFoundError

    monkeypatch.setattr(capabilities, "version", missing)
    monkeypatch.setattr(
        capabilities.transcribe_local,
        "run",
        lambda *args: (_ for _ in ()).throw(AssertionError("No inference")),
    )
    response = client.get(base + "/optional-features")
    assert response.status_code == 200, response.text
    assert response.json()["captions"]["available"]
    assert not response.json()["transcription"]["available"]
    assert not response.json()["ocr"]["available"]
    assert before == [client.get(f"{base}/{route}").json() for route in routes]
    assert not list((directory / "preview-staging").iterdir())


def test_unavailable_caption_support_is_optional_and_busy_is_explicit(local, monkeypatch):
    client, directory, _ = local
    saved, _ = seed_analyses(client, directory)
    base = f"/api/projects/{saved['id']}"

    def unavailable(*args, **kwargs):
        raise engine.RetrievalFailure("captions_unavailable", "Install FFmpeg with libass.")

    monkeypatch.setattr(capabilities.render, "subtitle", unavailable)
    monkeypatch.setattr(
        capabilities.caption_ocr,
        "inspect_capability",
        lambda *args: {
            "available": False,
            "message": "Manual text remains available.",
            "engine_version": None,
            "assets_commit": "a" * 40,
        },
    )
    response = client.get(base + "/optional-features")
    assert response.status_code == 200, response.text
    assert not response.json()["captions"]["available"]
    assert not list((directory / "preview-staging").iterdir())
    import reference_jobs

    monkeypatch.setattr(reference_jobs, "active", object())
    assert client.get(base + "/optional-features").status_code == 409
    monkeypatch.setattr(reference_jobs, "active", None)
