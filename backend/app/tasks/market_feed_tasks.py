"""Direct anonymous Shorts-feed intake for Market Trends."""

from datetime import UTC, datetime, timedelta
from math import ceil

from celery import Task
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models.market_trends import MarketSourceRun, MarketVideo, MarketVideoObservation
from app.services.seed_store import SeedStore
from app.services.youtube_client import YoutubeAnonymousClient
from app.schemas.youtube import YoutubeSeed
from app.tasks.celery_app import celery_app

MARKET_FEED_LOCK = "ycgc:youtube:lock:market-shorts-feed"
EARLY_BRIDGE_LOCK = "ycgc:youtube:lock:market-reel-to-early"


def _admit_fresh_early_seed(store: SeedStore, seed, *, region: str, language: str) -> bool:
    """Share native fresh Shorts intake with Early Signals without sharing ranking.

    The anonymous reel feed is already a real Shorts-only surface.  When a
    Short is first observed while fresh and still below the early-view ceiling,
    it deserves velocity observation even if it was discovered by the broader
    Market lane.  This only writes a bounded Redis seed; it does *not* create a
    public topic or bypass the later cross-channel gate.
    """
    now = datetime.now(UTC)
    age_hours = (now - seed.published_at).total_seconds() / 3600
    if age_hours < 0 or age_hours > settings.early_topic_max_entry_age_hours:
        return False
    if seed.seed_view_count > settings.early_topic_max_entry_views:
        return False
    if seed.view_count_precision != "exact":
        return False
    seed.source = f"market_reel_feed:{region}/{language}"
    return store.save(seed)


@celery_app.task(bind=True, name="app.tasks.market_feed_tasks.collect_market_shorts_feed", soft_time_limit=540, time_limit=600)
def collect_market_shorts_feed(self: Task) -> dict[str, int | str]:
    """This lane is Shorts by construction: it reads logged-out Shorts reel feed."""
    store = SeedStore()
    if not store.client.set(MARKET_FEED_LOCK, "1", nx=True, ex=570):
        return {"status": "skipped_locked"}
    created = updated = observations = early_seeded = 0
    now = datetime.now(UTC)
    try:
        with SessionLocal() as db:
            existing = {item.video_id: item for item in db.scalars(select(MarketVideo)).all()}
            # Each Market profile owns its full per-region target. This is not
            # the capped Early Breakout profile list and is never split when a
            # new market region is added.
            cohort_count = min(
                max(1, settings.market_feed_cohorts_per_region),
                max(1, settings.market_feed_max_cohorts_per_region),
            )
            pages_per_cohort = max(1, ceil(settings.market_feed_pages_per_region / cohort_count))
            target_per_cohort = max(1, ceil(settings.market_feed_target_per_region / cohort_count))
            for region, language in settings.market_profile_list:
                # A cohort is one clean logged-out feed walk.  It is not a
                # topic persona and never receives a keyword or a creator
                # list.  Separate runs make the sample and its duplication
                # rate measurable instead of treating one repeat-heavy feed
                # as broad market coverage.
                for cohort_number in range(1, cohort_count + 1):
                    started_at = datetime.now(UTC)
                    source_run = MarketSourceRun(
                        source_lane="anonymous_shorts_feed",
                        region=region,
                        language=language,
                        cohort_key=f"{region}/{language}/cohort-{cohort_number}",
                        started_at=started_at,
                    )
                    db.add(source_run)
                    db.flush()
                    client = YoutubeAnonymousClient(region=region, language=language)
                    try:
                        seeds, result = client.discover_seeds(
                            # Cohorts divide one region's collection budget;
                            # adding cohorts increases independent paths, not
                            # the request volume without bound.
                            max_pages=pages_per_cohort,
                            max_accepted=target_per_cohort,
                            # Market Topics intentionally use their own 7-day
                            # window. This must not inherit the 24-hour Early
                            # Breakout rule, nor discard rounded feed counters.
                            max_age_hours=settings.market_topic_active_video_max_age_hours,
                            require_exact_views=False,
                        )
                    except Exception as exc:
                        source_run.status = "ERROR"
                        source_run.error_type = type(exc).__name__
                        source_run.completed_at = datetime.now(UTC)
                        source_run.details = {"error": str(exc)[:300]}
                        db.commit()
                        continue
                    finally:
                        client.close()

                    run_created = run_duplicates = run_fresh_0_24 = run_fresh_24_72 = 0
                    for rank, seed in enumerate(seeds, start=1):
                        age_hours = max(0.0, (now - seed.published_at).total_seconds() / 3600)
                        if age_hours < 24:
                            run_fresh_0_24 += 1
                        elif age_hours < 72:
                            run_fresh_24_72 += 1
                        if _admit_fresh_early_seed(store, seed, region=region, language=language):
                            early_seeded += 1
                        video = existing.get(seed.video_id)
                        if not video:
                            video = MarketVideo(video_id=seed.video_id, video_url=seed.video_url)
                            db.add(video); db.flush(); existing[seed.video_id] = video; created += 1; run_created += 1
                        else:
                            updated += 1; run_duplicates += 1
                        video.channel_id, video.channel_title, video.title = seed.channel_id, seed.channel_title, seed.title
                        video.thumbnail_url, video.published_at = seed.thumbnail_url, seed.published_at
                        video.last_seen_at, video.shorts_status = now, "VERIFIED_SHORTS"
                        # This is the native logged-out YouTube reel/Shorts feed,
                        # not a duration-based search. Preserve other source
                        # provenance instead of overwriting it on a duplicate.
                        provenance = dict(video.source_provenance or {})
                        provenance.setdefault("first_lane", "anonymous_shorts_feed")
                        provenance.update({"anonymous_shorts_feed": True, "shorts_verified_by_source": "youtube_reel_feed"})
                        video.source_provenance = provenance
                        db.add(MarketVideoObservation(market_video_id=video.id, observed_at=now, source_lane="anonymous_shorts_feed", region=region, language=language, view_count=seed.seed_view_count, source_rank=rank, raw_payload={"cohort": cohort_number, "source_run_id": source_run.id}))
                        observations += 1
                    source_run.status = "OK"
                    source_run.completed_at = datetime.now(UTC)
                    source_run.candidates_seen = result.candidates_seen
                    source_run.accepted_shorts = len(seeds)
                    source_run.unique_shorts = run_created
                    source_run.duplicate_shorts = run_duplicates
                    source_run.fresh_0_24h = run_fresh_0_24
                    source_run.fresh_24_72h = run_fresh_24_72
                    source_run.details = {
                        "pages_requested": result.pages_requested,
                        "rejected_age": result.seeds_rejected_age,
                        "rejected_incomplete": result.seeds_rejected_incomplete,
                    }
            db.commit()
        store.set_status(market_feed_state="ok", market_feed_last_scan_at=now.isoformat(), market_feed_created=created, market_feed_observations=observations, market_feed_early_seeded=early_seeded)
        return {"created": created, "updated": updated, "observations": observations, "early_seeded": early_seeded}
    finally:
        store.client.delete(MARKET_FEED_LOCK)


