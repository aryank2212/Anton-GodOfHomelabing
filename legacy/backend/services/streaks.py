from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import func, or_
from .. import models


def get_current_streak(
    db: Session,
    user: Optional[models.User] = None,
) -> int:
    today = datetime.now().date()

    query = db.query(func.date(models.Entry.created_at)).distinct()
    if user and user.role != "admin":
        query = query.filter(
            or_(
                models.Entry.visibility == "public",
                models.Entry.user_id == user.id,
            )
        )
    elif not user:
        query = query.filter(models.Entry.visibility == "public")

    rows = query.order_by(func.date(models.Entry.created_at).desc()).all()

    dates = {row[0] for row in rows}

    streak = 0
    check = today
    while check in dates:
        streak += 1
        check -= timedelta(days=1)

    return streak


def get_longest_streak(db: Session) -> int:
    rows = (
        db.query(func.date(models.Entry.created_at))
        .distinct()
        .order_by(func.date(models.Entry.created_at).asc())
        .all()
    )

    date_strings = [row[0] for row in rows]

    if not date_strings:
        return 0

    dates = [datetime.strptime(d, "%Y-%m-%d").date() for d in date_strings]

    longest = 1
    current = 1

    for i in range(1, len(dates)):
        if (dates[i] - dates[i - 1]).days == 1:
            current += 1
        else:
            longest = max(longest, current)
            current = 1

    return max(longest, current)
