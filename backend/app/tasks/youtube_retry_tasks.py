"""Retry enrichment work without relying on a seed still being alive in Redis."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from celery import Task
from sqlalchemy import select

from app.config import settings
from app.core.redis_keys import ENRICHMENT_RETRY_LOCK_KEY
from app.database import SessionLocal
from app.models.youtube_snipe import YoutubeSnipe
from app.services.peak_frame import PeakFrameError, PeakFrameExtractor, classify_media_error
from app.services.seed_store import SeedStore
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def _is_due(value: str | None, now: datetime) -> bool:
    if not value:
        return True
    try:
        return datetime.fromisoformat(value).astimezone(UTC) <= now
    except (TypeError, ValueError):
        return True


def _update_row(video_id: str, media_status: str, reason: str | None, pending: dict) -> None:
    with SessionLocal() as db:
        row = db.scalar(select(YoutubeSnipe).where(YoutubeSnipe.video_id == video_id))
        if row:
            row.media_status = media_status
            row.processing_reason = reason
            metadata = dict(row.raw_metadata or {})
            metadata["enrichment"] = {"state": pending.get("enrichment_state", "pending"), "stages": pending.get("stages", {}), "last_media_error": pending.get("last_media_error")}
            row.raw_metadata = metadata
            db.commit()


@celery_app.task(bind=True, name="app.tasks.youtube_retry_tasks.retry_pending_enrichment", soft_time_limit=300, time_limit=360)
def retry_pending_enrichment(self: Task) -> dict[str, int | str]:
    store = SeedStore()
    if not store.client.set(ENRICHMENT_RETRY_LOCK_KEY, "1", nx=True, ex=settings.youtube_enrichment_retry_lock_seconds):
        return {"status": "skipped_locked"}
    queued = 0
    try:
        now = datetime.now(UTC)
        for pending in store.iter_pending_breakouts():
            video_id = pending.get("video_id")
            if not video_id:
                continue
            if pending.get("media_state") == "retry_scheduled" and _is_due(pending.get("next_retry_at"), now):
                retry_youtube_breakout_media.delay(video_id)
                queued += 1
            elif pending.get("enrichment_state") == "retry_scheduled" and _is_due(pending.get("next_enrichment_retry_at"), now):
                from app.tasks.youtube_enrichment_tasks import enrich_youtube_breakout
                enrich_youtube_breakout.delay(video_id)
                queued += 1
        store.set_status(last_enrichment_retry_scan_at=now.isoformat(), last_enrichment_retries_queued=queued)
        return {"queued": queued}
    finally:
        store.client.delete(ENRICHMENT_RETRY_LOCK_KEY)


@celery_app.task(bind=True, name="app.tasks.youtube_retry_tasks.retry_youtube_breakout_media", soft_time_limit=420, time_limit=480)
def retry_youtube_breakout_media(self: Task, video_id: str) -> dict[str, str]:
    store = SeedStore()
    pending = store.get_pending_breakout(video_id)
    if not pending or pending.get("media_state") != "retry_scheduled":
        return {"status": "not_retryable"}
    now = datetime.now(UTC)
    if not _is_due(pending.get("next_retry_at"), now):
        return {"status": "not_due"}
    attempt = int(pending.get("media_attempt", 0)) + 1
    pending.update({"media_state": "pending_media", "media_attempt": attempt, "next_retry_at": None})
    store.save_pending_breakout(pending)
    try:
        peak_seconds, frame_path = PeakFrameExtractor().extract(video_id, pending["seed"]["video_url"])
        pending.update({"media_state": "ready_for_enrichment", "peak_timestamp_seconds": peak_seconds, "peak_frame_path": frame_path, "stages": {"heatmap": "ready", "frame": "ready", "transcript": "pending", "ai": "pending"}})
        store.save_pending_breakout(pending)
        _update_row(video_id, "ready", None, pending)
        with SessionLocal() as db:
            row = db.scalar(select(YoutubeSnipe).where(YoutubeSnipe.video_id == video_id))
            if row:
                row.peak_timestamp_seconds = peak_seconds
                row.peak_frame_path = frame_path
                db.commit()
        from app.tasks.youtube_enrichment_tasks import enrich_youtube_breakout
        enrich_youtube_breakout.delay(video_id)
        return {"status": "media_ready"}
    except PeakFrameError as exc:
        reason = classify_media_error(exc)
        if reason == "heatmap_unavailable":
            pending.update({"media_state": "heatmap_unavailable", "last_media_error": reason, "last_media_error_detail": str(exc)[:300], "next_retry_at": None, "stages": {"heatmap": "unavailable", "frame": "unavailable", "transcript": "pending", "ai": "pending"}})
            store.save_pending_breakout(pending)
            _update_row(video_id, "heatmap_unavailable", reason, pending)
            from app.tasks.youtube_enrichment_tasks import enrich_youtube_breakout
            enrich_youtube_breakout.delay(video_id)
            return {"status": "heatmap_unavailable"}
        exhausted = attempt >= settings.youtube_media_max_attempts
        pending.update({"media_state": "media_unavailable" if exhausted else "retry_scheduled", "last_media_error": reason, "last_media_error_detail": str(exc)[:300], "next_retry_at": None if exhausted else (now + timedelta(hours=2)).isoformat()})
        store.save_pending_breakout(pending)
        _update_row(video_id, pending["media_state"], reason, pending)
        store.record_report(media_errors=1, media_retries=0 if exhausted else 1)
        return {"status": pending["media_state"]}
