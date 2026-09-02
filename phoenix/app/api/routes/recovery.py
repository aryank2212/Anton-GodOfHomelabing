from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from app.models.incident import Incident

router = APIRouter(tags=["recovery"])


@router.post(
    "/recover/{component}",
    response_model=Incident,
    summary="Manually trigger recovery for a component",
)
async def recover_component(component: str, request: Request) -> Incident:
    """Run the recovery workflow for ``component`` and return the incident.

    Recovery (including retries, backoff and dependency cascade) runs
    synchronously; the response contains the final incident state.
    """
    orchestrator = request.app.state.orchestrator
    incident = await orchestrator.recover_component(component)
    if incident is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown component: {component}",
        )
    return incident
