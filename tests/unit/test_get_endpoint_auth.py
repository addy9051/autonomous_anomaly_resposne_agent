import pytest
from fastapi.testclient import TestClient

import api
from shared.config import get_settings

PROTECTED_GETS = [
    "/api/v1/status",
    "/api/v1/incidents",
    "/api/v1/incidents/active",
    "/api/v1/telemetry/recent",
    "/api/v1/knowledge/health",
    "/api/v1/actions/tiers",
    "/api/v1/ingestion/status",
    "/api/v1/health/detailed",
]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("REQUIRE_GATEWAY_AUTH", "false")
    monkeypatch.setenv("API_AUTH_TOKEN", "test-token")
    get_settings.cache_clear()
    yield TestClient(api.app)
    get_settings.cache_clear()


@pytest.mark.parametrize("path", PROTECTED_GETS)
def test_get_requires_key(client, path):
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get(path, headers={"X-API-Key": "test-token"}).status_code != 401


def test_health_stays_public(client):
    assert client.get("/health").status_code == 200
