"""Apify intake for broad, non-keyword YouTube Shorts coverage.

The Actor is fed an independently sampled panel of channels already seen by
our public sources. It is an additional coverage lane, never a direct writer
to topics and never a keyword discovery mechanism.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
from celery import Task
from sqlalchemy import func, select

from app.config import settings
from app.database import SessionLocal
from app.models.market_trends import MarketSourceRun, MarketVideo, MarketVideoObservation
from app.services.apify_shorts_adapter import normalize_apify_short
from app.services.seed_store import SeedStore
from app.tasks.celery_app import celery_app


LOCK = "ycgc:youtube:lock:market-apify"
API_ROOT = "https://api.apify.com/v2/acts"


def _actor_api_id(value: str) -> str:
    """Accept either Store notation owner/actor or API notation owner~actor."""
    return value.strip().replace("/", "~")


def _channel_panel(db) -> list[str]:
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=settings.market_topic_active_video_max_age_hours)
    # Rotate through the channel universe before revisiting a channel. This
    # makes every paid Actor run expand coverage rather than repeatedly buying
    # the same recent Shorts from a random small panel.
    recently_scanned = set(db.scalars(
        select(MarketVideo.channel_id)
        .join(MarketVideoObservation, MarketVideoObservation.market_video_id == MarketVideo.id)
        .where(
            MarketVideoObservation.source_lane == "apify",
            MarketVideoObservation.observed_at >= now - timedelta(hours=24),
            MarketVideo.channel_id.is_not(None),
        )
    ).all())
    regions = [region for region, _ in settings.market_profile_list]
    target = max(settings.apify_channels_per_run, len(regions) * settings.apify_channels_per_region)
    rows = db.execute(
        select(MarketVideo, MarketVideoObservation.region)
        .join(MarketVideoObservation, MarketVideoObservation.market_video_id == MarketVideo.id)
        .where(
            MarketVideo.channel_id.is_not(None),
            MarketVideo.published_at >= cutoff,
            MarketVideo.channel_id.not_in(recently_scanned) if recently_scanned else True,
        )
        .order_by(func.random())
        .limit(target * 8)
    ).all()
    # On a small initial dataset all channels may already have been scanned;
    # fall back gracefully rather than idling the collector.
    if not rows:
        rows = db.execute(
            select(MarketVideo, MarketVideoObservation.region)
            .join(MarketVideoObservation, MarketVideoObservation.market_video_id == MarketVideo.id)
            .where(MarketVideo.channel_id.is_not(None), MarketVideo.published_at >= cutoff)
            .order_by(func.random())
            .limit(target * 8)
        ).all()
    channels: list[str] = []
    seen: set[str] = set()
    # Allocate a minimum independent panel to every configured region. The
    # region is provenance from the source that first surfaced the channel;
    # it does not filter the Actor output itself.
    for region in regions:
        selected = 0
        for row, observed_region in rows:
            if observed_region != region or not row.channel_id or row.channel_id in seen:
                continue
            seen.add(row.channel_id); channels.append(row.channel_id); selected += 1
            if selected >= settings.apify_channels_per_region:
                break
    for row, _ in rows:
        if len(channels) >= target:
            break
        if row.channel_id and row.channel_id not in seen:
            seen.add(row.channel_id); channels.append(row.channel_id)
    return channels


def _actor_input(channel_ids: list[str]) -> dict:
    # Input schema documented by streamers/youtube-scraper. maxResults=0
    # suppresses regular videos; Shorts are requested newest-first.
    return {
        "startUrls": [{"url": f"https://www.youtube.com/channel/{channel_id}"} for channel_id in channel_ids],
        "maxResults": 0,
        "maxResultsShorts": settings.apify_shorts_per_channel,
        "maxResultStreams": 0,
        "oldestPostDate": f"{max(1, settings.market_topic_active_video_max_age_hours // 24)} days",
        "sortVideosBy": "NEWEST",
        "downloadSubtitles": False,
        "aiVideoDescription": False,
        "aiVideoSummary": False,
    }


@celery_app.task(bind=True, name="app.tasks.market_apify_tasks.collect_apify_shorts", soft_time_limit=540, time_limit=600)
def collect_apify_shorts(self: Task) -> dict[str, int | str]:
    """Run the approved Actor and place normalized rows into raw MarketVideo intake."""
    if not settings.apify_enabled:
        return {"status": "disabled"}
    if not settings.apify_token or not settings.apify_actor_id:
        return {"status": "not_configured"}
    store = SeedStore()
    if not store.client.set(LOCK, "1", nx=True, ex=350):
        return {"status": "skipped_locked"}
    now = datetime.now(UTC)
    created = updated = observations = invalid = failed_batches = 0
    batch_errors: list[str] = []
    try:
        with SessionLocal() as db:
            # Backfill rows collected by the same Shorts-only Actor before
            # this explicit marker existed. They are preserved raw evidence;
            # only their verified source classification is completed.
            prior_apify = db.scalars(
                select(MarketVideo).where(MarketVideo.source_provenance.contains({"apify": True}))
            ).all()
            for prior in prior_apify:
                provenance = dict(prior.source_provenance or {})
                if provenance.get("shorts_source_signal") in {"apify_shorts_only_request", "apify_actor_type_shorts"}:
                    prior.shorts_status = "VERIFIED_SHORTS"
                    provenance["shorts_source_signal"] = "apify_actor_type_shorts"
                    prior.source_provenance = provenance
            channel_ids = _channel_panel(db)
            if not channel_ids:
                return {"status": "no_fresh_channel_panel"}
            endpoint = f"{API_ROOT}/{_actor_api_id(settings.apify_actor_id)}/run-sync-get-dataset-items"
            # Keep a direct link from every raw Actor item to the exact run
            # that produced it.  This is important for the source-health
            # report: a channel batch must never borrow another batch's
            # unique/duplicate/fresh counters merely because response sizes
            # differ.
            rows: list[tuple[dict, MarketSourceRun]] = []
            batch_size = max(1, settings.apify_max_channels_per_request)
            # The Actor accepts a limited direct-URL payload. Splitting the
            # independent panel preserves the desired 21-channel coverage
            # without sending an invalid oversized request.
            for start in range(0, len(channel_ids), batch_size):
                source_run = MarketSourceRun(
                    source_lane="apify",
                    cohort_key=f"channel-panel:{start // batch_size + 1}",
                    started_at=datetime.now(UTC),
                    details={"requested_channels": len(channel_ids[start : start + batch_size])},
                )
                db.add(source_run)
                db.flush()
                try:
                    response = httpx.post(
                        endpoint,
                        params={"token": settings.apify_token, "clean": "true"},
                        json=_actor_input(channel_ids[start : start + batch_size]),
                        # A single slow batch must not consume the whole task.
                        # Five bounded runs still cover the 21-channel panel.
                        timeout=min(settings.apify_timeout_seconds, 75),
                    )
                    if response.is_error:
                        failed_batches += 1
                        batch_errors.append(f"http_{response.status_code}")
                        source_run.status = "ERROR"
                        source_run.error_type = f"http_{response.status_code}"
                        source_run.completed_at = datetime.now(UTC)
                        continue
                    batch_rows = response.json()
                    if not isinstance(batch_rows, list):
                        failed_batches += 1
                        batch_errors.append("invalid_response_shape")
                        source_run.status = "ERROR"
                        source_run.error_type = "invalid_response_shape"
                        source_run.completed_at = datetime.now(UTC)
                        continue
                    rows.extend((item, source_run) for item in batch_rows if isinstance(item, dict))
                    source_run.status = "OK"
                    source_run.completed_at = datetime.now(UTC)
                    source_run.candidates_seen = len(batch_rows)
                except httpx.HTTPError as exc:
                    # Retain successful batches. The next scheduled cycle will
                    # rotate back to the failed channel subset automatically.
                    failed_batches += 1
                    batch_errors.append(type(exc).__name__)
                    source_run.status = "ERROR"
                    source_run.error_type = type(exc).__name__
                    source_run.completed_at = datetime.now(UTC)
            existing = {video.video_id: video for video in db.scalars(select(MarketVideo).where(MarketVideo.video_id.in_([str(row.get("id") or row.get("videoId") or "") for row, _ in rows]))).all()}
            active_runs = [run for _, run in rows if run.status == "OK"]
            run_stats = {run.id: {"accepted": 0, "unique": 0, "duplicates": 0, "fresh_0_24": 0, "fresh_24_72": 0} for run in active_runs}
            for rank, (raw, run) in enumerate(rows, start=1):
                if not isinstance(raw, dict) or raw.get("error"):
                    invalid += 1
                    run.rejected_not_shorts += 1
                    continue
                item = normalize_apify_short(raw, region=None, observed_at=now)
                if not item:
                    invalid += 1
                    run.rejected_not_shorts += 1
                    continue
                # Apify's current actor is a supplemental channel lane. It
                # remains strictly type=shorts, but its contribution and
                # novelty are measured rather than assumed to be broad.
                stat = run_stats.get(run.id)
                if stat is not None:
                    stat["accepted"] += 1
                video = existing.get(item.video_id)
                if video is None:
                    video = MarketVideo(video_id=item.video_id, video_url=item.video_url or f"https://www.youtube.com/watch?v={item.video_id}")
                    db.add(video); db.flush(); existing[item.video_id] = video; created += 1
                    if stat is not None:
                        stat["unique"] += 1
                else:
                    updated += 1
                    if stat is not None:
                        stat["duplicates"] += 1
                video.channel_id = item.channel_id or video.channel_id
                video.channel_title = item.channel_title or video.channel_title
                video.title = item.title or video.title
                video.thumbnail_url = item.thumbnail_url or video.thumbnail_url
                video.video_url = item.video_url or video.video_url
                video.published_at = item.published_at or video.published_at
                video.last_seen_at = now
                # The actor returns the native YouTube content type and the
                # adapter admits only `type: shorts`. It is therefore a more
                # authoritative Shorts signal than a duration heuristic.
                video.shorts_status = "VERIFIED_SHORTS"
                provenance = dict(video.source_provenance or {})
                provenance.update({
                    "apify": True,
                    "apify_actor": _actor_api_id(settings.apify_actor_id),
                    "apify_discovery": "random_fresh_channel_panel",
                    "shorts_source_signal": "apify_actor_type_shorts",
                })
                video.source_provenance = provenance
                if item.published_at:
                    age = max(0.0, (now - item.published_at).total_seconds() / 3600)
                    if stat is not None and age < 24:
                        stat["fresh_0_24"] += 1
                    elif stat is not None and age < 72:
                        stat["fresh_24_72"] += 1
                db.add(MarketVideoObservation(
                    market_video_id=video.id, observed_at=now, source_lane="apify", region=None, language=None,
                    category_id=None, view_count=item.view_count or 0, like_count=None, comment_count=None,
                    source_rank=rank, raw_payload={"provider": "apify", "has_title": bool(item.title), "has_published_at": bool(item.published_at)},
                ))
                observations += 1
            for run in {run.id: run for run in active_runs}.values():
                stat = run_stats[run.id]
                run.accepted_shorts = stat["accepted"]
                run.unique_shorts = stat["unique"]
                run.duplicate_shorts = stat["duplicates"]
                run.fresh_0_24h = stat["fresh_0_24"]
                run.fresh_24_72h = stat["fresh_24_72"]
            db.commit()
        state = "partial" if failed_batches else "ok"
        store.set_status(apify_market_state=state, apify_market_last_scan_at=now.isoformat(), apify_market_created=created, apify_market_updated=updated, apify_market_observations=observations, apify_market_invalid=invalid, apify_market_failed_batches=failed_batches, apify_market_last_error="|".join(batch_errors[:5]) if batch_errors else "")
        return {"status": state, "created": created, "updated": updated, "observations": observations, "invalid": invalid, "failed_batches": failed_batches}
    except (httpx.HTTPError, ValueError) as exc:
        store.set_status(apify_market_state="source_error", apify_market_last_error=type(exc).__name__)
        raise self.retry(exc=exc, countdown=900, max_retries=1)
    finally:
        store.client.delete(LOCK)
