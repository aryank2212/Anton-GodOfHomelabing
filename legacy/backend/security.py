import ipaddress
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from .config import config


class RateLimiter:
    """Simple in-memory sliding-window rate limiter (per-process)."""

    def __init__(self):
        self._buckets: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str, max_hits: int, window_seconds: int) -> int:
        """Record a hit. Returns 0 if allowed, or seconds to wait if blocked."""
        now = time.monotonic()
        with self._lock:
            bucket = self._buckets[key]
            while bucket and bucket[0] <= now - window_seconds:
                bucket.popleft()
            if len(bucket) >= max_hits:
                retry_after = int(window_seconds - (now - bucket[0])) + 1
                return max(retry_after, 1)
            bucket.append(now)
            return 0

    def clear(self, key: str) -> None:
        with self._lock:
            self._buckets.pop(key, None)

    def clear_all(self) -> None:
        with self._lock:
            self._buckets.clear()


rate_limiter = RateLimiter()


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


def _rl() -> dict:
    return config.security.get("rate_limits", {})


def make_rate_limit(key_fn, max_hits: int, window: int):
    def dependency(request: Request):
        retry = rate_limiter.hit(key_fn(request), max_hits, window)
        if retry:
            raise HTTPException(
                429,
                f"Too many requests. Try again in {retry}s.",
                headers={"Retry-After": str(retry)},
            )

    return dependency


def login_ip_rate_limit():
    limits = _rl()
    return make_rate_limit(
        lambda r: f"login_ip:{client_ip(r)}",
        limits.get("login_ip_max", 30),
        limits.get("login_ip_window", 900),
    )


def check_login_username_rate(request: Request, username: str) -> None:
    limits = _rl()
    retry = rate_limiter.hit(
        f"login_user:{client_ip(request)}:{username}",
        limits.get("login_max", 5),
        limits.get("login_window", 900),
    )
    if retry:
        raise HTTPException(
            429,
            f"Too many login attempts. Try again in {retry}s.",
            headers={"Retry-After": str(retry)},
        )


def register_rate_limit():
    limits = _rl()
    return make_rate_limit(
        lambda r: f"register:{client_ip(r)}",
        limits.get("register_max", 5),
        limits.get("register_window", 3600),
    )


def otp_send_rate_limit():
    limits = _rl()
    return make_rate_limit(
        lambda r: f"otp_send:{client_ip(r)}",
        limits.get("otp_send_max", 5),
        limits.get("otp_send_window", 3600),
    )


def otp_verify_rate_limit():
    limits = _rl()
    return make_rate_limit(
        lambda r: f"otp_verify:{client_ip(r)}",
        limits.get("otp_verify_max", 10),
        limits.get("otp_verify_window", 900),
    )


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-XSS-Protection": "1; mode=block",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "connect-src 'self'"
    ),
}


def apply_security_headers(response) -> None:
    for header, value in SECURITY_HEADERS.items():
        response.headers.setdefault(header, value)


def _is_trusted_host(host: str) -> bool:
    host = host.split(":")[0].strip().lower()
    trusted = config.security.get("trusted_hosts", [])
    if host in {h.lower() for h in trusted}:
        return True
    if host == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        return False


def is_trusted_host(host: str) -> bool:
    return _is_trusted_host(host)


def validate_host_header(request: Request) -> None:
    host = request.headers.get("host", "")
    if not host or not is_trusted_host(host):
        raise HTTPException(400, "Invalid Host header")
