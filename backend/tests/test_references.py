import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

import references
from main import app

client = TestClient(app)
VIDEO = "https://www.tiktok.com/@scout2015/video/6718335390845095173"
META = {
    "version": "1.0",
    "type": "video",
    "provider_name": "TikTok",
    "provider_url": "https://www.tiktok.com",
    "title": "A video",
    "author_name": "Scout",
}


def inspect(url: str):
    return client.post("/api/references/inspect", json={"url": url})


def mock_upstream(monkeypatch, handler):
    real_client = httpx.AsyncClient

    def factory(**kwargs):
        assert kwargs["follow_redirects"] is False
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(references.httpx, "AsyncClient", factory)


@pytest.mark.parametrize("host", ["www.tiktok.com", "tiktok.com", "m.tiktok.com"])
def test_normalizes_hosts_and_strips_tracking(monkeypatch, host):
    def handler(request):
        assert request.url.host == "www.tiktok.com"
        assert request.url.path == "/oembed"
        assert request.url.params["url"] == VIDEO
        return httpx.Response(200, json=META)

    mock_upstream(monkeypatch, handler)
    response = inspect(f"  https://{host}/@scout2015/video/6718335390845095173?share=1#part  ")
    assert response.status_code == 200
    assert response.json() == {
        "provider": "tiktok",
        "video_id": "6718335390845095173",
        "canonical_url": VIDEO,
        "title": "A video",
        "author_name": "Scout",
        "metadata_status": "available",
        "analysis_status": "not_started",
    }


def test_video_id_remains_string_with_leading_zeroes():
    assert references.canonicalize_url("https://tiktok.com/@name/video/00012") == (
        "https://www.tiktok.com/@name/video/00012",
        "00012",
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://www.tiktok.com/@name/video/123",
        "https://tiktok.com.evil.test/@name/video/123",
        "https://evil-tiktok.com/@name/video/123",
        "https://user@tiktok.com/@name/video/123",
        "https://@tiktok.com/@name/video/123",
        "https://tiktok.com:443/@name/video/123",
        "https://tiktok.com/@name/video/1 2",
        "https://tiktok.com/@name/video/12\n3",
        "https://tiktok.com/@name",
        "https://tiktok.com/@name/photo/123",
        "https://tiktok.com/@name/live",
        "https://tiktok.com/playlist/123",
        "https://tiktok.com/@name/video/abc",
        "https://tiktok.com/@name/video/123/other",
        "https://tiktok.com/@name/video/123/",
    ],
)
def test_rejects_unsafe_or_unsupported_urls(url):
    with pytest.raises(references.ReferenceError) as error:
        references.canonicalize_url(url)
    assert (error.value.status_code, error.value.code) == (422, "invalid_url")


@pytest.mark.parametrize(
    "url",
    [
        "https://vm.tiktok.com/abc",
        "https://vt.tiktok.com/abc",
        "https://www.tiktok.com/t/abc",
    ],
)
def test_short_links_get_specific_guidance(url):
    response = inspect(url)
    assert response.status_code == 422
    assert response.json() == {
        "error": {"code": "short_link_unsupported", "message": references.SHORT_LINK_MESSAGE}
    }


def test_missing_optional_metadata_is_null(monkeypatch):
    mock_upstream(
        monkeypatch,
        lambda _request: httpx.Response(
            200,
            json={key: META[key] for key in ("version", "type", "provider_name", "provider_url")},
        ),
    )
    response = inspect(VIDEO)
    assert response.status_code == 200
    assert response.json()["title"] is None
    assert response.json()["author_name"] is None


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {
            "version": "1.0",
            "type": "photo",
            "provider_name": "TikTok",
            "provider_url": "https://www.tiktok.com",
        },
        {**META, "provider_name": "Other"},
        {**META, "title": 2},
        {**META, "author_name": "x" * 501},
    ],
)
def test_malformed_metadata(monkeypatch, payload):
    mock_upstream(monkeypatch, lambda _request: httpx.Response(200, json=payload))
    response = inspect(VIDEO)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_invalid"


def test_malformed_json(monkeypatch):
    mock_upstream(monkeypatch, lambda _request: httpx.Response(200, content=b"not json"))
    assert inspect(VIDEO).json()["error"]["code"] == "upstream_invalid"


def test_oversized_body(monkeypatch):
    mock_upstream(
        monkeypatch, lambda _request: httpx.Response(200, content=b"x" * (references.MAX_BODY + 1))
    )
    response = inspect(VIDEO)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_oversized"


@pytest.mark.parametrize(
    "status,expected_status,code",
    [
        (404, 404, "reference_unavailable"),
        (410, 404, "reference_unavailable"),
        (429, 503, "upstream_rate_limited"),
        (403, 502, "upstream_failure"),
        (301, 502, "upstream_failure"),
        (500, 502, "upstream_failure"),
    ],
)
def test_upstream_statuses(monkeypatch, status, expected_status, code):
    mock_upstream(
        monkeypatch,
        lambda _request: httpx.Response(status, headers={"location": "https://example.com/"}),
    )
    response = inspect(VIDEO)
    assert response.status_code == expected_status
    assert response.json()["error"]["code"] == code


@pytest.mark.parametrize(
    "failure,expected_status,code",
    [
        (httpx.ReadTimeout("secret"), 504, "upstream_timeout"),
        (httpx.ConnectError("secret"), 502, "upstream_failure"),
    ],
)
def test_network_errors_are_safe(monkeypatch, failure, expected_status, code):
    def handler(_request):
        raise failure

    mock_upstream(monkeypatch, handler)
    response = inspect(VIDEO)
    assert response.status_code == expected_status
    assert response.json()["error"]["code"] == code
    assert "secret" not in response.text


def test_overall_deadline(monkeypatch):
    async def slow(_request):
        await asyncio.sleep(0.05)
        return httpx.Response(200, json=META)

    mock_upstream(monkeypatch, slow)
    real_timeout = asyncio.timeout
    monkeypatch.setattr(references.asyncio, "timeout", lambda _seconds: real_timeout(0.01))
    response = inspect(VIDEO)
    assert response.status_code == 504
    assert response.json()["error"]["code"] == "upstream_timeout"


def test_post_cors_preflight_and_health():
    response = client.options(
        "/api/references/inspect",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "POST" in response.headers["access-control-allow-methods"]
    assert "content-type" in response.headers["access-control-allow-headers"].lower()
    assert client.get("/health").json() == {"status": "ok", "service": "reframe-api"}


def test_invalid_request_envelope():
    response = client.post("/api/references/inspect", json={"url": 42})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
