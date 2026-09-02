import json
import time
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional, Callable
from queue import PriorityQueue
from sqlalchemy.orm import Session

from ..database import SessionLocal
from .. import models
from ..config import config
from ..logging_config import get_logger

logger = get_logger("legacy.queue")

JOB_TYPES = {
    "embedding": "Generate embedding for entry",
    "reflection": "Generate daily reflection",
    "entity_extraction": "Extract entities from content",
    "summary": "Generate summary for entry",
    "reindex": "Re-index entries",
    "backup": "Create backup",
    "cleanup": "Cleanup old data",
    "session_cleanup": "Cleanup expired sessions",
}


class JobQueue:
    def __init__(self):
        self._queue: PriorityQueue = PriorityQueue()
        self._running = False
        self._threads: list[threading.Thread] = []
        self._active_jobs: dict[int, threading.Thread] = {}
        self._lock = threading.Lock()

    def start(self):
        if self._running:
            return
        self._running = True
        max_workers = config.queue.get("max_workers", 4)
        for i in range(max_workers):
            t = threading.Thread(target=self._worker_loop, daemon=True, name=f"queue-{i}")
            self._threads.append(t)
            t.start()
        logger.info(f"Queue started with {max_workers} workers")

    def stop(self):
        self._running = False
        logger.info("Queue stopping...")

    def enqueue(
        self,
        job_type: str,
        payload: Optional[dict] = None,
        priority: int = 0,
        scheduled_at: Optional[datetime] = None,
        max_retries: int = 3,
    ) -> int:
        db = SessionLocal()
        try:
            job = models.Job(
                job_type=job_type,
                status="pending",
                priority=priority,
                payload=json.dumps(payload) if payload else None,
                max_retries=max_retries,
                scheduled_at=scheduled_at,
            )
            db.add(job)
            db.commit()
            db.refresh(job)
            self._queue.put((-priority, job.id, job.job_type))
            logger.debug(f"Job {job.id} enqueued: {job_type}")
            return job.id
        finally:
            db.close()

    def _worker_loop(self):
        while self._running:
            try:
                if not self._queue.empty():
                    _, job_id, job_type = self._queue.get(timeout=1)
                    self._process_job(job_id, job_type)
                else:
                    self._process_pending_jobs()
                    time.sleep(config.queue.get("poll_interval", 1))
            except Exception:
                time.sleep(1)

    def _process_pending_jobs(self):
        db = SessionLocal()
        try:
            now = datetime.now(timezone.utc)
            pending = (
                db.query(models.Job)
                .filter(
                    models.Job.status == "pending",
                    models.Job.scheduled_at.is_(None) | (models.Job.scheduled_at <= now),
                )
                .order_by(models.Job.priority.desc(), models.Job.created_at.asc())
                .limit(10)
                .all()
            )
            for job in pending:
                job.status = "queued"
            db.commit()
            for job in pending:
                self._queue.put((-job.priority, job.id, job.job_type))
        finally:
            db.close()

    def _process_job(self, job_id: int, job_type: str):
        db = SessionLocal()
        try:
            job = db.query(models.Job).filter(models.Job.id == job_id).first()
            if not job or job.status == "completed":
                return

            job.status = "running"
            job.started_at = datetime.now(timezone.utc)
            db.commit()

            payload = json.loads(job.payload) if job.payload else {}
            logger.info(f"Processing job {job_id}: {job_type}")

            try:
                result = self._execute_job(job_type, payload, db)
                job.status = "completed"
                job.result = json.dumps(result) if result else None
                job.completed_at = datetime.now(timezone.utc)
                db.commit()
                logger.info(f"Job {job_id} completed: {job_type}")
            except Exception as e:
                job.retry_count = (job.retry_count or 0) + 1
                job.error = str(e)
                if job.retry_count >= job.max_retries:
                    job.status = "failed"
                    logger.error(f"Job {job_id} failed permanently: {e}")
                else:
                    job.status = "pending"
                    job.scheduled_at = datetime.now(timezone.utc) + timedelta(
                        seconds=config.queue.get("retry_delay", 60)
                    )
                    logger.warning(f"Job {job_id} failed (retry {job.retry_count}/{job.max_retries}): {e}")
                db.commit()
        finally:
            db.close()

    def _execute_job(self, job_type: str, payload: dict, db: Session) -> Optional[dict]:
        if job_type == "embedding":
            return self._run_embedding(payload, db)
        elif job_type == "reflection":
            return self._run_reflection(payload, db)
        elif job_type == "entity_extraction":
            return self._run_entity_extraction(payload, db)
        elif job_type == "summary":
            return self._run_summary(payload, db)
        elif job_type == "reindex":
            return self._run_reindex(payload, db)
        elif job_type == "cleanup":
            return self._run_cleanup(payload, db)
        elif job_type == "session_cleanup":
            return self._run_session_cleanup(payload, db)
        else:
            raise ValueError(f"Unknown job type: {job_type}")

    def _run_embedding(self, payload: dict, db: Session) -> dict:
        entry_id = payload.get("entry_id")
        if entry_id:
            entries = db.query(models.Entry).filter(models.Entry.id == entry_id).all()
        else:
            entries = (
                db.query(models.Entry)
                .filter(models.Entry.embedding.is_(None))
                .limit(payload.get("batch_size", 10))
                .all()
            )

        from ..services.embedding_service import generate_embedding

        count = 0
        for entry in entries:
            text = f"{entry.title or ''} {entry.content}"
            emb = generate_embedding(text)
            if emb:
                entry.embedding = emb
                db.commit()
                count += 1
        return {"entries_embedded": count}

    def _run_reflection(self, payload: dict, db: Session) -> dict:
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        existing = db.query(models.Entry).filter(
            models.Entry.title.contains("Daily Reflection"),
            models.Entry.created_at >= today_start,
        ).first()

        if existing:
            return {"reflection_created": False, "reason": "already_exists"}

        entries_today = db.query(models.Entry).filter(
            models.Entry.created_at >= today_start,
        ).count()

        if entries_today == 0:
            return {"reflection_created": False, "reason": "no_entries"}

        summary_content = f"Daily Reflection - {now.strftime('%Y-%m-%d')}\n\n"
        summary_content += f"{entries_today} entries were recorded today."

        entry = models.Entry(
            title=f"Daily Reflection - {now.strftime('%Y-%m-%d')}",
            content=summary_content,
            entry_type="Journal",
            source="system",
            visibility="private",
        )
        db.add(entry)
        db.commit()

        from ..services.audit_service import record_audit
        record_audit(
            db, "create", "system", resource_type="entry",
            resource_id=entry.id, details={"type": "reflection"},
        )
        return {"reflection_created": True, "entry_id": entry.id}

    def _run_entity_extraction(self, payload: dict, db: Session) -> dict:
        entry_id = payload.get("entry_id")
        if not entry_id:
            return {"error": "no_entry_id"}
        entry = db.query(models.Entry).filter(models.Entry.id == entry_id).first()
        if not entry:
            return {"error": "entry_not_found"}

        from ..services.entity_service import extract_entities

        found = extract_entities(entry.content)
        count = 0
        for entity_data in found:
            entity = db.query(models.Entity).filter(
                models.Entity.name == entity_data["name"]
            ).first()
            if not entity:
                entity = models.Entity(
                    name=entity_data["name"],
                    entity_type=entity_data["entity_type"],
                )
                db.add(entity)
                db.flush()

            existing = db.query(models.EntryEntity).filter(
                models.EntryEntity.entry_id == entry.id,
                models.EntryEntity.entity_id == entity.id,
            ).first()
            if not existing:
                db.add(models.EntryEntity(entry_id=entry.id, entity_id=entity.id))
                count += 1
        db.commit()
        return {"entities_found": len(found), "entities_added": count}

    def _run_summary(self, payload: dict, db: Session) -> dict:
        entry_id = payload.get("entry_id")
        if not entry_id:
            return {"error": "no_entry_id"}
        entry = db.query(models.Entry).filter(models.Entry.id == entry_id).first()
        if not entry:
            return {"error": "entry_not_found"}

        content = entry.content[:1000]
        words = content.split()
        if len(words) <= 50:
            summary = content
        else:
            summary = " ".join(words[:50]) + "..."

        entry.summary = summary
        db.commit()
        return {"summary": summary}

    def _run_reindex(self, payload: dict, db: Session) -> dict:
        from ..services.embedding_service import generate_embedding

        entries = db.query(models.Entry).all()
        count = 0
        for entry in entries:
            text = f"{entry.title or ''} {entry.content}"
            emb = generate_embedding(text)
            if emb:
                entry.embedding = emb
                db.commit()
                count += 1
        return {"entries_reindexed": count}

    def _run_cleanup(self, payload: dict, db: Session) -> dict:
        cutoff = datetime.now(timezone.utc) - timedelta(days=90)
        old_logs = (
            db.query(models.AuditLog)
            .filter(models.AuditLog.created_at < cutoff)
            .delete()
        )
        db.commit()
        return {"audit_logs_removed": old_logs}

    def _run_session_cleanup(self, payload: dict, db: Session) -> dict:
        from ..auth import cleanup_expired_sessions
        count = cleanup_expired_sessions(db)
        return {"expired_sessions_removed": count}

    def get_status(self) -> dict:
        db = SessionLocal()
        try:
            total = db.query(models.Job).count()
            pending = db.query(models.Job).filter(models.Job.status == "pending").count()
            running = db.query(models.Job).filter(models.Job.status == "running").count()
            completed = db.query(models.Job).filter(models.Job.status == "completed").count()
            failed = db.query(models.Job).filter(models.Job.status == "failed").count()
            queued = db.query(models.Job).filter(models.Job.status == "queued").count()

            return {
                "total": total,
                "pending": pending,
                "queued": queued,
                "running": running,
                "completed": completed,
                "failed": failed,
                "active_workers": len(self._threads),
            }
        finally:
            db.close()


queue = JobQueue()
