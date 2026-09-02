import json
import os
import csv
import io
import hashlib
import zipfile
import shutil
from datetime import datetime
from typing import Optional
from pathlib import Path

from sqlalchemy.orm import Session
from .. import models
from ..config import config
from ..logging_config import get_logger

logger = get_logger("legacy.backup")

BACKUP_DIR = Path(config.backup.get("directory", "backups"))
MAX_BACKUPS = config.backup.get("max_backups", 30)


def _ensure_backup_dir():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)


def _checksum_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


# ============================================================
# Export
# ============================================================

def export_entries(
    db: Session,
    fmt: str = "json",
    entry_ids: Optional[list[int]] = None,
    source: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    include_embeddings: bool = False,
) -> tuple[str, str]:
    query = db.query(models.Entry)

    if entry_ids:
        query = query.filter(models.Entry.id.in_(entry_ids))
    if source:
        query = query.filter(models.Entry.source == source)
    if start_date:
        query = query.filter(models.Entry.created_at >= start_date)
    if end_date:
        query = query.filter(models.Entry.created_at <= end_date)

    entries = query.order_by(models.Entry.created_at.asc()).all()

    if fmt == "json":
        return _export_json(entries, include_embeddings)
    elif fmt == "csv":
        return _export_csv(entries)
    elif fmt == "markdown":
        return _export_markdown(entries)
    else:
        raise ValueError(f"Unsupported format: {fmt}")


def _export_json(entries: list, include_embeddings: bool) -> tuple[str, str]:
    data = []
    for e in entries:
        item = {
            "id": e.id,
            "title": e.title,
            "content": e.content,
            "entry_type": e.entry_type,
            "tags": e.tags,
            "mood": e.mood,
            "visibility": e.visibility,
            "source": e.source,
            "summary": e.summary,
            "created_at": e.created_at.isoformat() if e.created_at else None,
            "updated_at": e.updated_at.isoformat() if e.updated_at else None,
        }
        if include_embeddings:
            item["embedding"] = e.embedding
        data.append(item)

    content = json.dumps(data, indent=2, ensure_ascii=False)
    return content, "json"


def _export_csv(entries: list) -> tuple[str, str]:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "id", "title", "content", "entry_type", "tags", "mood",
        "visibility", "source", "created_at", "updated_at",
    ])
    for e in entries:
        writer.writerow([
            e.id, e.title, e.content, e.entry_type, e.tags, e.mood,
            e.visibility, e.source,
            e.created_at.isoformat() if e.created_at else "",
            e.updated_at.isoformat() if e.updated_at else "",
        ])
    return output.getvalue(), "csv"


def _export_markdown(entries: list) -> tuple[str, str]:
    parts = []
    for e in entries:
        date_str = e.created_at.strftime("%Y-%m-%d %H:%M") if e.created_at else "unknown"
        parts.append(f"# {e.title or 'Untitled'}")
        parts.append(f"*{date_str}* | *{e.entry_type}* | *{e.visibility}*")
        if e.tags:
            parts.append(f"Tags: {e.tags}")
        if e.mood:
            parts.append(f"Mood: {e.mood}")
        parts.append("")
        parts.append(e.content)
        parts.append("")
        parts.append("---")
        parts.append("")
    return "\n".join(parts), "md"


# ============================================================
# Import
# ============================================================

def import_entries(
    db: Session,
    content: str,
    fmt: str = "json",
    user_id: Optional[int] = None,
) -> dict:
    if fmt == "json":
        return _import_json(db, content, user_id)
    elif fmt == "csv":
        return _import_csv(db, content, user_id)
    else:
        raise ValueError(f"Import from {fmt} not supported")


def _import_json(db: Session, content: str, user_id: Optional[int]) -> dict:
    data = json.loads(content)
    if isinstance(data, dict):
        data = [data]

    imported = 0
    skipped = 0
    errors = []

    for item in data:
        try:
            existing = db.query(models.Entry).filter(
                models.Entry.id == item.get("id")
            ).first()
            if existing:
                skipped += 1
                continue

            entry = models.Entry(
                title=item.get("title"),
                content=item.get("content", ""),
                entry_type=item.get("entry_type", "Journal"),
                tags=item.get("tags"),
                mood=item.get("mood"),
                visibility=item.get("visibility", "private"),
                source=item.get("source", "import"),
                user_id=user_id,
            )
            db.add(entry)
            imported += 1
        except Exception as e:
            errors.append(str(e))

    db.commit()
    return {"imported": imported, "skipped": skipped, "errors": errors}


def _import_csv(db: Session, content: str, user_id: Optional[int]) -> dict:
    reader = csv.DictReader(io.StringIO(content))
    imported = 0
    skipped = 0
    errors = []

    for row in reader:
        try:
            entry = models.Entry(
                title=row.get("title"),
                content=row.get("content", ""),
                entry_type=row.get("entry_type", "Journal"),
                tags=row.get("tags"),
                mood=row.get("mood"),
                visibility=row.get("visibility", "private"),
                source=row.get("source", "import"),
                user_id=user_id,
            )
            db.add(entry)
            imported += 1
        except Exception as e:
            errors.append(str(e))

    db.commit()
    return {"imported": imported, "skipped": skipped, "errors": errors}


