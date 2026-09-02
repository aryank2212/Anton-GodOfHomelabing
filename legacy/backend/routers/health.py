import time
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db, check_db_health
from ..config import config
from ..services.embedding_service import is_available as embeddings_available
from ..services.queue_service import queue

router = APIRouter(tags=["Health"])

_start_time = time.time()


@router.get("/health")
def health_check(db: Session = Depends(get_db)):
    db_health = check_db_health()
    emb_available = embeddings_available()

    return {
        "status": "healthy" if db_health["status"] == "healthy" else "degraded",
        "version": config.app.get("version", "3.0.0"),
        "database": db_health,
        "embedding": {
            "status": "available" if emb_available else "unavailable",
            "provider": config.embedding.get("provider", "sentence-transformers"),
            "model": config.embedding.get("model", "all-MiniLM-L6-v2"),
        } if emb_available else {
            "status": "unavailable",
            "provider": "none",
        },
        "uptime_seconds": round(time.time() - _start_time, 2),
    }


@router.get("/ready")
def ready_check(db: Session = Depends(get_db)):
    db_health = check_db_health()
    if db_health["status"] != "healthy":
        return {"status": "not_ready", "reason": "database_unavailable"}, 503

    return {
        "status": "ready",
        "version": config.app.get("version", "3.0.0"),
        "database": db_health["status"],
    }


@router.get("/live")
def live_check():
    return {
        "status": "alive",
        "uptime_seconds": round(time.time() - _start_time, 2),
    }


@router.get("/status")
def full_status(db: Session = Depends(get_db)):
    db_health = check_db_health()
    emb_available = embeddings_available()
    queue_status = queue.get_status()

    return {
        "status": "healthy" if db_health["status"] == "healthy" else "degraded",
        "version": config.app.get("version", "3.0.0"),
        "database": db_health,
        "embedding": {
            "available": emb_available,
            "provider": config.embedding.get("provider") if emb_available else None,
            "model": config.embedding.get("model") if emb_available else None,
        },
        "worker": {
            "active": len(queue._threads) if hasattr(queue, '_threads') else 0,
        },
        "queue": queue_status,
        "uptime_seconds": round(time.time() - _start_time, 2),
    }