@celery_app.task(bind=True, name="app.tasks.market_feed_tasks.seed_fresh_early_from_market_reel", soft_time_limit=300, time_limit=360)
def seed_fresh_early_from_market_reel(self: Task) -> dict[str, int | str]:
    """Backfill the *observation pool*, never the public Early Topics board.

    A native reel Short can be found by the broad collector a few minutes
    before the narrow Early collector sees it.  Reusing that native Shorts
    evidence avoids wasting fresh coverage, but it obtains a new exact view
    baseline at admission and still has to grow and repeat across channels.
    """
    store = SeedStore()
    if not store.client.set(EARLY_BRIDGE_LOCK, "1", nx=True, ex=330):
        return {"status": "skipped_locked"}
    examined = seeded = rejected = unavailable = 0
    client = YoutubeAnonymousClient()
    try:
        now = datetime.now(UTC)
        cutoff = now - timedelta(hours=settings.early_topic_max_entry_age_hours)
        live_ids = set(store.list_ids())
        with SessionLocal() as db:
            candidates = db.scalars(
                select(MarketVideo)
                .where(
                    MarketVideo.shorts_status == "VERIFIED_SHORTS",
                    MarketVideo.published_at >= cutoff,
                    MarketVideo.source_provenance.contains({"anonymous_shorts_feed": True}),
                )
                .order_by(MarketVideo.last_seen_at.desc())
                .limit(36)
            ).all()
        for video in candidates:
            if video.video_id in live_ids:
                continue
            examined += 1
            try:
                metadata = client.fetch_current_metadata(video.video_id)
            except Exception:
                unavailable += 1
                continue
            published_at = metadata.get("published_at")
            view_count = metadata.get("view_count")
            channel_id = metadata.get("channel_id") or video.channel_id
            if not published_at or not isinstance(view_count, int) or not channel_id:
                unavailable += 1
                continue
            seed = YoutubeSeed(
                video_id=video.video_id,
                channel_id=channel_id,
                channel_title=metadata.get("channel_title") or video.channel_title,
                title=metadata.get("title") or video.title,
                seed_view_count=view_count,
                published_at=published_at,
                seeded_at=now,
                video_url=f"https://www.youtube.com/shorts/{video.video_id}",
                thumbnail_url=metadata.get("thumbnail_url") or video.thumbnail_url,
                view_count_precision="exact",
                published_at_precision="day",
                source="market_reel_feed:bridge",
            )
            if _admit_fresh_early_seed(store, seed, region="bridge", language="native"):
                seeded += 1
            else:
                rejected += 1
        store.set_status(
            early_bridge_last_run_at=now.isoformat(),
            early_bridge_examined=examined,
            early_bridge_seeded=seeded,
            early_bridge_rejected=rejected,
            early_bridge_unavailable=unavailable,
        )
        return {"examined": examined, "seeded": seeded, "rejected": rejected, "unavailable": unavailable}
    finally:
        client.close()
        store.client.delete(EARLY_BRIDGE_LOCK)