# ============================================================
# Backup
# ============================================================

def create_backup(db: Session, backup_type: str = "full") -> dict:
    _ensure_backup_dir()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"legacy_backup_{timestamp}_{backup_type}.legacy"
    backup_path = BACKUP_DIR / filename

    includes = ["database", "entries", "events", "settings"]

    with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as zf:
        entries_data = []
        for e in db.query(models.Entry).all():
            entries_data.append({
                "id": e.id,
                "title": e.title,
                "content": e.content,
                "entry_type": e.entry_type,
                "tags": e.tags,
                "mood": e.mood,
                "visibility": e.visibility,
                "source": e.source,
                "summary": e.summary,
                "embedding": e.embedding if backup_type == "full" else None,
                "created_at": e.created_at.isoformat() if e.created_at else None,
                "updated_at": e.updated_at.isoformat() if e.updated_at else None,
            })
        zf.writestr("entries.json", json.dumps(entries_data, indent=2))

        events_data = []
        for ev in db.query(models.Event).all():
            events_data.append({
                "id": ev.id,
                "source": ev.source,
                "event_type": ev.event_type,
                "severity": ev.severity,
                "title": ev.title,
                "description": ev.description,
                "created_at": ev.created_at.isoformat() if ev.created_at else None,
            })
        zf.writestr("events.json", json.dumps(events_data, indent=2))

        settings_data = {}
        for s in db.query(models.Setting).all():
            settings_data[s.key] = s.value
        zf.writestr("settings.json", json.dumps(settings_data, indent=2))

        metadata = {
            "version": "3.0.0",
            "created_at": timestamp,
            "type": backup_type,
            "entry_count": len(entries_data),
            "event_count": len(events_data),
        }
        zf.writestr("metadata.json", json.dumps(metadata, indent=2))

    size = backup_path.stat().st_size
    checksum = _checksum_file(backup_path)

    backup_record = models.Backup(
        filename=filename,
        size_bytes=size,
        checksum=checksum,
        backup_type=backup_type,
        includes=",".join(includes),
        status="completed",
    )
    db.add(backup_record)
    db.commit()

    _rotate_backups(db)

    logger.info(f"Backup created: {filename} ({size} bytes)")
    return {"filename": filename, "size_bytes": size, "checksum": checksum}


def _rotate_backups(db: Session):
    backups = (
        db.query(models.Backup)
        .order_by(models.Backup.created_at.desc())
        .all()
    )
    if len(backups) > MAX_BACKUPS:
        for old in backups[MAX_BACKUPS:]:
            try:
                (BACKUP_DIR / old.filename).unlink(missing_ok=True)
            except Exception:
                pass
            db.delete(old)
        db.commit()


def restore_backup(db: Session, filename: str, selective: Optional[list[str]] = None) -> dict:
    backup_path = BACKUP_DIR / filename
    if not backup_path.exists():
        raise FileNotFoundError(f"Backup not found: {filename}")

    checksum = _checksum_file(backup_path)
    backup_record = db.query(models.Backup).filter(
        models.Backup.filename == filename
    ).first()
    if backup_record and backup_record.checksum != checksum:
        raise ValueError("Backup checksum mismatch - file may be corrupted")

    restored = {"entries": 0, "events": 0, "settings": 0}

    with zipfile.ZipFile(backup_path, "r") as zf:
        if not selective or "entries" in selective:
            if "entries.json" in zf.namelist():
                data = json.loads(zf.read("entries.json"))
                for item in data:
                    existing = db.query(models.Entry).filter(
                        models.Entry.id == item["id"]
                    ).first()
                    if not existing:
                        entry = models.Entry(
                            id=item["id"],
                            title=item.get("title"),
                            content=item.get("content", ""),
                            entry_type=item.get("entry_type", "Journal"),
                            tags=item.get("tags"),
                            mood=item.get("mood"),
                            visibility=item.get("visibility", "private"),
                            source=item.get("source", "restored"),
                            summary=item.get("summary"),
                            embedding=item.get("embedding"),
                        )
                        db.add(entry)
                        restored["entries"] += 1
            db.flush()

        if not selective or "events" in selective:
            if "events.json" in zf.namelist():
                data = json.loads(zf.read("events.json"))
                for item in data:
                    ev = models.Event(
                        source=item.get("source", "restored"),
                        event_type=item.get("event_type", "restored"),
                        severity=item.get("severity", "info"),
                        title=item.get("title"),
                        description=item.get("description"),
                    )
                    db.add(ev)
                    restored["events"] += 1

        if not selective or "settings" in selective:
            if "settings.json" in zf.namelist():
                data = json.loads(zf.read("settings.json"))
                for key, value in data.items():
                    setting = db.query(models.Setting).filter(
                        models.Setting.key == key
                    ).first()
                    if not setting:
                        db.add(models.Setting(key=key, value=str(value)))
                    restored["settings"] += 1

    db.commit()
    logger.info(f"Restored from {filename}: {restored}")
    return restored


def list_backups(db: Session) -> list[models.Backup]:
    return (
        db.query(models.Backup)
        .order_by(models.Backup.created_at.desc())
        .all()
    )
