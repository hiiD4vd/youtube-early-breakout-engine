import logging
from datetime import UTC, datetime

from celery import Task
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models.youtube_snipe import YoutubeSnipe
from app.services.peak_frame import PeakFrameError, PeakFrameExtractor, classify_media_error
from app.services.seed_store import SeedStore
from app.services.signal_scoring import SCORING_VERSION, age_bucket, interval_velocities, score_tier
from app.services.velocity import calculate_velocity
from app.services.youtube_client import YoutubeAnonymousClient
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name="app.tasks.youtube_velocity_tasks.check_youtube_seed_velocity", soft_time_limit=900, time_limit=960)
def check_youtube_seed_velocity(self: Task) -> dict[str, int]:
    """Poll fresh seeds at an age-appropriate cadence, then score their momentum."""
    store = SeedStore()
    if not store.acquire_velocity_lock(settings.youtube_velocity_lock_seconds):
        logger.info("YouTube velocity check skipped: an earlier run still holds the lock")
        return {"status": "skipped_locked"}
    client = YoutubeAnonymousClient()
    scanned = eligible = breakouts = media_failures = 0
    try:
        now = datetime.now(UTC)
        for video_id in store.list_ids():
            scanned += 1
            seed = store.get(video_id)
            if not seed:
                continue
            age_hours = (now - seed.published_at).total_seconds() / 3600
            if age_hours > settings.youtube_seed_max_age_hours:
                store.remove(video_id)
                continue
            snapshots = store.snapshots(video_id)
            last_observed_at = datetime.fromisoformat(snapshots[-1]["observed_at"]) if snapshots else seed.seeded_at
            if age_hours < settings.youtube_ultra_fresh_max_age_hours:
                poll_minutes = settings.youtube_ultra_fresh_poll_minutes
            elif age_hours < settings.youtube_fast_poll_max_age_hours:
                poll_minutes = settings.youtube_fast_poll_minutes
            else:
                poll_minutes = settings.youtube_mature_poll_minutes
            if (now - last_observed_at).total_seconds() < poll_minutes * 60:
                continue
            metadata = client.fetch_current_metadata(video_id)
            current_views = metadata.get("view_count")
            if not isinstance(current_views, int):
                continue
            signal = calculate_velocity(seed.seed_view_count, current_views, seed.seeded_at, now, settings.youtube_breakout_min_view_delta, settings.youtube_breakout_min_velocity_per_hour)
            snapshots = store.append_snapshot(video_id, now, current_views)
            bucket = age_bucket(seed.published_at, now)
            intervals_before = interval_velocities(seed.seed_view_count, seed.seeded_at, snapshots)
            latest_interval_velocity = intervals_before[-1] if intervals_before else 0.0
            relative_percentile = store.velocity_percentile(bucket, latest_interval_velocity, settings.youtube_relative_min_samples)
            store.add_velocity_sample(bucket, now, latest_interval_velocity)
            tier, score, acceleration, intervals = score_tier(
                seed.seed_view_count, seed.seeded_at, snapshots, bucket,
                settings.youtube_early_min_velocity_per_hour,
                settings.youtube_rising_min_velocity_per_hour,
                settings.youtube_breakout_min_velocity_per_hour,
                relative_percentile=relative_percentile,
                relative_enabled=settings.youtube_relative_scoring_enabled,
                relative_early=settings.youtube_relative_early_percentile,
                relative_rising=settings.youtube_relative_rising_percentile,
                relative_breakout=settings.youtube_relative_breakout_percentile,
            )
            store.record_tier_transition(video_id, tier)
            store.record_report(velocity_observations=1)
            audit = {
                "seeded_at": seed.seeded_at.isoformat(), "view_delta": signal.view_delta,
                "age_bucket": bucket, "snapshot_count": len(snapshots),
                "interval_velocities": intervals, "acceleration": acceleration,
                "relative_percentile": relative_percentile, "scoring_version": SCORING_VERSION,
            }
            if tier in {"WATCH", "COOLED"}:
                # Existing rows from an earlier score are demoted rather than
                # left visible after the stricter evidence rules take effect.
                with SessionLocal() as db:
                    row = db.scalar(select(YoutubeSnipe).where(YoutubeSnipe.video_id == video_id))
                    if row:
                        row.current_view_count = current_views
                        row.velocity_per_hour = signal.velocity_per_hour
                        row.signal_tier = tier
                        row.signal_score = score
                        row.processing_status = "watching" if tier == "WATCH" else "cooled"
                        row.processing_reason = "insufficient_observations" if tier == "WATCH" else "momentum_cooled"
                        row.raw_metadata = audit
                        db.commit()
                continue
            eligible += 1
            # VTR is the primary outward signal. Persist it immediately so the
            # dashboard is useful even when optional media/AI enrichment fails.
            with SessionLocal() as db:
                row = db.scalar(select(YoutubeSnipe).where(YoutubeSnipe.video_id == video_id))
                values = {"channel_id": seed.channel_id, "channel_title": seed.channel_title, "title": seed.title, "video_url": seed.video_url, "thumbnail_url": seed.thumbnail_url, "published_at": seed.published_at, "initial_view_count": seed.seed_view_count, "current_view_count": current_views, "velocity_per_hour": signal.velocity_per_hour, "breakout_score": signal.velocity_per_hour, "signal_tier": tier, "signal_score": score, "processing_status": "signal_detected", "media_status": "pending", "enrichment_status": "pending", "processing_reason": None, "raw_metadata": audit}
                if row:
                    for key, value in values.items(): setattr(row, key, value)
                else:
                    db.add(YoutubeSnipe(video_id=video_id, **values))
                db.commit()
            # Heavy media/AI work stays reserved for confirmed BREAKOUT only.
            if tier != "BREAKOUT" or not store.acquire_breakout_lock(video_id):
                continue
            previous = store.get_pending_breakout(video_id) or {}
            if previous.get("media_state") == "media_unavailable":
                continue
            attempt = int(previous.get("media_attempt", 0)) + 1
            pending = {
                "video_id": video_id, "seed": seed.model_dump(mode="json"),
                "current_view_count": current_views, "view_delta": signal.view_delta,
                "velocity_per_hour": signal.velocity_per_hour, "validated_at": now.isoformat(),
                "media_state": "pending_media", "media_attempt": attempt,
                "last_media_error": None, "next_retry_at": None,
            }
            store.save_pending_breakout(pending)
            try:
                peak_seconds, frame_path = PeakFrameExtractor().extract(video_id, seed.video_url)
                pending.update({"media_state": "ready_for_enrichment", "peak_timestamp_seconds": peak_seconds, "peak_frame_path": frame_path})
                store.save_pending_breakout(pending)
                with SessionLocal() as db:
                    row = db.scalar(select(YoutubeSnipe).where(YoutubeSnipe.video_id == video_id))
                    if row:
                        row.media_status = "ready"; row.peak_timestamp_seconds = peak_seconds; row.peak_frame_path = frame_path
                        db.commit()
                from app.tasks.youtube_enrichment_tasks import enrich_youtube_breakout
                enrich_youtube_breakout.delay(video_id)
                breakouts += 1
            except PeakFrameError as exc:
                media_failures += 1
                store.record_report(media_errors=1)
                reason = classify_media_error(exc)
                pending.update({"media_state": "media_unavailable" if attempt >= settings.youtube_media_max_attempts else "retry_scheduled", "last_media_error": reason, "last_media_error_detail": str(exc)[:300], "next_retry_at": None if attempt >= settings.youtube_media_max_attempts else (now + __import__("datetime").timedelta(hours=2)).isoformat()})
                store.save_pending_breakout(pending)
                with SessionLocal() as db:
                    row = db.scalar(select(YoutubeSnipe).where(YoutubeSnipe.video_id == video_id))
                    if row:
                        row.media_status = pending["media_state"]; row.processing_reason = reason
                        db.commit()
                logger.warning("Breakout %s media extraction %s (attempt %s): %s", video_id, reason, attempt, exc)
        store.set_status(last_velocity_scan_at=now.isoformat(), last_velocity_scanned=scanned, last_velocity_eligible=eligible, last_velocity_breakouts=breakouts, last_media_failures=media_failures)
        return {"scanned": scanned, "eligible": eligible, "breakouts": breakouts, "media_failures": media_failures}
    finally:
        client.close()
        store.release_velocity_lock()
