"""Tests for health check endpoints."""

from fastapi.testclient import TestClient


def test_health(client: TestClient):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "version" in data
    assert "database" in data
    assert "uptime_seconds" in data


def test_ready(client: TestClient):
    response = client.get("/ready")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"


def test_live(client: TestClient):
    response = client.get("/live")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "alive"


def test_full_status(client: TestClient):
    response = client.get("/status")
    assert response.status_code == 200
    data = response.json()
    assert "version" in data
    assert "database" in data
    assert "embedding" in data
    assert "queue" in data
    assert "uptime_seconds" in data
