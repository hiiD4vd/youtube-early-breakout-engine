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


def _save_row_status(video_id: str, *, media_status: str | None = None, enrichment_status: str | None = None, reason: str | None = None, metadata: dict | None = None) -> None:
    with SessionLocal() as db:
        row = db.scalar(select(YoutubeSnipe).where(YoutubeSnipe.video_id == video_id))
        if not row:
            return
        if media_status is not None:
            row.media_status = media_status
        if enrichment_status is not None:
            row.enrichment_status = enrichment_status
        row.processing_reason = reason
        if metadata is not None:
            merged = dict(row.raw_metadata or {})
            merged["enrichment"] = metadata
            row.raw_metadata = merged
        db.commit()


@celery_app.task(bind=True, name="app.tasks.youtube_enrichment_tasks.enrich_youtube_breakout", autoretry_for=(RuntimeError,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def enrich_youtube_breakout(self: Task, video_id: str) -> dict[str, str]:
    """Enrich a BREAKOUT honestly: visual+text when possible, text-only when not."""
    store = SeedStore()
    pending = store.get_pending_breakout(video_id)
    if not pending:
        return {"status": "missing_pending"}
    media_state = pending.get("media_state")
    if media_state not in {"ready_for_enrichment", "heatmap_unavailable"}:
        return {"status": "media_not_ready"}
    stages = dict(pending.get("stages") or {})
    stages["heatmap"] = "ready" if media_state == "ready_for_enrichment" else "unavailable"
    stages["frame"] = "ready" if pending.get("peak_frame_path") else "unavailable"
    pending["enrichment_state"] = "transcript_checking"
    pending["stages"] = stages
    store.save_pending_breakout(pending)
    _save_row_status(video_id, media_status=media_state, enrichment_status="transcript_checking", reason=None, metadata={"state": pending["enrichment_state"], "stages": stages})
    try:
        seed = pending["seed"]
        transcript = fetch_transcript(seed["video_url"])
        stages["transcript"] = "ready" if transcript else "unavailable"
        pending["transcript_state"] = stages["transcript"]
        if not transcript and not pending.get("peak_frame_path"):
            pending["enrichment_state"] = "insufficient_evidence"
            pending["stages"] = stages
            store.save_pending_breakout(pending)
            _save_row_status(video_id, media_status=media_state, enrichment_status="insufficient_evidence", reason="heatmap_and_transcript_unavailable", metadata={"state": pending["enrichment_state"], "stages": stages})
            return {"status": "insufficient_evidence"}
        if not settings.gemini_api_key:
            pending["enrichment_state"] = "gemini_not_configured"
            pending["stages"] = stages
            store.save_pending_breakout(pending)
            _save_row_status(video_id, media_status=media_state, enrichment_status="gemini_not_configured", reason="gemini_not_configured", metadata={"state": pending["enrichment_state"], "stages": stages})
            return {"status": "gemini_not_configured"}
        stages["ai"] = "analyzing"
        pending["enrichment_state"] = "ai_analyzing"
        pending["stages"] = stages
        store.save_pending_breakout(pending)
        _save_row_status(video_id, media_status=media_state, enrichment_status="ai_analyzing", reason=None, metadata={"state": pending["enrichment_state"], "stages": stages})
        facts = GeminiClient().analyze(pending.get("peak_frame_path"), transcript)
        stages["ai"] = "completed"
        mode = "visual_and_text" if pending.get("peak_frame_path") else "text_only"
        values = {"channel_id": seed["channel_id"], "channel_title": seed.get("channel_title"), "title": seed.get("title"), "video_url": seed["video_url"], "thumbnail_url": seed.get("thumbnail_url"), "published_at": datetime.fromisoformat(seed["published_at"]), "initial_view_count": seed["seed_view_count"], "current_view_count": pending["current_view_count"], "velocity_per_hour": pending["velocity_per_hour"], "breakout_score": pending["velocity_per_hour"], "peak_timestamp_seconds": pending.get("peak_timestamp_seconds"), "peak_frame_path": pending.get("peak_frame_path"), "transcript": transcript, "niche": facts.niche, "visual_facts": {"facts": facts.visual_facts}, "ai_analysis": {**facts.model_dump(), "mode": mode}, "processing_status": "enriched", "media_status": media_state, "enrichment_status": "completed", "processing_reason": None}
        with SessionLocal() as db:
            row = db.scalar(select(YoutubeSnipe).where(YoutubeSnipe.video_id == video_id))
            if row:
                for key, value in values.items():
                    setattr(row, key, value)
                merged = dict(row.raw_metadata or {})
                merged["enrichment"] = {"state": "completed", "mode": mode, "stages": stages}
                row.raw_metadata = merged
            else:
                db.add(YoutubeSnipe(video_id=video_id, raw_metadata={"enrichment": {"state": "completed", "mode": mode, "stages": stages}}, **values))
            db.commit()
        pending.update({"enrichment_state": "completed", "enrichment_mode": mode, "stages": stages})
        store.save_pending_breakout(pending)
        store.record_report(enrichment_completed=1, transcript_available=1 if transcript else 0, enrichment_text_only=1 if mode == "text_only" else 0)
        return {"status": "saved", "video_id": video_id}
    except Exception as exc:
        store.record_report(enrichment_errors=1)
        pending["enrichment_state"] = "retry_scheduled"
        pending["last_enrichment_error"] = str(exc)[:300]
        pending["stages"] = stages
        store.save_pending_breakout(pending)
        _save_row_status(video_id, media_status=media_state, enrichment_status="retry_scheduled", reason="enrichment_failed", metadata={"state": "retry_scheduled", "stages": stages, "error": str(exc)[:300]})
        raise
