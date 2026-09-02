from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models, schemas, auth
from ..services.knowledge_service import search_knowledge, get_context, generate_summary
from ..services.rag_service import build_rag_context
from ..services.audit_service import record_audit

router = APIRouter(prefix="/api/knowledge", tags=["Knowledge"])


@router.get("/search")
def api_knowledge_search(
    request: Request,
    q: str = Query(""),
    sources: str = Query(None),
    limit: int = Query(20),
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)
    auth.enforce_role(user, ["admin", "user", "agent", "read-only"])

    source_list = sources.split(",") if sources else None
    results = search_knowledge(db, q=q, sources=source_list, limit=limit, user=user)

    record_audit(
        db, "search", "user" if user else "anonymous",
        actor_id=user.id if user else None,
        resource_type="knowledge",
        details={"query": q, "results": len(results)},
    )

    return results


@router.get("/context")
def api_knowledge_context(
    request: Request,
    topics: str = Query(""),
    max_entries: int = Query(5),
    max_events: int = Query(3),
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)
    auth.enforce_role(user, ["admin", "user", "agent"])

    if not topics.strip():
        raise HTTPException(400, "topics parameter is required")

    topic_list = [t.strip() for t in topics.split(",") if t.strip()]
    context = get_context(db, topic_list, max_entries, max_events, user=user)
    return {"context": context}


@router.get("/rag")
def api_knowledge_rag(
    request: Request,
    query: str = Query(""),
    max_entries: int = Query(5),
    max_events: int = Query(3),
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)
    auth.enforce_role(user, ["admin", "user", "agent"])

    if not query.strip():
        raise HTTPException(400, "query parameter is required")

    context = build_rag_context(db, query, max_entries, max_events, user=user)
    return {"query": query, "context": context}


@router.get("/summary")
def api_knowledge_summary(
    request: Request,
    entry_ids: str = Query(""),
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)
    auth.enforce_role(user, ["admin", "user", "agent"])

    if not entry_ids.strip():
        raise HTTPException(400, "entry_ids parameter is required (comma-separated)")

    ids = []
    for s in entry_ids.split(","):
        try:
            ids.append(int(s.strip()))
        except ValueError:
            raise HTTPException(400, f"Invalid entry ID: {s}")

    summary = generate_summary(db, ids)
    return {"summary": summary, "entry_count": len(ids)}
