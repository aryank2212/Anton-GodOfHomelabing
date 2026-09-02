"""Tests for admin endpoints."""

from fastapi.testclient import TestClient


def test_admin_list_users(client: TestClient, admin_token):
    response = client.get("/api/admin/users", cookies={"session_id": admin_token})
    assert response.status_code == 200
    assert len(response.json()) >= 1


def test_admin_list_users_forbidden(client: TestClient, user_token):
    response = client.get("/api/admin/users", cookies={"session_id": user_token})
    assert response.status_code == 403


def test_admin_audit_log(client: TestClient, admin_token):
    response = client.get("/api/admin/audit", cookies={"session_id": admin_token})
    assert response.status_code == 200


def test_admin_audit_stats(client: TestClient, admin_token):
    response = client.get("/api/admin/audit/stats", cookies={"session_id": admin_token})
    assert response.status_code == 200
    assert "total" in response.json()


def test_admin_backup(client: TestClient, admin_token):
    response = client.post(
        "/api/admin/backup?backup_type=full",
        cookies={"session_id": admin_token},
    )
    assert response.status_code == 200
    assert "filename" in response.json()


def test_admin_queue_status(client: TestClient, admin_token):
    response = client.get("/api/admin/queue", cookies={"session_id": admin_token})
    assert response.status_code == 200
    data = response.json()
    assert "total" in data
    assert "pending" in data
    assert "running" in data
    assert "completed" in data
    assert "failed" in data


def test_system_version(client: TestClient):
    response = client.get("/api/admin/system/version")
    assert response.status_code == 200
    assert response.json()["name"] == "LEGACY"
