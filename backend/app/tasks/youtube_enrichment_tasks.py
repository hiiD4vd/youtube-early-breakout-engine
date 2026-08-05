import logging
from datetime import datetime

from celery import Task
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models.youtube_snipe import YoutubeSnipe
from app.services.gemini_client import GeminiClient
from app.services.seed_store import SeedStore
from app.services.transcript import fetch_transcript
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name="app.tasks.youtube_enrichment_tasks.enrich_youtube_breakout", autoretry_for=(RuntimeError,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def enrich_youtube_breakout(self: Task, video_id: str) -> dict[str, str]:
    store = SeedStore()
    pending = store.get_pending_breakout(video_id)
    if not pending:
        return {"status": "missing_pending"}
    if pending.get("media_state") != "ready_for_enrichment" or not pending.get("peak_frame_path"):
        return {"status": "media_not_ready"}
    if not settings.gemini_api_key:
        logger.warning("Gemini is not configured; pending breakout %s retained", video_id)
        return {"status": "gemini_not_configured"}
    try:
        seed = pending["seed"]
        transcript = fetch_transcript(seed["video_url"])
        facts = GeminiClient().analyze(pending["peak_frame_path"], transcript)
        values = {"channel_id": seed["channel_id"], "channel_title": seed.get("channel_title"), "title": seed.get("title"), "video_url": seed["video_url"], "thumbnail_url": seed.get("thumbnail_url"), "published_at": datetime.fromisoformat(seed["published_at"]), "initial_view_count": seed["seed_view_count"], "current_view_count": pending["current_view_count"], "velocity_per_hour": pending["velocity_per_hour"], "breakout_score": pending["velocity_per_hour"], "peak_timestamp_seconds": pending["peak_timestamp_seconds"], "peak_frame_path": pending["peak_frame_path"], "transcript": transcript, "niche": facts.niche, "visual_facts": {"facts": facts.visual_facts}, "ai_analysis": facts.model_dump(), "raw_metadata": pending, "processing_status": "enriched", "media_status": "ready", "enrichment_status": "completed", "processing_reason": None}
        with SessionLocal() as db:
            row = db.scalar(select(YoutubeSnipe).where(YoutubeSnipe.video_id == video_id))
            if row:
                for key, value in values.items(): setattr(row, key, value)
            else:
                db.add(YoutubeSnipe(video_id=video_id, **values))
            db.commit()
        pending["enrichment_state"] = "completed"
        store.save_pending_breakout(pending)
        store.record_report(enrichment_completed=1, transcript_available=1 if transcript else 0)
        return {"status": "saved", "video_id": video_id}
    except Exception as exc:
        store.record_report(enrichment_errors=1)
        pending["enrichment_state"] = "retry_scheduled"
        pending["last_enrichment_error"] = str(exc)[:300]
        store.save_pending_breakout(pending)
        with SessionLocal() as db:
            row = db.scalar(select(YoutubeSnipe).where(YoutubeSnipe.video_id == video_id))
            if row:
                row.enrichment_status = "retry_scheduled"
                row.processing_reason = "enrichment_failed"
                db.commit()
        raise
