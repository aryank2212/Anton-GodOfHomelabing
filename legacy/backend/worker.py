import threading
import time
import json
from datetime import datetime, timedelta

from .database import SessionLocal
from . import models


class BackgroundWorker:
    def __init__(self):
        self.running = False
        self._threads: list[threading.Thread] = []

    def start(self):
        if self.running:
            return
        self.running = True

        self._threads.append(threading.Thread(target=self._embedding_loop, daemon=True))
        self._threads.append(threading.Thread(target=self._reflection_loop, daemon=True))
        self._threads.append(threading.Thread(target=self._cleanup_loop, daemon=True))

        for t in self._threads:
            t.start()

    def stop(self):
        self.running = False

    def _embedding_loop(self):
        while self.running:
            try:
                db = SessionLocal()
                try:
                    from .services.embedding_service import generate_embedding, parse_embedding

                    unembedded = db.query(models.Entry).filter(
                        models.Entry.embedding.is_(None)
                    ).limit(10).all()

                    for entry in unembedded:
                        if not self.running:
                            break
                        text = f"{entry.title or ''} {entry.content}"
                        emb = generate_embedding(text)
                        if emb:
                            entry.embedding = emb
                            db.commit()
                finally:
                    db.close()
            except Exception:
                pass

            for _ in range(60):
                if not self.running:
                    return
                time.sleep(1)

    def _reflection_loop(self):
        while self.running:
            try:
                db = SessionLocal()
                try:
                    now = datetime.utcnow()
                    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

                    if now.hour == 23 and now.minute < 5:
                        existing = db.query(models.Entry).filter(
                            models.Entry.title.contains("Daily Reflection"),
                            models.Entry.created_at >= today_start,
                        ).first()

                        if not existing:
                            entries_today = db.query(models.Entry).filter(
                                models.Entry.created_at >= today_start,
                            ).count()

                            if entries_today > 0:
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
                finally:
                    db.close()
            except Exception:
                pass

            for _ in range(3600):
                if not self.running:
                    return
                time.sleep(1)

    def _cleanup_loop(self):
        while self.running:
            try:
                db = SessionLocal()
                try:
                    _, _ = db, models
                finally:
                    db.close()
            except Exception:
                pass

            for _ in range(36000):
                if not self.running:
                    return
                time.sleep(1)


worker = BackgroundWorker()
