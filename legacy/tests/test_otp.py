"""Tests for the email OTP verification service."""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from backend.config import config


@pytest.fixture
def email_user(client: TestClient):
    from backend.auth import create_user, create_session
    from backend.database import SessionLocal

    db = SessionLocal()
    try:
        user = create_user(
            db, "otpuser", "securepass123",
            email="otp@test.com", role="user",
        )
        session = create_session(db, user)
        db.commit()
        yield session.session_id
    finally:
        db.close()


def test_otp_send_and_verify_full_flow(client: TestClient):
    with patch("backend.services.otp_service.generate_code", return_value="123456"):
        resp = client.post("/api/auth/register", json={
            "username": "newotp",
            "password": "securepass123",
            "email": "newotp@test.com",
        })
        assert resp.status_code == 200

    login = client.post("/api/auth/login", json={
        "username": "newotp", "password": "securepass123",
    })
    sid = login.json()["session_id"]

    bad = client.post(
        "/api/auth/otp/verify", json={"code": "999999"},
        cookies={"session_id": sid},
    )
    assert bad.status_code == 400

    ok = client.post(
        "/api/auth/otp/verify", json={"code": "123456"},
        cookies={"session_id": sid},
    )
    assert ok.status_code == 200
    assert ok.json()["verified"] is True
    assert ok.json()["email_verified"] is True

    me = client.get("/api/auth/me", cookies={"session_id": sid})
    assert me.json()["email_verified"] is True


def test_otp_send_masks_email(client: TestClient, email_user):
    resp = client.post(
        "/api/auth/otp/send", json={"purpose": "verify_email"},
        cookies={"session_id": email_user},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["sent"] is True
    assert data["email"] == "ot***@test.com"
    assert data["expires_in"] == 600


def test_otp_send_cooldown(client: TestClient, email_user):
    with patch("backend.services.otp_service.generate_code", return_value="111111"):
        first = client.post(
            "/api/auth/otp/send", json={"purpose": "verify_email"},
            cookies={"session_id": email_user},
        )
        assert first.status_code == 200

        second = client.post(
            "/api/auth/otp/send", json={"purpose": "verify_email"},
            cookies={"session_id": email_user},
        )
        assert second.status_code == 429


def test_otp_requires_email(client: TestClient, user_token):
    resp = client.post(
        "/api/auth/otp/send", json={"purpose": "verify_email"},
        cookies={"session_id": user_token},
    )
    assert resp.status_code == 400


def test_otp_exhausts_attempts(client: TestClient, email_user):
    config._data["otp"]["max_attempts"] = 2
    try:
        with patch("backend.services.otp_service.generate_code", return_value="222222"):
            client.post(
                "/api/auth/otp/send", json={"purpose": "verify_email"},
                cookies={"session_id": email_user},
            )

        statuses = []
        for _ in range(2):
            r = client.post(
                "/api/auth/otp/verify", json={"code": "000000"},
                cookies={"session_id": email_user},
            )
            statuses.append(r.status_code)
        assert statuses == [400, 429]
    finally:
        config._data["otp"]["max_attempts"] = 5


def test_otp_verify_requires_auth(client: TestClient):
    resp = client.post(
        "/api/auth/otp/verify", json={"code": "123456"},
    )
    assert resp.status_code == 401


def test_verify_email_page_renders(client: TestClient, email_user):
    resp = client.get("/verify-email", cookies={"session_id": email_user})
    assert resp.status_code == 200
    assert "verify-form" in resp.text
