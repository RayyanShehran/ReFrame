import subprocess

import pytest

import caption_ocr as ocr
import caption_preview
import color_analysis as color
import projects
import reference_engine as engine
from references import ReferenceError
from tests.test_color_recipe import local as local  # noqa: F401
from tests.test_color_recipe import seed_analyses

CAP = {
    "available": True,
    "method": ocr.METHOD,
    "token": "a" * 64,
    "engine_version": "tesseract 5.5.3",
    "assets_commit": ocr.MANIFEST["assets_commit"],
    "message": "Ready",
}


@pytest.fixture
def boundary(local, monkeypatch):
    client, directory, _ = local
    saved, _ = seed_analyses(client, directory)
    pid = saved["id"]
    before = color.source(pid)
    frame = caption_preview.ReferenceFrame(
        project_id=pid,
        source=before["identity"],
        reference_operation_id=before["reference_operation_id"],
        requested_timestamp_seconds=0.5,
        timestamp_seconds=0.5,
        image={"width": 64, "height": 64, "png_base64": ""},
        warnings=[],
        ffmpeg_version="fixture",
    )
    monkeypatch.setattr(ocr, "inspect_capability", lambda *_: CAP)
    monkeypatch.setattr(caption_preview, "reference_frame", lambda *_args, **_kwargs: frame)

    def prepared(_frame, _request, path, _deadline):
        target = path / "crop.pgm"
        target.write_bytes(b"P5\n8 8\n255\n" + b"\xff" * 64)
        return target, 8, 8

    monkeypatch.setattr(ocr, "preprocess", prepared)
    monkeypatch.setattr(ocr, "recognize", lambda *_: "Known caption\nSecond line")
    body = {
        "expected_reference_operation_id": before["reference_operation_id"],
        "expected_source_hash": before["identity"].media_sha256,
        "timestamp_seconds": 0.5,
        "expected_capability_token": CAP["token"],
        "rectangle": {"x": 0.1, "y": 0.1, "width": 0.8, "height": 0.8},
        "language": "english",
        "polarity": "light",
    }
    return client, directory, pid, body


def test_explicit_proposal_review_preserves_project_and_captions(boundary):
    client, directory, pid, body = boundary
    base = f"/api/projects/{pid}"
    routes = ("captions", "font-match", "caption-appearance", "color-recipe")
    before = [client.get(f"{base}/{route}").json() for route in routes]
    response = client.post(base + "/caption-ocr", json=body)
    assert response.status_code == 200, response.text
    proposal = response.json()
    assert proposal["text"] == "Known caption\nSecond line"
    edited = r"{literal} \text correction"
    applied = client.post(
        base + "/caption-ocr/apply", json={**body, "proposal": proposal, "text": edited}
    )
    assert applied.status_code == 200, applied.text
    assert applied.json() == {"text": edited}
    assert before == [client.get(f"{base}/{route}").json() for route in routes]
    assert not list((directory / "preview-staging").iterdir())
    assert (
        client.post(
            base + "/caption-ocr/apply", json={**body, "proposal": proposal, "text": "a" * 81}
        ).status_code
        == 422
    )


def test_stale_crop_language_source_and_tampered_proposal_rejected(boundary):
    client, directory, pid, body = boundary
    url = f"/api/projects/{pid}/caption-ocr"
    proposal = client.post(url, json=body).json()
    for changed in (
        {"language": "arabic"},
        {"timestamp_seconds": 0.6},
        {"rectangle": {"x": 0.0, "y": 0.0, "width": 0.5, "height": 0.5}},
        {"expected_capability_token": "b" * 64},
        {"expected_source_hash": "c" * 64},
    ):
        assert (
            client.post(
                url + "/apply",
                json={**body, **changed, "proposal": proposal, "text": "Reviewed text"},
            ).status_code
            == 409
        )
    assert (
        client.post(
            url + "/apply",
            json={**body, "proposal": {**proposal, "text": "tampered"}, "text": "Reviewed text"},
        ).status_code
        == 409
    )
    with projects.database() as db:
        db.execute("DELETE FROM reference_operations WHERE project_id=?", (pid,))
    assert (
        client.post(
            url + "/apply", json={**body, "proposal": proposal, "text": "Reviewed text"}
        ).status_code
        == 409
    )


def test_unavailable_and_timeout_keep_manual_state_and_clean_stage(boundary, monkeypatch):
    client, directory, pid, body = boundary
    url = f"/api/projects/{pid}/caption-ocr"
    before = client.get(f"/api/projects/{pid}/captions").json()
    monkeypatch.setattr(
        ocr,
        "inspect_capability",
        lambda *_: {**CAP, "available": False, "message": "Install pinned OCR assets"},
    )
    assert client.get(url).json()["available"] is False
    assert client.post(url, json=body).json()["error"]["code"] == "ocr_unavailable"
    monkeypatch.setattr(ocr, "inspect_capability", lambda *_: CAP)

    def timeout(*_):
        raise engine.ProbeTimeout()

    monkeypatch.setattr(ocr, "recognize", timeout)
    result = client.post(url, json=body)
    assert result.status_code == 408
    assert before == client.get(f"/api/projects/{pid}/captions").json()
    assert not list((directory / "preview-staging").iterdir())
    monkeypatch.setattr(ocr, "recognize", lambda *_: "Retry caption")
    assert client.post(url, json=body).status_code == 200


def test_capability_assets_and_bounded_plain_output(tmp_path, monkeypatch):
    exe = tmp_path / "engine"
    exe.write_bytes(b"exe")
    monkeypatch.setattr(ocr, "executable", lambda: exe)
    monkeypatch.setattr(ocr, "DATA", tmp_path)
    assert ocr.inspect_capability(tmp_path, 9999999999)["available"] is False
    for name in ocr.MANIFEST["files"]:
        (tmp_path / name).write_bytes(b"changed")
    assert ocr.inspect_capability(tmp_path, 9999999999)["available"] is False
    request = type("Request", (), {"language": "arabic"})()

    def command(raw):
        monkeypatch.setattr(
            engine, "run_command", lambda *_a, **_k: subprocess.CompletedProcess([], 0, raw, b"")
        )

    command("مرحبا بالعالم\n".encode())
    assert ocr.recognize(exe, request, tmp_path, 9999999999) == "مرحبا بالعالم"
    for raw, code in [
        (b"\n\x0c", "ocr_empty"),
        (b"x" * 1025, "ocr_text_limit"),
        (b"\xff", "ocr_output"),
        (b"text\x00", "ocr_output"),
    ]:
        command(raw)
        with pytest.raises(ReferenceError) as error:
            ocr.recognize(exe, request, tmp_path, 9999999999)
        assert error.value.code == code
