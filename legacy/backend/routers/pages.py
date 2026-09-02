from datetime import datetime, date
import calendar
import markdown
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from sqlalchemy.orm import Session
from sqlalchemy import or_

from .. import models, auth
from ..database import get_db
from ..services.dashboard import get_dashboard
from ..services.analytics import get_analytics
from ..services import similar_memories, embeddings_available
from ..services.event_service import get_events, get_event_stats
from ..services.agent_service import get_agent_status
from ..utils.date_formatter import to_kanji_date
from ..utils.tags import parse_tags

router = APIRouter()

templates_path = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "templates",
)

templates = Jinja2Templates(directory=templates_path)
templates.env.filters["kanji_date"] = to_kanji_date
templates.env.filters["parse_tags"] = parse_tags

MOOD_EMOJIS = {
    "Happy": "😊",
    "Calm": "😌",
    "Neutral": "😐",
    "Thoughtful": "🤔",
    "Motivated": "🔥",
    "Tired": "😴",
    "Sad": "😔",
    "Angry": "😡",
}


def mood_display(mood: str | None) -> str:
    if not mood:
        return ""
    emoji = MOOD_EMOJIS.get(mood, "")
    return f"{emoji} {mood}" if emoji else mood


templates.env.filters["mood_display"] = mood_display


def _get_user_context(request: Request, db: Session) -> dict:
    user = auth.get_current_user(request, db)
    return {
        "current_user": user,
        "auth_enabled": auth.AUTH_ENABLED,
        "is_admin": user and user.role == "admin",
        "is_read_only": user and user.role == "read-only",
    }


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, registered: bool = False):
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "request": request,
            "auth_enabled": auth.AUTH_ENABLED,
            "registered": registered,
            "ambience": False,
        },
    )


@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="register.html",
        context={
            "request": request,
            "auth_enabled": auth.AUTH_ENABLED,
            "allow_registration": auth.ALLOW_REGISTRATION,
            "ambience": False,
        },
    )


@router.get("/verify-email", response_class=HTMLResponse)
def verify_email_page(
    request: Request,
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)
    if auth.AUTH_ENABLED and not user:
        return RedirectResponse(url="/login")

    ctx = _get_user_context(request, db)
    ctx.update({"request": request})
    return templates.TemplateResponse(
        request=request, name="verify_email.html", context=ctx
    )


@router.get("/change-password", response_class=HTMLResponse)
def change_password_page(
    request: Request,
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)
    if auth.AUTH_ENABLED and not user:
        return RedirectResponse(url="/login")

    ctx = _get_user_context(request, db)
    ctx.update({"request": request})
    return templates.TemplateResponse(
        request=request, name="change_password.html", context=ctx
    )


@router.get("/", response_class=HTMLResponse)
def read_root(
    request: Request,
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)
    if auth.AUTH_ENABLED and not user:
        return RedirectResponse(url="/login")

    dashboard = get_dashboard(db, user)

    recent_entries = dashboard["entries"][:5]
    latest_entry = dashboard["latest"]
    total_entries = dashboard["total"]
    top_tags = dashboard["top_tags"]

    is_admin = user is not None and user.role == "admin"
    event_stats = get_event_stats(db) if is_admin else {"last_24h": 0, "total": 0, "by_source": {}}
    agent_status = get_agent_status(db) if is_admin else {}
    recent_events = get_events(db, limit=5) if is_admin else []

    now = datetime.now()

    if now.hour < 12:
        greeting = "Good Morning"
    elif now.hour < 18:
        greeting = "Good Afternoon"
    else:
        greeting = "Good Evening"

    ctx = _get_user_context(request, db)
    ctx.update({
        "request": request,
        "dashboard": dashboard,
        "entries": recent_entries,
        "latest_entry": latest_entry,
        "total_entries": total_entries,
        "top_tags": top_tags,
        "greeting": greeting,
        "today": now.strftime("%A, %d %B %Y"),
        "quote": "Waste no more time arguing what a good person should be. Be one.",
        "event_stats": event_stats,
        "agent_status": agent_status,
        "recent_events": recent_events,
    })
    return templates.TemplateResponse(request=request, name="index.html", context=ctx)


@router.get("/write", response_class=HTMLResponse)
def write_page(
    request: Request,
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)
    if auth.AUTH_ENABLED and not user:
        return RedirectResponse(url="/login")
    if user and user.role == "read-only":
        raise HTTPException(403, "Read-only users cannot write")

    ctx = _get_user_context(request, db)
    ctx.update({"request": request, "now": datetime.now()})
    return templates.TemplateResponse(request=request, name="write.html", context=ctx)


