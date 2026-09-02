"""Auth for Argus' command surface.

Read-only endpoints stay open on the LAN. Mutating operations (starting or
cancelling investigations, research sessions, dot-watch management) require a
bearer token — the ``ARGUS_COMMAND_TOKEN`` configured on the server. When no
token is configured, commands remain open (LAN development default).
"""

from __future__ import annotations

from fastapi import HTTPException, Request, status


def require_command_token(request: Request) -> None:
    """Dependency enforcing the command token on mutating routes.

    When ``ARGUS_COMMAND_TOKEN`` is unset, accepts any request (development
    default). Otherwise it must match ``Authorization: Bearer <token>``.
    """
    runtime = getattr(request.app.state, "runtime", None)
    settings = getattr(runtime, "settings", None)
    expected = getattr(settings, "command_token", None)
    if not expected:
        return
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if token != expected:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="invalid command token",
        )
