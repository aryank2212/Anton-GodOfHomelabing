import hashlib
import hmac
import secrets
import smtplib
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .. import models
from ..config import config
from ..logging_config import get_logger

logger = get_logger("legacy.otp")


def _settings() -> dict:
    otp = config.otp
    return {
        "length": otp.get("length", 6),
        "ttl_seconds": otp.get("ttl_seconds", 600),
        "max_attempts": otp.get("max_attempts", 5),
        "resend_cooldown_seconds": otp.get("resend_cooldown_seconds", 60),
    }


def _email_config() -> dict:
    return config.email


def generate_code(length: int = 6) -> str:
    return f"{secrets.randbelow(10 ** length):0{length}d}"


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _mask_email(email: str) -> str:
    if "@" not in email:
        return email[:2] + "***" if len(email) > 2 else "***"
    local, _, domain = email.partition("@")
    visible = local[:2] if len(local) > 2 else local[:1]
    return f"{visible}***@{domain}"


def send_email(to: str, subject: str, text: str, html: Optional[str] = None) -> str:
    cfg = _email_config()
    method = cfg.get("send_method", "console")
    from_name = cfg.get("from_name", "LEGACY")
    from_email = cfg.get("from_email", "noreply@redclove.space")

    if method == "smtp":
        _send_smtp(to, subject, text, html, cfg, from_name, from_email)
        return "smtp"

    logger.info(
        f"[CONSOLE EMAIL] To={to} Subject={subject!r}\n{text}"
    )
    return "console"


def _send_smtp(
    to: str,
    subject: str,
    text: str,
    html: Optional[str],
    cfg: dict,
    from_name: str,
    from_email: str,
):
    host = cfg.get("smtp_host", "")
    if not host:
        raise HTTPException(500, "SMTP not configured")

    port = int(cfg.get("smtp_port", 587))
    user = cfg.get("smtp_user", "")
    password = cfg.get("smtp_password", "")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{from_email}>"
    msg["To"] = to
    msg.attach(MIMEText(text, "plain"))
    if html:
        msg.attach(MIMEText(html, "html"))

    if cfg.get("use_ssl"):
        server = smtplib.SMTP_SSL(host, port, timeout=15)
    else:
        server = smtplib.SMTP(host, port, timeout=15)
        if cfg.get("use_tls"):
            server.starttls()

    try:
        if user:
            server.login(user, password)
        server.sendmail(from_email, [to], msg.as_string())
    finally:
        server.quit()


def _latest_active_code(
    db: Session, user_id: int, purpose: str
) -> Optional[models.OtpCode]:
    return (
        db.query(models.OtpCode)
        .filter(
            models.OtpCode.user_id == user_id,
            models.OtpCode.purpose == purpose,
            models.OtpCode.used_at.is_(None),
            models.OtpCode.expires_at > datetime.now(timezone.utc),
        )
        .order_by(models.OtpCode.created_at.desc())
        .first()
    )


def _active_codes(
    db: Session, user_id: int, purpose: str
) -> list[models.OtpCode]:
    return (
        db.query(models.OtpCode)
        .filter(
            models.OtpCode.user_id == user_id,
            models.OtpCode.purpose == purpose,
            models.OtpCode.used_at.is_(None),
            models.OtpCode.expires_at > datetime.now(timezone.utc),
        )
        .order_by(models.OtpCode.created_at.desc())
        .all()
    )


def send_otp(
    db: Session,
    user: models.User,
    purpose: str = "verify_email",
) -> dict:
    if not _email_config().get("enabled", True):
        raise HTTPException(503, "Email service is disabled")

    if not user.email:
        raise HTTPException(400, "No email address on this account")

    settings = _settings()
    cooldown = settings["resend_cooldown_seconds"]
    now = datetime.now(timezone.utc)

    active = _active_codes(db, user.id, purpose)
    if active:
        newest = active[0]
        created = newest.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        elapsed = (now - created).total_seconds()
        if elapsed < cooldown:
            raise HTTPException(
                429,
                f"Please wait {int(cooldown - elapsed)}s before requesting another code",
            )

    for old in active:
        old.expires_at = now
    db.commit()

    code = generate_code(settings["length"])
    expires_at = now + timedelta(seconds=settings["ttl_seconds"])

    otp = models.OtpCode(
        user_id=user.id,
        email=user.email,
        purpose=purpose,
        code_hash=_hash_code(code),
        max_attempts=settings["max_attempts"],
        expires_at=expires_at,
    )
    db.add(otp)
    db.commit()

    subject = f"Your {config.app.get('name', 'LEGACY')} verification code"
    text = (
        f"Your verification code is: {code}\n\n"
        f"This code expires in {settings['ttl_seconds'] // 60} minutes.\n"
        f"If you did not request this, you can safely ignore this email."
    )
    html = (
        f"<p>Your verification code is:</p>"
        f"<h2 style='letter-spacing:0.3em;'>{code}</h2>"
        f"<p>This code expires in {settings['ttl_seconds'] // 60} minutes.</p>"
    )

    method = send_email(user.email, subject, text, html)
    logger.info(
        f"OTP sent user_id={user.id} purpose={purpose} method={method} "
        f"to={_mask_email(user.email)}"
    )

    return {
        "sent": True,
        "email": _mask_email(user.email),
        "expires_in": settings["ttl_seconds"],
        "resend_in": cooldown,
        "method": method,
    }


def verify_otp(
    db: Session,
    user: models.User,
    code: str,
    purpose: str = "verify_email",
) -> dict:
    code = code.strip()
    if not code:
        raise HTTPException(400, "Code is required")

    settings = _settings()
    otp = _latest_active_code(db, user.id, purpose)
    if not otp:
        raise HTTPException(400, "No valid code. Request a new one.")

    expected = _hash_code(code)
    if hmac.compare_digest(expected, otp.code_hash):
        otp.used_at = datetime.now(timezone.utc)
        if purpose == "verify_email" and user.email == otp.email:
            user.email_verified = 1
        db.commit()
        logger.info(
            f"OTP verified user_id={user.id} purpose={purpose} "
            f"to={_mask_email(otp.email)}"
        )
        return {"verified": True, "email_verified": bool(user.email_verified)}

    otp.attempts += 1
    if otp.attempts >= otp.max_attempts:
        otp.expires_at = datetime.now(timezone.utc)
        db.commit()
        raise HTTPException(429, "Too many failed attempts. Request a new code.")
    db.commit()
    raise HTTPException(400, "Incorrect code")


def cleanup_expired_otps(db: Session) -> int:
    count = db.query(models.OtpCode).filter(
        models.OtpCode.expires_at <= datetime.now(timezone.utc)
    ).delete()
    db.commit()
    return count
