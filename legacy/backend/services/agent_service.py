from datetime import datetime
from sqlalchemy.orm import Session
from .. import models
from .memory_service import create_memory, search_memories, semantic_search, store_observation


AGENTS = ["watcher", "hermes", "sentinel", "phoenix", "training", "system"]


def read_memory(agent: str, db: Session, memory_id: int) -> dict | None:
    entry = db.query(models.Entry).filter(models.Entry.id == memory_id).first()
    if not entry:
        return None

    if entry.visibility == "agent-only" and entry.source != agent:
        return None

    return {
        "id": entry.id,
        "title": entry.title,
        "content": entry.content,
        "entry_type": entry.entry_type,
        "tags": entry.tags,
        "source": entry.source,
        "visibility": entry.visibility,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }


def write_memory(agent: str, db: Session, data: dict) -> models.Entry:
    data.setdefault("source", agent)
    return create_memory(db, data)


def search_memory(
    agent: str,
    db: Session,
    query: str,
    limit: int = 10,
) -> list[dict]:
    results = search_memories(db, q=query, limit=limit)

    semantic_results = semantic_search(db, query, limit=limit)
    seen_ids = {r["id"] for r in results}
    for r in semantic_results:
        if r["id"] not in seen_ids:
            results.append(r)

    return results[:limit]


def reflect(
    agent: str,
    db: Session,
    topic: str | None = None,
    content: str | None = None,
) -> dict:
    try:
        recent = db.query(models.Entry).filter(
            models.Entry.source == agent
        ).order_by(models.Entry.created_at.desc()).limit(5).all()

        if recent:
            summary_parts = [f"{e.title or 'Untitled'}: {e.content[:200]}" for e in recent]
            summary = "\n".join(summary_parts)
        else:
            summary = "No recent activity."

        reflection_text = f"[{agent.upper()} Reflection]\n"
        if topic:
            reflection_text += f"Topic: {topic}\n"
        if content:
            reflection_text += f"{content}\n"
        reflection_text += f"Recent context:\n{summary}"

        entry = store_observation(
            db,
            content=reflection_text,
            title=f"{agent.title()} Reflection - {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        )

        return {
            "id": entry.id,
            "title": entry.title,
            "content": entry.content,
            "created_at": entry.created_at.isoformat() if entry.created_at else None,
        }
    except Exception as e:
        return {"error": str(e)}


def get_agent_status(db: Session) -> dict:
    status = {}
    for agent in AGENTS:
        count = db.query(models.Entry).filter(
            models.Entry.source == agent
        ).count()
        recent = db.query(models.Entry).filter(
            models.Entry.source == agent
        ).order_by(models.Entry.created_at.desc()).first()
        status[agent] = {
            "memory_count": count,
            "last_active": recent.created_at.isoformat() if recent else None,
        }
    return status
