from datetime import datetime, timedelta
from collections import Counter
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import or_
from .. import models
from .streaks import get_current_streak, get_longest_streak


def get_analytics(db: Session, user: Optional[models.User] = None):
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

    entries = query.order_by(models.Entry.created_at.asc()).all()

    total = len(entries)

    if total == 0:
        return {
            "total_entries": 0,
            "words_written": 0,
            "average_words": 0,
            "average_per_day": 0,
            "longest_entry": 0,
            "shortest_entry": 0,
            "most_active_weekday": {"label": "N/A", "count": 0},
            "most_active_hour": {"label": 0, "count": 0},
            "entries_this_week": 0,
            "entries_this_month": 0,
            "mood_distribution": {},
            "entry_type_distribution": {},
            "monthly_activity": {"labels": [], "data": []},
            "hour_distribution": {},
            "current_streak": 0,
            "longest_streak": 0,
        }

    word_counts = [len(e.content.split()) for e in entries]

    today = datetime.now().date()
    first_entry = entries[0].created_at.date()
    days_span = max((today - first_entry).days, 1)

    weekday_counts = Counter(e.created_at.strftime("%A") for e in entries)
    most_active_weekday = weekday_counts.most_common(1)[0]

    hour_counts = Counter(e.created_at.hour for e in entries)
    most_active_hour = hour_counts.most_common(1)[0]

    week_start = today - timedelta(days=today.weekday())
    entries_this_week = sum(1 for e in entries if e.created_at.date() >= week_start)

    month_start = today.replace(day=1)
    entries_this_month = sum(1 for e in entries if e.created_at.date() >= month_start)

    mood_counts = Counter(e.mood for e in entries if e.mood)
    type_counts = Counter(e.entry_type for e in entries if e.entry_type)

    monthly = Counter(f"{e.created_at.year}-{e.created_at.month:02d}" for e in entries)
    monthly_labels = []
    monthly_data = []
    for i in range(11, -1, -1):
        d = today.replace(day=1) - timedelta(days=30 * i)
        label = d.strftime("%b %Y")
        key = d.strftime("%Y-%m")
        monthly_labels.append(label)
        monthly_data.append(monthly.get(key, 0))

    hour_distribution = {h: 0 for h in range(24)}
    for e in entries:
        hour_distribution[e.created_at.hour] = hour_distribution.get(e.created_at.hour, 0) + 1

    return {
        "total_entries": total,
        "words_written": sum(word_counts),
        "average_words": round(sum(word_counts) / total, 1),
        "average_per_day": round(total / days_span, 1),
        "longest_entry": max(word_counts),
        "shortest_entry": min(word_counts),
        "most_active_weekday": {"label": most_active_weekday[0], "count": most_active_weekday[1]},
        "most_active_hour": {"label": most_active_hour[0], "count": most_active_hour[1]},
        "entries_this_week": entries_this_week,
        "entries_this_month": entries_this_month,
        "mood_distribution": dict(mood_counts.most_common()),
        "entry_type_distribution": dict(type_counts.most_common()),
        "monthly_activity": {"labels": monthly_labels, "data": monthly_data},
        "hour_distribution": hour_distribution,
        "current_streak": get_current_streak(db, user),
        "longest_streak": get_longest_streak(db),
    }
