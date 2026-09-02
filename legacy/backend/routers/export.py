from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session
from typing import Optional

from ..database import get_db
from .. import models, schemas, auth
from ..services.backup_service import export_entries, import_entries
from ..services.audit_service import record_audit

router = APIRouter(prefix="/api/export", tags=["Export/Import"])


@router.get("/export")
def export(
    request: Request,
    db: Session = Depends(get_db),
    fmt: str = Query("json", pattern="^(json|csv|markdown)$"),
    entry_ids: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    include_embeddings: bool = Query(False),
):
    user = auth.get_current_user(request, db)
    auth.enforce_role(user, ["admin", "user", "agent"])

    ids = [int(x) for x in entry_ids.split(",")] if entry_ids else None

    content, fmt_out = export_entries(
        db, fmt=fmt, entry_ids=ids, source=source,
        start_date=start_date, end_date=end_date,
        include_embeddings=include_embeddings,
    )

    content_type_map = {
        "json": "application/json",
        "csv": "text/csv",
        "markdown": "text/markdown",
    }

    ext_map = {"json": "json", "csv": "csv", "markdown": "md"}

    record_audit(
        db, "export", "user" if user else "anonymous",
        actor_id=user.id if user else None,
        actor_name=user.username if user else None,
        resource_type="entry",
        details={"format": fmt},
    )

    return Response(
        content=content,
        media_type=content_type_map.get(fmt, "text/plain"),
        headers={
            "Content-Disposition": f'attachment; filename="legacy_export.{ext_map.get(fmt, "json")}"'
        },
    )


@router.post("/import", response_model=schemas.ImportResult)
async def import_data(
    request: Request,
    db: Session = Depends(get_db),
    fmt: str = Query("json", pattern="^(json|csv)$"),
):
    user = auth.get_current_user(request, db)
    auth.enforce_role(user, ["admin", "user", "agent"])

    body = await request.body()
    content = body.decode("utf-8")

    result = import_entries(db, content, fmt, user_id=user.id if user else None)

    record_audit(
        db, "import", "user" if user else "anonymous",
        actor_id=user.id if user else None,
        actor_name=user.username if user else None,
        resource_type="entry",
        details=result,
    )

    return result
