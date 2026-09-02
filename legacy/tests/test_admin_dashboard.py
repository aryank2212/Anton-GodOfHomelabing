import pytest
from fastapi.testclient import TestClient
from backend.database import SessionLocal
from backend import models


@pytest.fixture
def entry(db):
    def _make(user_id, content="Secret private thoughts"):
        entry = models.Entry(
            user_id=user_id,
            title="My Entry",
            content=content,
            entry_type="Journal",
            visibility="private",
        )
        db.add(entry)
        db.commit()
        db.refresh(entry)
        return entry

    return _make


def test_admin_dashboard_requires_admin(client: TestClient, user_token):
    resp = client.get("/api/admin/dashboard", cookies={"session_id": user_token})
    assert resp.status_code == 403


def test_admin_dashboard_stats(client: TestClient, admin_token, user_token):
    resp = client.get("/api/admin/dashboard", cookies={"session_id": admin_token})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_users"] >= 2
    assert data["online_users"] >= 2
    assert data["active_sessions"] >= 2
    assert "users" in data
    assert "sessions" in data
    assert "recent_audit" in data


def test_admin_dashboard_user_rows(client: TestClient, admin_token, user_token, entry):
    db = SessionLocal()
    try:
        user = db.query(models.User).filter(models.User.username == "testuser").first()
        entry(user.id, "A private diary entry")
        entry(user.id, "Another private entry")
    finally:
        db.close()

    resp = client.get("/api/admin/dashboard", cookies={"session_id": admin_token})
    assert resp.status_code == 200
    rows = {u["username"]: u for u in resp.json()["users"]}
    assert "testuser" in rows
    row = rows["testuser"]
    assert row["entry_count"] == 2
    assert row["online"] is True
    assert row["session_count"] >= 1
    assert row["email"] is None


def test_admin_can_read_any_users_entries(client: TestClient, admin_token, user_token, entry):
    db = SessionLocal()
    try:
        user = db.query(models.User).filter(models.User.username == "testuser").first()
        entry(user.id, "Top secret private note")
        user_id = user.id
    finally:
        db.close()

    resp = client.get(
        f"/api/admin/users/{user_id}/entries", cookies={"session_id": admin_token}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert any("Top secret private note" in e["content"] for e in data)


def test_admin_can_read_user_sessions_with_ip(client: TestClient, admin_token, user_token):
    db = SessionLocal()
    try:
        session_row = db.query(models.Session).filter(
            models.Session.session_id == user_token
        ).first()
        session_row.ip_address = "203.0.113.42"
        db.commit()
        user_id = session_row.user_id
    finally:
        db.close()

    resp = client.get(
        f"/api/admin/users/{user_id}/sessions", cookies={"session_id": admin_token}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert any(s["ip_address"] == "203.0.113.42" for s in data)


def test_regular_user_cannot_read_others_entries(client: TestClient, admin_token, user_token):
    db = SessionLocal()
    try:
        user = db.query(models.User).filter(models.User.username == "testadmin").first()
        target = user.id
    finally:
        db.close()

    resp = client.get(
        f"/api/admin/users/{target}/entries", cookies={"session_id": user_token}
    )
    assert resp.status_code == 403


def test_admin_page_renders_for_admin(client: TestClient, admin_token):
    resp = client.get("/admin", cookies={"session_id": admin_token})
    assert resp.status_code == 200
    assert "Admin Console" in resp.text


def test_admin_page_redirects_non_admin(client: TestClient, user_token):
    resp = client.get("/admin", cookies={"session_id": user_token}, follow_redirects=False)
    assert resp.status_code in (302, 307)


def test_admin_entries_lists_all_users_entries(client: TestClient, admin_token, user_token, entry):
    db = SessionLocal()
    try:
        user = db.query(models.User).filter(models.User.username == "testuser").first()
        entry(user.id, "Private thoughts from testuser")
        entry(None, "System generated note")
    finally:
        db.close()

    resp = client.get("/api/admin/entries", cookies={"session_id": admin_token})
    assert resp.status_code == 200
    data = resp.json()
    assert any("Private thoughts from testuser" in e["content"] for e in data)
    assert any(e["username"] == "testuser" for e in data)
    assert any(e["username"] is None and "System generated note" in e["content"] for e in data)


def test_admin_entries_requires_admin(client: TestClient, user_token):
    resp = client.get("/api/admin/entries", cookies={"session_id": user_token})
    assert resp.status_code == 403


def test_admin_page_has_entries_browser(client: TestClient, admin_token):
    resp = client.get("/admin", cookies={"session_id": admin_token})
    assert resp.status_code == 200
    assert "All Entries" in resp.text
    assert "entries-search" in resp.text
    assert "entry-modal" in resp.text
