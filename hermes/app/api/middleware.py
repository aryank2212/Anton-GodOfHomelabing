from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.core.logging import request_id_var


class RequestIDMiddleware:
    """Assign a request id to every HTTP request.

    The id is stored in a context variable (so all logs emitted while the
    request is in flight carry it) and echoed back on the response headers.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = scope.get("headers") or []
        incoming = dict(headers).get(b"x-request-id")
        request_id = incoming.decode("ascii", "ignore") if incoming else uuid4().hex[:12]
        token = request_id_var.set(request_id)

        async def send_with_header(message: dict) -> None:
            if message["type"] == "http.response.start":
                message = {
                    **message,
                    "headers": [
                        *message.get("headers", []),
                        (b"x-request-id", request_id.encode()),
                    ],
                }
            await send(message)

        try:
            await self.app(scope, receive, send_with_header)
        finally:
            request_id_var.reset(token)
