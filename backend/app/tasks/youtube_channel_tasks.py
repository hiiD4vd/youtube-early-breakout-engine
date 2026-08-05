import logging

from celery import Task
from sqlalchemy import select

from app.database import SessionLocal
from app.models.youtube_snipe import YoutubeSnipe
from app.services.channel_context import fetch_channel_context
from app.services.seed_store import SeedStore
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name="app.tasks.youtube_channel_tasks.enrich_youtube_channel_context", soft_time_limit=180, time_limit=210)
def enrich_youtube_channel_context(self: Task, video_id: str) -> dict[str, str]:
    with SessionLocal() as db:
        row = db.scalar(select(YoutubeSnipe).where(YoutubeSnipe.video_id == video_id))
        if not row:
            return {"status": "missing_signal"}
        existing = (row.raw_metadata or {}).get("channel_context")
        if existing:
            return {"status": "cached", "context": existing.get("status", "UNKNOWN")}
        context = fetch_channel_context(row.channel_id, row.video_url)
        metadata = dict(row.raw_metadata or {})
        metadata["channel_context"] = context
        row.raw_metadata = metadata
        db.commit()
    SeedStore().record_report(**{f"channel_context_{context['status'].lower()}": 1})
    return {"status": "saved", "context": context["status"]}
