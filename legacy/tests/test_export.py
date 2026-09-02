"""Tests for export and import functionality."""

from fastapi.testclient import TestClient


def test_export_json(client: TestClient, user_token):
    client.post(
        "/api/entries/",
        json={"title": "Export Test", "content": "Export this content"},
        cookies={"session_id": user_token},
    )

    response = client.get(
        "/api/export/export?fmt=json",
        cookies={"session_id": user_token},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"


def test_export_csv(client: TestClient, user_token):
    response = client.get(
        "/api/export/export?fmt=csv",
        cookies={"session_id": user_token},
    )
    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]


def test_export_markdown(client: TestClient, user_token):
    response = client.get(
        "/api/export/export?fmt=markdown",
        cookies={"session_id": user_token},
    )
    assert response.status_code == 200
    assert "text/markdown" in response.headers["content-type"]


def test_import_json(client: TestClient, user_token):
    data = [
        {"title": "Imported 1", "content": "Content 1"},
        {"title": "Imported 2", "content": "Content 2"},
    ]
    import json
    response = client.post(
        "/api/export/import?fmt=json",
        content=json.dumps(data),
        cookies={"session_id": user_token},
    )
    assert response.status_code == 200
    result = response.json()
    assert result["imported"] == 2


def test_import_unauthenticated(client: TestClient):
    response = client.get("/api/export/export?fmt=json")
    assert response.status_code == 401
