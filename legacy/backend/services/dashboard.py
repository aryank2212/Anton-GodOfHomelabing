from datetime import datetime, timedelta
from collections import Counter
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import or_
from .. import models
from ..utils.tags import parse_tags, top_tags_from_entries
from ..auth import check_visibility
from .streaks import get_current_streak, get_longest_streak


def get_dashboard(db: Session, user: Optional[models.User] = None):

    query = db.query(models.Entry)
    if user and user.role != "admin":
        query = query.filter(
            or_(
                models.Entry.visibility == "public",
                models.Entry.user_id == user.id,
            )
        )
    elif not user:
        query = query.filter(models.Entry.visibility == "public")

    entries = query.order_by(models.Entry.created_at.desc()).all()

    total = len(entries)
    latest = entries[0] if entries else None

    journal = len([e for e in entries if e.entry_type == "Journal"])
    dreams = len([e for e in entries if e.entry_type == "Dream"])
    ideas = len([e for e in entries if e.entry_type == "Idea"])
    reflections = len([e for e in entries if e.entry_type == "Reflection"])

    words_written = sum(len(e.content.split()) for e in entries)

    today = datetime.now().date()
    week_start = today - timedelta(days=today.weekday())
    entries_this_week = sum(1 for e in entries if e.created_at.date() >= week_start)

    mood_counts = Counter(e.mood for e in entries if e.mood)
    most_used_mood = mood_counts.most_common(1)[0] if mood_counts else None

    tag_counts: dict[str, int] = {}
    for e in entries:
        for tag in parse_tags(e.tags):
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
    most_used_tag = (
        sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)[0]
        if tag_counts else None
    )

    return {
        "entries": entries,
        "latest": latest,
        "total": total,
        "journal": journal,
        "dreams": dreams,
        "ideas": ideas,
        "reflections": reflections,
        "top_tags": top_tags_from_entries(entries),
        "current_streak": get_current_streak(db, user),
        "longest_streak": get_longest_streak(db),
        "words_written": words_written,
        "entries_this_week": entries_this_week,
        "most_used_mood": most_used_mood,
        "most_used_tag": most_used_tag,
    }
