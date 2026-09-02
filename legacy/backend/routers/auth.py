from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models, schemas, auth
from ..logging_config import get_logger
from ..security import (
    client_ip,
    register_rate_limit,
    login_ip_rate_limit,
    check_login_username_rate,
    otp_send_rate_limit,
    otp_verify_rate_limit,
)
from ..services.audit_service import record_audit
from ..services.otp_service import send_otp, verify_otp, cleanup_expired_otps

router = APIRouter(prefix="/api/auth", tags=["Authentication"])

logger = get_logger("legacy.auth")


@router.post("/register", response_model=schemas.UserResponse)
def register(
    data: schemas.UserCreate,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(register_rate_limit()),
):
    if not auth.ALLOW_REGISTRATION:
        raise HTTPException(403, "Registration is disabled")
    if not data.email or "@" not in data.email:
        raise HTTPException(400, "A valid email address is required")
    user = auth.create_user(
        db=db,
        username=data.username,
        password=data.password,
        email=data.email,
        display_name=data.display_name,
        role=schemas.UserRole.USER.value,
    )
    record_audit(
        db, "authentication", "user", user.id, user.username,
        resource_type="user", resource_id=user.id,
        details={"action": "register"},
    )
    if user.email:
        try:
            send_otp(db, user)
        except Exception as e:
            logger.warning(f"OTP send after registration failed for {user.username}: {e}")
    return user


@router.post("/otp/send", response_model=schemas.OtpSendResponse)
def send_otp_code(
    data: schemas.OtpSendRequest,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(otp_send_rate_limit()),
):
    user = auth.get_current_user(request, db)
    if not user:
        raise HTTPException(401, "Not authenticated")

    result = send_otp(db, user, purpose=data.purpose)
    record_audit(
        db, "send_otp", "user", user.id, user.username,
        resource_type="user", resource_id=user.id,
        details={"purpose": data.purpose},
    )
    return result


@router.post("/otp/verify", response_model=schemas.OtpVerifyResponse)
def verify_otp_code(
    data: schemas.OtpVerifyRequest,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(otp_verify_rate_limit()),
):
    user = auth.get_current_user(request, db)
    if not user:
        raise HTTPException(401, "Not authenticated")

    cleanup_expired_otps(db)
    result = verify_otp(db, user, data.code, purpose=data.purpose)
    record_audit(
        db, "verify_otp", "user", user.id, user.username,
        resource_type="user", resource_id=user.id,
        details={"purpose": data.purpose},
    )
    return result


@router.post("/login", response_model=schemas.LoginResponse)
def login(
    data: schemas.LoginRequest,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(login_ip_rate_limit()),
):
    check_login_username_rate(request, data.username)
    user = auth.authenticate_user(db, data.username, data.password)
    if not user:
        record_audit(
            db, "authentication", "anonymous", resource_type="user",
            details={"action": "login_failed", "username": data.username},
            status="failure",
        )
        raise HTTPException(401, "Invalid username or password")

    session = auth.create_session(
        db, user,
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )

    record_audit(
        db, "login", "user", user.id, user.username,
        resource_type="session", resource_id=session.id,
        details={"action": "login_success"},
    )

    return schemas.LoginResponse(
        session_id=session.session_id,
        user=schemas.UserResponse.model_validate(user),
        expires_at=session.expires_at,
        must_change_password=auth.user_must_change_password(user),
    )


@router.post("/change-password", response_model=schemas.UserResponse)
def change_password(
    data: schemas.ChangePasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)
    if not user:
        raise HTTPException(401, "Not authenticated")

    auth.change_password(db, user, data.current_password, data.new_password)
    record_audit(
        db, "password_change", "user", user.id, user.username,
        resource_type="user", resource_id=user.id,
    )
    return user


@router.post("/logout")
def logout(
    request: Request,
    db: Session = Depends(get_db),
):
    session_id = request.cookies.get("session_id", "")
    if session_id:
        session = auth.get_session(db, session_id)
        if session:
            record_audit(
                db, "logout", "user", session.user_id,
                resource_type="session", resource_id=session.id,
            )
        auth.delete_session(db, session_id)
    return {"status": "logged_out"}


@router.get("/me", response_model=schemas.UserResponse)
def get_me(
    request: Request,
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


@router.get("/sessions")
def list_sessions(
    request: Request,
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)
    if not user:
        raise HTTPException(401, "Not authenticated")

    sessions = db.query(models.Session).filter(
        models.Session.user_id == user.id,
        models.Session.expires_at > datetime.now(timezone.utc),
    ).all()

    return [
        {
            "id": s.id,
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "expires_at": s.expires_at.isoformat() if s.expires_at else None,
            "ip_address": s.ip_address,
            "user_agent": s.user_agent,
        }
        for s in sessions
    ]


@router.delete("/sessions/{session_id}")
def delete_session(
    session_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)
    if not user:
        raise HTTPException(401, "Not authenticated")

    session = db.query(models.Session).filter(
        models.Session.id == session_id,
        models.Session.user_id == user.id,
    ).first()
    if not session:
        raise HTTPException(404, "Session not found")

    record_audit(
        db, "logout", "user", user.id,
        resource_type="session", resource_id=session_id,
    )
    db.delete(session)
    db.commit()
    return {"status": "deleted"}


@router.post("/apikeys", response_model=schemas.ApiKeyResponse)
def create_api_key(
    data: schemas.ApiKeyCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)
    if not user:
        raise HTTPException(401, "Not authenticated")

    api_key, raw_key = auth.create_api_key(db, data.name, user.id, data.role)

    record_audit(
        db, "create", "user", user.id, user.username,
        resource_type="api_key", resource_id=api_key.id,
        details={"name": data.name, "role": data.role},
    )

    resp = schemas.ApiKeyResponse.model_validate(api_key)
    resp.key = raw_key
    return resp


@router.get("/apikeys", response_model=list[schemas.ApiKeyResponse])
def list_api_keys(
    request: Request,
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)
    if not user:
        raise HTTPException(401, "Not authenticated")

    return db.query(models.ApiKey).filter(
        models.ApiKey.user_id == user.id
    ).order_by(models.ApiKey.created_at.desc()).all()


@router.delete("/apikeys/{key_id}")
def delete_api_key(
    key_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)
    if not user:
        raise HTTPException(401, "Not authenticated")

    api_key = db.query(models.ApiKey).filter(
        models.ApiKey.id == key_id,
        models.ApiKey.user_id == user.id,
    ).first()
    if not api_key:
        raise HTTPException(404, "API key not found")

    record_audit(
        db, "delete", "user", user.id, user.username,
        resource_type="api_key", resource_id=key_id,
    )
    db.delete(api_key)
    db.commit()
    return {"status": "deleted"}
