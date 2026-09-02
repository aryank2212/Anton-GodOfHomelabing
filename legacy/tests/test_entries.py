"""Tests for entry CRUD endpoints."""

import pytest
from fastapi.testclient import TestClient


def test_create_entry(client: TestClient, user_token):
    response = client.post(
        "/api/entries/",
        json={
            "title": "Test Entry",
            "content": "This is a test entry content.",
            "entry_type": "Journal",
            "visibility": "private",
        },
        cookies={"session_id": user_token},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Test Entry"
    assert data["content"] == "This is a test entry content."
    assert "id" in data


def test_read_entries(client: TestClient, user_token):
    client.post(
        "/api/entries/",
        json={"title": "Entry 1", "content": "Content 1"},
        cookies={"session_id": user_token},
    )
    response = client.get("/api/entries/", cookies={"session_id": user_token})
    assert response.status_code == 200
    assert len(response.json()) >= 1


def test_read_entry(client: TestClient, user_token):
    create_resp = client.post(
        "/api/entries/",
        json={"title": "Specific", "content": "Specific content"},
        cookies={"session_id": user_token},
    )
    entry_id = create_resp.json()["id"]

    response = client.get(
        f"/api/entries/{entry_id}",
        cookies={"session_id": user_token},
    )
    assert response.status_code == 200
    assert response.json()["title"] == "Specific"


def test_read_entry_not_found(client: TestClient, user_token):
    response = client.get("/api/entries/99999", cookies={"session_id": user_token})
    assert response.status_code == 404


def test_update_entry(client: TestClient, user_token):
    create_resp = client.post(
        "/api/entries/",
        json={"title": "Before", "content": "Before content"},
        cookies={"session_id": user_token},
    )
    entry_id = create_resp.json()["id"]

    response = client.put(
        f"/api/entries/{entry_id}",
        json={"title": "After", "content": "After content"},
        cookies={"session_id": user_token},
    )
    assert response.status_code == 200
    assert response.json()["title"] == "After"


def test_delete_entry(client: TestClient, user_token):
    create_resp = client.post(
        "/api/entries/",
        json={"title": "Delete Me", "content": "To be deleted"},
        cookies={"session_id": user_token},
    )
    entry_id = create_resp.json()["id"]

    response = client.delete(
        f"/api/entries/{entry_id}",
        cookies={"session_id": user_token},
    )
    assert response.status_code == 200

    response = client.get(
        f"/api/entries/{entry_id}",
        cookies={"session_id": user_token},
    )
    assert response.status_code == 404


def test_create_entry_unauthenticated(client: TestClient):
    response = client.post(
        "/api/entries/",
        json={"title": "No Auth", "content": "Should fail"},
    )
    assert response.status_code == 401


def test_visibility_private(client: TestClient):
    from backend.auth import create_user, create_session
    from backend.database import SessionLocal

    db = SessionLocal()
    try:
        user1 = create_user(db, "user1", "pass123", role="user")
        session1 = create_session(db, user1)
        user2 = create_user(db, "user2", "pass456", role="user")
        session2 = create_session(db, user2)
        db.commit()
        token1 = session1.session_id
        token2 = session2.session_id
    finally:
        db.close()

    resp = client.post(
        "/api/entries/",
        json={"title": "Private Entry", "content": "Secret", "visibility": "private"},
        cookies={"session_id": token1},
    )
    entry_id = resp.json()["id"]

    resp2 = client.get(
        f"/api/entries/{entry_id}",
        cookies={"session_id": token2},
    )
    assert resp2.status_code == 403
