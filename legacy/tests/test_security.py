"""Tests for security hardening: headers, host validation, rate limiting, password policy."""

import pytest
from fastapi.testclient import TestClient


def _make_user_with_session(client, username="securuser", password="securepass123", role="user", **overrides):
    from backend.auth import create_user, create_session
    from backend.database import SessionLocal

    db = SessionLocal()
    try:
        user = create_user(db, username, password, role=role, **overrides)
        session = create_session(db, user)
        db.commit()
        yield session.session_id
    finally:
        db.close()


@pytest.fixture
def admin_must_change(client):
    from backend.auth import create_user, create_session
    from backend.database import SessionLocal

    db = SessionLocal()
    try:
        user = create_user(db, "securadmin", "admin", role="admin")
        user.must_change_password = 1
        db.commit()
        session = create_session(db, user)
        db.commit()
        yield session.session_id, user.id
    finally:
        db.close()


@pytest.fixture
def normal_user(client):
    from backend.auth import create_user, create_session
    from backend.database import SessionLocal

    db = SessionLocal()
    try:
        user = create_user(db, "secnormal", "securepass123", role="user")
        session = create_session(db, user)
        db.commit()
        yield session.session_id
    finally:
        db.close()


def test_security_headers_present(client: TestClient):
    resp = client.get("/login")
    assert resp.status_code == 200
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert "Content-Security-Policy" in resp.headers
    assert "Referrer-Policy" in resp.headers


def test_host_header_rejected(client: TestClient):
    resp = client.get("/login", headers={"host": "evil.example.com"})
    assert resp.status_code == 400


def test_host_header_allowed(client: TestClient):
    resp = client.get("/login", headers={"host": "journal.redclove.space"})
    assert resp.status_code == 200


def test_register_rate_limited(client: TestClient):
    for i in range(5):
        resp = client.post("/api/auth/register", json={
            "username": f"ratelimit{i}",
            "password": "securepass123",
            "email": f"ratelimit{i}@test.com",
        })
        assert resp.status_code == 200

    resp = client.post("/api/auth/register", json={
        "username": "ratelimit6",
        "password": "securepass123",
    })
    assert resp.status_code == 429


def test_login_rate_limited(client: TestClient):
    for _ in range(5):
        resp = client.post("/api/auth/login", json={
            "username": "bruteforce", "password": "wrongpass",
        })
        assert resp.status_code == 401

    resp = client.post("/api/auth/login", json={
        "username": "bruteforce", "password": "wrongpass",
    })
    assert resp.status_code == 429


def test_login_flags_must_change_password(client: TestClient, admin_must_change):
    sid, _ = admin_must_change
    resp = client.post("/api/auth/login", json={
        "username": "securadmin", "password": "admin",
    })
    assert resp.status_code == 200
    assert resp.json()["must_change_password"] is True


def test_password_change_required_blocks_pages(client: TestClient, admin_must_change):
    sid, _ = admin_must_change
    resp = client.get("/", cookies={"session_id": sid}, follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert "/change-password" in resp.headers["location"]

    page = client.get("/change-password", cookies={"session_id": sid})
    assert page.status_code == 200
    assert "change-password-form" in page.text


def test_password_change_required_blocks_admin_api(client: TestClient, admin_must_change):
    sid, _ = admin_must_change
    resp = client.get("/api/admin/users", cookies={"session_id": sid})
    assert resp.status_code == 403


def test_change_password_flow(client: TestClient, admin_must_change):
    sid, _ = admin_must_change

    bad = client.post("/api/auth/change-password", json={
        "current_password": "wrong", "new_password": "newpass123"},
        cookies={"session_id": sid},
    )
    assert bad.status_code == 400

    ok = client.post("/api/auth/change-password", json={
        "current_password": "admin", "new_password": "newpass123"},
        cookies={"session_id": sid},
    )
    assert ok.status_code == 200
    assert ok.json()["must_change_password"] is False

    # sessions invalidated after password change
    me = client.get("/api/auth/me", cookies={"session_id": sid})
    assert me.status_code == 401

    # old password no longer works, new one does
    old = client.post("/api/auth/login", json={
        "username": "securadmin", "password": "admin",
    })
    assert old.status_code == 401

    new = client.post("/api/auth/login", json={
        "username": "securadmin", "password": "newpass123",
    })
    assert new.status_code == 200
    assert new.json()["must_change_password"] is False


def test_change_password_requires_auth(client: TestClient):
    resp = client.post("/api/auth/change-password", json={
        "current_password": "x", "new_password": "y" * 8,
    })
    assert resp.status_code == 401
