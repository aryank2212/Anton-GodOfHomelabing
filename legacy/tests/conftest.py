import os
import sys
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

os.environ["DATABASE_URL"] = "sqlite:///./test.db"
os.environ["LEGACY_LOG_FILE"] = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "logs", "test-legacy.log"
)
os.environ["LEGACY_CONFIG"] = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "config.yaml"
)

from backend.main import app
from backend.database import engine, Base, SessionLocal, get_db
from backend import models
from backend.config import config

config._data["auth"]["enabled"] = True
config._data["security"]["trusted_hosts"] = list(
    config._data["security"].get("trusted_hosts", [])
) + ["testserver"]


@pytest.fixture(autouse=True)
def clear_rate_limits():
    from backend.security import rate_limiter

    yield
    rate_limiter.clear_all()


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def admin_token(client):
    from backend.auth import create_user, create_session

    db = SessionLocal()
    try:
        user = create_user(db, "testadmin", "testpass123", role="admin")
        session = create_session(db, user)
        db.commit()
        yield session.session_id
    finally:
        db.close()


@pytest.fixture
def user_token(client):
    from backend.auth import create_user, create_session

    db = SessionLocal()
    try:
        user = create_user(db, "testuser", "testpass123", role="user")
        session = create_session(db, user)
        db.commit()
        yield session.session_id
    finally:
        db.close()
