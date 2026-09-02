import os
import uuid
import hashlib
import secrets
import hmac
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import Request, HTTPException
from sqlalchemy.orm import Session
from . import models
from .config import config
from .logging_config import get_logger

logger = get_logger("legacy.auth")

SECRET_KEY = config.app.get("secret_key", "change-me-in-production")
SESSION_TTL = config.auth.get("session_ttl", 86400)
BCRYPT_ROUNDS = config.auth.get("bcrypt_rounds", 12)
AUTH_ENABLED = config.auth.get("enabled", True)
ALLOW_REGISTRATION = config.auth.get("allow_registration", True)


def _constant_time_compare(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)


def _hash_password(password: str) -> str:
    salt = os.urandom(16)
    key = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, 100000
    )
    return salt.hex() + ":" + key.hex()


def user_must_change_password(user) -> bool:
    return bool(getattr(user, "must_change_password", False))


def change_password(db: Session, user: models.User, current_password: str, new_password: str):
    if not _verify_password(current_password, user.password_hash):
        raise HTTPException(400, "Current password is incorrect")
    if new_password == current_password:
        raise HTTPException(400, "New password must be different from the current one")
    if len(new_password) < 8 or len(new_password) > 256:
        raise HTTPException(400, "Password must be between 8 and 256 characters")

    user.password_hash = _hash_password(new_password)
    user.must_change_password = 0
    db.commit()

    db.query(models.Session).filter(models.Session.user_id == user.id).delete()
    db.commit()
    logger.info(f"Password changed for user {user.username}")
    return user


def _verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, key_hex = stored.split(":")
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(key_hex)
        key = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, 100000
        )
        return _constant_time_compare(key.hex(), expected.hex())
    except (ValueError, AttributeError):
        return False


def _generate_session_id() -> str:
    return secrets.token_urlsafe(48)


def _hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def create_user(
    db: Session,
    username: str,
    password: str,
    email: Optional[str] = None,
    display_name: Optional[str] = None,
    role: str = "user",
) -> models.User:
    existing = db.query(models.User).filter(
        models.User.username == username
    ).first()
    if existing:
        raise HTTPException(409, "Username already exists")

    if email:
        existing_email = db.query(models.User).filter(
            models.User.email == email
        ).first()
        if existing_email:
            raise HTTPException(409, "Email already exists")

    user = models.User(
        username=username,
        email=email,
        password_hash=_hash_password(password),
        role=role,
        display_name=display_name or username,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info(f"User created: {username} (role={role})")
    return user


def authenticate_user(db: Session, username: str, password: str) -> Optional[models.User]:
    user = db.query(models.User).filter(
        models.User.username == username
    ).first()
    if not user:
        return None
    if not user.is_active:
        return None
    if not _verify_password(password, user.password_hash):
        return None
    return user


def create_session(
    db: Session,
    user: models.User,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> models.Session:
    session_id = _generate_session_id()
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=SESSION_TTL)

    session = models.Session(
        session_id=session_id,
        user_id=user.id,
        ip_address=ip_address,
        user_agent=user_agent,
        expires_at=expires_at,
    )
    db.add(session)

    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(session)

    return session


def get_session(db: Session, session_id: str) -> Optional[models.Session]:
    if not session_id:
        return None
    session = db.query(models.Session).filter(
        models.Session.session_id == session_id,
        models.Session.expires_at > datetime.now(timezone.utc),
    ).first()
    return session


def delete_session(db: Session, session_id: str) -> bool:
    session = db.query(models.Session).filter(
        models.Session.session_id == session_id
    ).first()
    if not session:
        return False
    db.delete(session)
    db.commit()
    return True


def cleanup_expired_sessions(db: Session) -> int:
    count = db.query(models.Session).filter(
        models.Session.expires_at <= datetime.now(timezone.utc)
    ).delete()
    db.commit()
    return count


def create_api_key(
    db: Session,
    name: str,
    user_id: int,
    role: str = "agent",
) -> tuple[models.ApiKey, str]:
    raw_key = f"leg_{secrets.token_urlsafe(32)}"
    key_hash = _hash_api_key(raw_key)

    api_key = models.ApiKey(
        key_hash=key_hash,
        name=name,
        user_id=user_id,
        role=role,
    )
    db.add(api_key)
    db.commit()
    db.refresh(api_key)

    return api_key, raw_key


def validate_api_key(db: Session, key: str) -> Optional[models.ApiKey]:
    key_hash = _hash_api_key(key)
    api_key = db.query(models.ApiKey).filter(
        models.ApiKey.key_hash == key_hash,
        models.ApiKey.is_active == 1,
    ).first()
    if not api_key:
        return None
    if api_key.expires_at and api_key.expires_at < datetime.now(timezone.utc):
        return None

    api_key.last_used_at = datetime.now(timezone.utc)
    db.commit()
    return api_key


def get_current_user(request: Request, db: Session) -> Optional[models.User]:
    if not AUTH_ENABLED:
        admin = db.query(models.User).filter(
            models.User.role == "admin"
        ).first()
        if not admin:
            admin = create_user(
                db, "admin", "admin", role="admin",
            )
        return admin

    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        api_key_str = auth_header[7:]
        api_key = validate_api_key(db, api_key_str)
        if api_key:
            return db.query(models.User).filter(
                models.User.id == api_key.user_id
            ).first()

    session_id = request.cookies.get("session_id", "")
    if not session_id:
        session_id = request.headers.get("X-Session-ID", "")

    if session_id:
        session = get_session(db, session_id)
        if session:
            return db.query(models.User).filter(
                models.User.id == session.user_id
            ).first()

    return None


def enforce_role(user: Optional[models.User], allowed_roles: list[str]):
    if not AUTH_ENABLED:
        return
    if not user:
        raise HTTPException(401, "Authentication required")
    if user.role != "admin" and user.role not in allowed_roles:
        raise HTTPException(403, "Insufficient permissions")


def check_visibility(
    entry_visibility: str,
    user: Optional[models.User],
    entry_user_id: Optional[int] = None,
) -> bool:
    if entry_visibility == "public":
        return True
    if entry_visibility == "system":
        return user is not None and user.role == "admin"
    if entry_visibility == "agent-only":
        return user is not None
    if entry_visibility == "shared":
        return user is not None
    if entry_visibility == "private":
        if not user:
            return False
        if user.role == "admin":
            return True
        return entry_user_id is None or entry_user_id == user.id
    return False