@router.get("/entry/{entry_id}", response_class=HTMLResponse)
def entry_page(
    request: Request,
    entry_id: int,
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)

    entry = db.query(models.Entry).filter(models.Entry.id == entry_id).first()

    if entry is None:
        raise HTTPException(status_code=404, detail="Entry not found")

    if not auth.check_visibility(entry.visibility, user, entry.user_id):
        raise HTTPException(403, "Access denied")

    if auth.AUTH_ENABLED and not user and entry.visibility != "public":
        return RedirectResponse(url="/login")

    content_html = markdown.markdown(entry.content)

    quotes = [
        "The wars are over. Yet my heart still draws its sword.",
        "We are but fading embers in a long, dark winter.",
        "What is remembered, lives. What is forgotten, finds peace.",
        "A falling blossom returns not to the branch.",
        "To endure is the greatest of disciplines.",
    ]

    quote = quotes[entry.id % len(quotes)]

    similar = similar_memories(entry_id, db, user=user)

    related = []
    entry_tags = set(parse_tags(entry.tags))
    if entry_tags:
        related_q = (
            db.query(models.Entry)
            .filter(models.Entry.id != entry_id)
            .order_by(models.Entry.created_at.desc())
            .all()
        )
        for r in related_q:
            if not auth.check_visibility(r.visibility, user, r.user_id):
                continue
            r_tags = set(parse_tags(r.tags))
            if r_tags & entry_tags:
                related.append(r)
                if len(related) >= 5:
                    break

    entry_entity_links = db.query(models.EntryEntity).filter(
        models.EntryEntity.entry_id == entry_id
    ).all()
    entity_ids = [eel.entity_id for eel in entry_entity_links]
    entry_entities = (
        db.query(models.Entity)
        .filter(models.Entity.id.in_(entity_ids))
        .all() if entity_ids else []
    )

    ctx = _get_user_context(request, db)
    ctx.update({
        "request": request,
        "entry": entry,
        "content_html": content_html,
        "quote": quote,
        "similar_entries": similar,
        "related_entries": related,
        "entry_entities": entry_entities,
        "embeddings_available": embeddings_available(),
    })
    return templates.TemplateResponse(request=request, name="entry.html", context=ctx)


@router.get("/timeline", response_class=HTMLResponse)
def timeline_page(
    request: Request,
    db: Session = Depends(get_db),
    q: str | None = None,
    mood: str | None = None,
    entity: str | None = None,
    tag: str | None = None,
    source: str | None = None,
    show_events: bool = False,
):
    user = auth.get_current_user(request, db)
    if auth.AUTH_ENABLED and not user:
        return RedirectResponse(url="/login")

    query = db.query(models.Entry)

    if q:
        query = query.filter(
            or_(
                models.Entry.title.contains(q),
                models.Entry.content.contains(q),
                models.Entry.entry_type.contains(q),
                models.Entry.tags.contains(q),
                models.Entry.mood.contains(q),
            )
        )

    if mood:
        query = query.filter(models.Entry.mood == mood)
    if tag:
        query = query.filter(models.Entry.tags.contains(tag))
    if source:
        query = query.filter(models.Entry.source == source)

    if entity:
        entity_obj = db.query(models.Entity).filter(
            models.Entity.name == entity
        ).first()
        if entity_obj:
            entry_ids = [
                ee.entry_id
                for ee in db.query(models.EntryEntity)
                .filter(models.EntryEntity.entity_id == entity_obj.id)
                .all()
            ]
            if entry_ids:
                query = query.filter(models.Entry.id.in_(entry_ids))
            else:
                query = query.filter(False)

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

    events = []
    if show_events or not q:
        event_q = db.query(models.Event)
        if q:
            event_q = event_q.filter(
                or_(
                    models.Event.title.contains(q),
                    models.Event.description.contains(q),
                )
            )
        if source and source in ("watcher", "hermes", "sentinel", "phoenix", "system"):
            event_q = event_q.filter(models.Event.source == source)
        events = event_q.order_by(models.Event.created_at.desc()).limit(50).all()

    all_moods = [
        "Happy", "Calm", "Neutral", "Thoughtful",
        "Motivated", "Tired", "Sad", "Angry",
    ]

    all_entities = db.query(models.Entity).order_by(models.Entity.name.asc()).all()

    tag_counts: dict[str, int] = {}
    for e in db.query(models.Entry).all():
        for t in parse_tags(e.tags):
            tag_counts[t] = tag_counts.get(t, 0) + 1
    all_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)

    all_sources = [
        ("journal", "Journal"),
        ("watcher", "Watcher"),
        ("hermes", "Hermes"),
        ("sentinel", "Sentinel"),
        ("phoenix", "Phoenix"),
        ("memory", "Memory"),
        ("system", "System"),
    ]

    ctx = _get_user_context(request, db)
    ctx.update({
        "request": request,
        "entries": entries,
        "events": events,
        "q": q,
        "filter_mood": mood,
        "filter_entity": entity,
        "filter_tag": tag,
        "filter_source": source,
        "show_events": show_events,
        "all_moods": all_moods,
        "all_entities": all_entities,
        "all_tags": all_tags,
        "all_sources": all_sources,
    })
    return templates.TemplateResponse(request=request, name="timeline.html", context=ctx)


