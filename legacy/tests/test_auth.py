"""Tests for authentication endpoints."""

import pytest
from fastapi.testclient import TestClient


def test_login_success(client: TestClient):
    from backend.auth import create_user
    from backend.database import SessionLocal

    db = SessionLocal()
    try:
        create_user(db, "loginuser", "testpass123")
        db.commit()
    finally:
        db.close()

    response = client.post("/api/auth/login", json={
        "username": "loginuser",
        "password": "testpass123",
    })
    assert response.status_code == 200
    data = response.json()
    assert "session_id" in data
    assert "user" in data
    assert data["user"]["username"] == "loginuser"


def test_login_failure(client: TestClient):
    response = client.post("/api/auth/login", json={
        "username": "nonexistent",
        "password": "wrongpass",
    })
    assert response.status_code == 401


def test_register(client: TestClient):
    response = client.post("/api/auth/register", json={
        "username": "newuser",
        "password": "securepass123",
        "email": "new@test.com",
    })
    assert response.status_code == 200
    assert response.json()["username"] == "newuser"


def test_register_duplicate(client: TestClient):
    client.post("/api/auth/register", json={
        "username": "dupuser",
        "password": "securepass123",
        "email": "dup@test.com",
    })
    response = client.post("/api/auth/register", json={
        "username": "dupuser",
        "password": "securepass123",
        "email": "dup@test.com",
    })
    assert response.status_code == 409


def test_register_ignores_client_role(client: TestClient):
    response = client.post("/api/auth/register", json={
        "username": "escalator",
        "password": "securepass123",
        "email": "escalator@test.com",
        "role": "admin",
    })
    assert response.status_code == 200
    assert response.json()["role"] == "user"


def test_register_requires_email(client: TestClient):
    response = client.post("/api/auth/register", json={
        "username": "noemailuser",
        "password": "securepass123",
    })
    assert response.status_code == 400


def test_me_authenticated(client: TestClient, user_token):
    response = client.get("/api/auth/me", cookies={"session_id": user_token})
    assert response.status_code == 200
    assert response.json()["username"] == "testuser"


def test_me_unauthenticated(client: TestClient):
    response = client.get("/api/auth/me")
    assert response.status_code == 401


def test_logout(client: TestClient, user_token):
    response = client.post("/api/auth/logout", cookies={"session_id": user_token})
    assert response.status_code == 200

    response = client.get("/api/auth/me", cookies={"session_id": user_token})
    assert response.status_code == 401


def test_create_api_key(client: TestClient, user_token):
    response = client.post(
        "/api/auth/apikeys",
        json={"name": "test-key", "role": "agent"},
        cookies={"session_id": user_token},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "test-key"
    assert data["key"].startswith("leg_")


def test_list_api_keys(client: TestClient, user_token):
    client.post(
        "/api/auth/apikeys",
        json={"name": "key1", "role": "agent"},
        cookies={"session_id": user_token},
    )
    response = client.get("/api/auth/apikeys", cookies={"session_id": user_token})
    assert response.status_code == 200
    assert len(response.json()) >= 1
