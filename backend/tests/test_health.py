from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_health_contract():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "reframe-api"}


def test_cors_allows_documented_origin_only():
    allowed = client.get("/health", headers={"Origin": "http://localhost:3000"})
    blocked = client.get("/health", headers={"Origin": "https://example.com"})
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "access-control-allow-origin" not in blocked.headers