@router.get("/calendar", response_class=HTMLResponse)
def calendar_page(
    request: Request,
    db: Session = Depends(get_db),
    year: int | None = None,
    month: int | None = None,
    day: int | None = None,
):
    user = auth.get_current_user(request, db)
    if auth.AUTH_ENABLED and not user:
        return RedirectResponse(url="/login")

    today = datetime.now()
    year = year or today.year
    month = month or today.month

    start_date = date(year, month, 1)
    if month == 12:
        end_date = date(year + 1, 1, 1)
    else:
        end_date = date(year, month + 1, 1)

    base_query = db.query(models.Entry).filter(
        models.Entry.created_at >= start_date,
        models.Entry.created_at < end_date,
    )

    if user and user.role != "admin":
        base_query = base_query.filter(
            or_(
                models.Entry.visibility == "public",
                models.Entry.user_id == user.id,
            )
        )
    elif not user:
        base_query = base_query.filter(models.Entry.visibility == "public")

    month_entries = base_query.order_by(models.Entry.created_at.asc()).all()

    entries_by_day: dict[int, list] = {}
    for e in month_entries:
        day_num = e.created_at.day
        if day_num not in entries_by_day:
            entries_by_day[day_num] = []
        entries_by_day[day_num].append(e)

    first_weekday = start_date.weekday()
    first_weekday = (first_weekday + 1) % 7

    num_days = calendar.monthrange(year, month)[1]

    days_data = []
    for d in range(1, num_days + 1):
        days_data.append({
            "day": d,
            "entries": entries_by_day.get(d, []),
            "has_entries": d in entries_by_day,
            "is_today": (
                year == today.year and month == today.month and d == today.day
            ),
        })

    grid = []
    week: list = [None] * first_weekday
    for dd in days_data:
        week.append(dd)
        if len(week) == 7:
            grid.append(week)
            week = []
    if week:
        week.extend([None] * (7 - len(week)))
        grid.append(week)

    selected_entries = entries_by_day.get(day, []) if day else []

    prev_month = month - 1
    prev_year = year
    if prev_month < 1:
        prev_month = 12
        prev_year -= 1

    next_month = month + 1
    next_year = year
    if next_month > 12:
        next_month = 1
        next_year += 1

    month_names = [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    ]

    ctx = _get_user_context(request, db)
    ctx.update({
        "request": request,
        "grid": grid,
        "year": year,
        "month": month,
        "month_name": month_names[month - 1],
        "prev_month": prev_month,
        "prev_year": prev_year,
        "next_month": next_month,
        "next_year": next_year,
        "selected_day": day,
        "selected_entries": selected_entries,
    })
    return templates.TemplateResponse(request=request, name="calendar.html", context=ctx)


@router.get("/analytics", response_class=HTMLResponse)
def analytics_page(
    request: Request,
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)
    if auth.AUTH_ENABLED and not user:
        return RedirectResponse(url="/login")

    data = get_analytics(db, user)

    ctx = _get_user_context(request, db)
    ctx.update({"request": request, "data": data})
    return templates.TemplateResponse(request=request, name="analytics.html", context=ctx)


@router.get("/graph", response_class=HTMLResponse)
def graph_page(
    request: Request,
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)
    if auth.AUTH_ENABLED and not user:
        return RedirectResponse(url="/login")

    ctx = _get_user_context(request, db)
    ctx.update({"request": request})
    return templates.TemplateResponse(request=request, name="graph.html", context=ctx)


@router.get("/collections", response_class=HTMLResponse)
def collections_page(
    request: Request,
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)
    if auth.AUTH_ENABLED and not user:
        return RedirectResponse(url="/login")

    collections = db.query(models.Collection).order_by(models.Collection.name.asc()).all()

    collection_data = []
    for c in collections:
        count = db.query(models.CollectionEntry).filter(
            models.CollectionEntry.collection_id == c.id
        ).count()
        collection_data.append({
            "id": c.id,
            "name": c.name,
            "description": c.description,
            "entry_count": count,
        })

    tag_counts: dict[str, int] = {}
    for e in db.query(models.Entry).all():
        for t in parse_tags(e.tags):
            tag_counts[t] = tag_counts.get(t, 0) + 1
    auto_tags = [t for t, c in sorted(tag_counts.items(), key=lambda x: x[1], reverse=True) if c > 0]

    ctx = _get_user_context(request, db)
    ctx.update({
        "request": request,
        "collections": collection_data,
        "auto_tags": auto_tags,
    })
    return templates.TemplateResponse(request=request, name="collections.html", context=ctx)


@router.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request,
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)
    if auth.AUTH_ENABLED and not user:
        return RedirectResponse(url="/login")

    settings_rows = db.query(models.Setting).all()
    settings_dict = {s.key: s.value for s in settings_rows}

    ctx = _get_user_context(request, db)
    ctx.update({
        "request": request,
        "settings": settings_dict,
        "embeddings_available": embeddings_available(),
    })
    return templates.TemplateResponse(request=request, name="settings.html", context=ctx)


@router.get("/admin", response_class=HTMLResponse)
def admin_page(
    request: Request,
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)
    if auth.AUTH_ENABLED and not user:
        return RedirectResponse(url="/login")
    if not user or user.role != "admin":
        return RedirectResponse(url="/")

    ctx = _get_user_context(request, db)
    ctx.update({"request": request})
    return templates.TemplateResponse(request=request, name="admin.html", context=ctx)
