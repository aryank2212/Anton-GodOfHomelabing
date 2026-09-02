from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.services.approvals import ApprovalBridge

router = APIRouter(tags=["approvals"])


class ApprovalRequest(BaseModel):
    """A Forge approval request: the action text to relay to the operator."""

    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    text: str = Field(min_length=1, max_length=2000)


@router.post(
    "/v1/approvals",
    summary="Accept a Forge approval request and relay it to Telegram",
)
async def create_approval(payload: ApprovalRequest, request: Request) -> JSONResponse:
    """Store the pending approval and message the operator over Telegram.

    Returns 202 when the message was delivered (Forge fails closed otherwise)
    and 503 when the bridge is disabled or has no chat to send to.
    """
    bridge: ApprovalBridge = request.app.state.approvals
    if not bridge.enabled:
        return JSONResponse(
            status_code=503, content={"ok": False, "detail": "approval bridge disabled"}
        )
    delivered = await bridge.request_approval(payload.id, payload.text)
    if not delivered:
        return JSONResponse(
            status_code=503, content={"ok": False, "detail": "no operator chat configured"}
        )
    return JSONResponse(status_code=202, content={"ok": True, "id": payload.id})
