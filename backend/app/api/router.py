import csv
from hashlib import sha256
from datetime import UTC, datetime, timedelta
from io import StringIO

from fastapi import APIRouter, Depends, HTTPException, Query, Header
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import case, desc, func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.config import settings
from app.models.youtube_snipe import YoutubeSnipe
from app.models.trend_cluster import TopicClusterFeedback, TrendCluster, TrendMembership, TrendSnapshot
from app.models.market_trends import ExternalTrendBenchmark, MarketContentTruthAudit, MarketMetadataTrend, MarketMetadataTrendMembership, MarketMetadataTrendSnapshot, MarketRankedTopic, MarketRankedTopicMembership, MarketRankedTopicReview, MarketRankedTopicSnapshot, MarketSourceRun, MarketTopic, MarketTopicFeedback, MarketTopicMembership, MarketTopicSnapshot, MarketVideo, MarketVideoFeature, MarketVideoObservation
from app.services.seed_store import SeedStore
from app.tasks.celery_app import celery_app

api_router = APIRouter(prefix="/api/v1")


PUBLIC_TREND_STATUSES = ("EMERGING", "ACCELERATING", "CONFIRMED")
REVIEW_DECISIONS = ("CONFIRM_CLUSTER", "REJECT_CLUSTER", "SPLIT_NEEDED", "INSUFFICIENT_EVIDENCE")
RANKED_TOPIC_REVIEW_DECISIONS = ("VALID_TOPIC", "WRONG_MERGE", "TOO_GENERIC", "NOT_A_TREND", "NEEDS_MORE_EVIDENCE")
NON_FOLLOWABLE_TOPIC_LABELS = {"animals", "food", "music", "comedy", "entertainment", "sports", "kick", "goal", "prank", "coach", "lucu", "they", "streamer", "volleyball"}


def _semantic_cooldown_key() -> str:
    identity = f"{settings.market_semantic_base_url}|{settings.market_semantic_model}"
    return f"ycgc:youtube:market-semantic-gateway-cooldown:{sha256(identity.encode()).hexdigest()[:12]}"


class TopicFeedbackInput(BaseModel):
    decision: str
    note: str | None = Field(default=None, max_length=1000)
    reviewer: str = Field(default="local_reviewer", min_length=1, max_length=96)


class RankedTopicReviewInput(BaseModel):
    decision: str
    note: str | None = Field(default=None, max_length=1000)
    reviewer: str = Field(default="local_reviewer", min_length=1, max_length=96)


class ExternalBenchmarkInput(BaseModel):
    source: str = Field(min_length=2, max_length=64)
    label: str = Field(min_length=3, max_length=255)
    observed_on: datetime | None = None
    region: str | None = Field(default=None, max_length=16)
    category: str | None = Field(default=None, max_length=64)
    source_rank: int | None = Field(default=None, ge=1, le=500)
    source_url: str | None = Field(default=None, max_length=1024)
    note: str | None = Field(default=None, max_length=1000)


def _trend_member_payload(row: YoutubeSnipe, membership: TrendMembership) -> dict:
    """Evidence posts are exposed separately from the topic-level aggregate."""
    metadata = row.raw_metadata or {}
    return {
        "video_id": row.video_id,
        "title": row.title,
        "channel_title": row.channel_title,
        "channel_id": row.channel_id,
        "video_url": row.video_url,
        "thumbnail_url": row.thumbnail_url,
        "published_at": row.published_at.isoformat(),
        "detected_at": row.detected_at.isoformat(),
        "current_view_count": row.current_view_count,
        "velocity_per_hour": row.velocity_per_hour,
        "signal_tier": row.signal_tier,
        "niche": row.niche,
        "channel_context": metadata.get("channel_context"),
        "similarity_score": membership.similarity_score,
        "joined_at": membership.joined_at.isoformat(),
        "membership_state": membership.membership_state,
        "is_reupload_suspect": membership.is_reupload_suspect,
        "is_same_channel_duplicate": membership.is_same_channel_duplicate,
    }


def _trend_snapshot_payload(snapshot: TrendSnapshot) -> dict:
    return {
        "observed_at": snapshot.observed_at.isoformat(),
        "observed_views": snapshot.observed_views,
        "observed_velocity_per_hour": snapshot.observed_velocity_per_hour,
        "median_velocity_per_hour": snapshot.median_velocity_per_hour,
        "acceleration": snapshot.acceleration,
        "member_count": snapshot.member_count,
        "channel_count": snapshot.channel_count,
        "new_member_count": snapshot.new_member_count,
        "new_channel_count": snapshot.new_channel_count,
        "trend_score": snapshot.trend_score,
        "reason": snapshot.reason,
    }


def _trend_payload(db: Session, cluster: TrendCluster, *, member_limit: int = 5, snapshot_limit: int = 12) -> dict:
    members = db.execute(
        select(YoutubeSnipe, TrendMembership)
        .join(TrendMembership, TrendMembership.youtube_snipe_id == YoutubeSnipe.id)
        .where(TrendMembership.cluster_id == cluster.id)
        .order_by(desc(YoutubeSnipe.velocity_per_hour))
        .limit(member_limit)
    ).all()
    snapshots = db.scalars(
        select(TrendSnapshot)
        .where(TrendSnapshot.cluster_id == cluster.id)
        .order_by(desc(TrendSnapshot.observed_at))
        .limit(snapshot_limit)
    ).all()
    snapshots.reverse()
    return {
        "id": str(cluster.id),
        "public_slug": cluster.public_slug,
        "label": cluster.label or "Unlabeled observed pattern",
        "label_confidence": cluster.label_confidence,
        "niche": cluster.niche,
        "status": cluster.status,
        "trend_score": cluster.trend_score,
        "semantic_cohesion": cluster.semantic_cohesion,
        "observed_views": cluster.observed_views,
        "observed_velocity_per_hour": cluster.observed_velocity_per_hour,
        "acceleration": cluster.acceleration,
        "member_count": cluster.member_count,
        "channel_count": cluster.channel_count,
        "first_detected_at": cluster.first_detected_at.isoformat(),
        "last_observed_at": cluster.last_observed_at.isoformat() if cluster.last_observed_at else None,
        "region_mix": cluster.region_mix or {},
        "channel_context_mix": cluster.channel_context_mix or {},
        "evidence_summary": cluster.evidence_summary or {},
        "cluster_reason": cluster.cluster_reason,
        "members": [_trend_member_payload(row, membership) for row, membership in members],
        "snapshots": [_trend_snapshot_payload(snapshot) for snapshot in snapshots],
    }


def require_admin(x_admin_token: str | None = Header(default=None)) -> bool:
    """Optional header-based admin guard. If `settings.admin_api_token` is empty,
    the guard is inactive for local development. Otherwise an exact token must be provided."""
    if not settings.admin_api_token:
        return True
    if x_admin_token and x_admin_token == settings.admin_api_token:
        return True
    raise HTTPException(status_code=403, detail="Admin token required")


@api_router.get("/youtube/status")
def youtube_pipeline_status(db: Session = Depends(get_db)) -> dict:
    store = SeedStore()
    status = store.status()
    pending_states = store.pending_state_counts()
    signal_count = db.scalar(
        select(__import__("sqlalchemy").func.count(YoutubeSnipe.id)).where(
            YoutubeSnipe.signal_tier.in_(("EARLY", "RISING", "BREAKOUT"))
        )
    ) or 0
    profiles = []
    for region, language in settings.youtube_profile_list:
        latest = {
            "seen": int(status.get(f"profile_{region}_{language}_seen", 0)),
            "fresh": int(status.get(f"profile_{region}_{language}_fresh", 0)),
            "duplicates": int(status.get(f"profile_{region}_{language}_duplicates", 0)),
            "sessions": int(status.get(f"profile_{region}_{language}_sessions", 0)),
            "target_shortfall": int(status.get(f"profile_{region}_{language}_target_shortfall", 0)),
        }
        profiles.append({"region": region, "language": language, "latest": latest, "coverage_24h": store.coverage(f"{region}_{language}")})
    return {"seed_active": len(store.list_ids()), "pending_breakouts": store.pending_count(), "pending_states": pending_states, "pending_items": store.pending_breakouts(), "signal_count": signal_count, "last_seed_scan_at": status.get("last_seed_scan_at"), "last_velocity_scan_at": status.get("last_velocity_scan_at"), "last_seed_seen": int(status.get("last_seed_seen", 0)), "last_seed_written": int(status.get("last_seed_written", 0)), "last_seed_old": int(status.get("last_seed_old", 0)), "last_seed_duplicates": int(status.get("last_seed_duplicates", 0)), "last_velocity_eligible": int(status.get("last_velocity_eligible", 0)), "last_media_failures": int(status.get("last_media_failures", 0)), "profiles": profiles}


@api_router.get("/youtube/market/status")
def market_trends_status(db: Session = Depends(get_db)) -> dict:
    """Coverage status for the isolated broad Market Trends collection lane."""
    store = SeedStore()
    status = store.status()
    return {
        "enabled": settings.market_trends_enabled,
        "configured": bool(settings.youtube_data_api_key),
        "state": status.get("market_trends_state", "waiting"),
        "last_scan_at": status.get("market_trends_last_scan_at"),
        "videos": db.scalar(select(__import__("sqlalchemy").func.count(MarketVideo.id))) or 0,
        "observations": db.scalar(select(__import__("sqlalchemy").func.count(MarketVideoObservation.id))) or 0,
        "regions": [item.strip().upper() for item in settings.market_trends_regions.split(",") if item.strip()],
        "profiles": [{"region": region, "language": language, "feed_target": settings.market_feed_target_per_region, "chart_results_per_category": settings.market_trends_max_results} for region, language in settings.market_profile_list],
        "categories": [item.strip() for item in settings.market_trends_chart_categories.split(",") if item.strip()],
        "methodology": "Official public-chart lane only. It is isolated from anonymous Early Breakout discovery and does not represent all YouTube uploads.",
    }


@api_router.get("/youtube/video-trends/coverage")
def general_video_trends_coverage(db: Session = Depends(get_db)) -> dict:
    """Operational coverage for the separate 110-region general chart lane."""
    store = SeedStore()
    status = store.status()
    cutoff = datetime.now(UTC) - timedelta(days=7)
    runs = db.scalars(
        select(MarketSourceRun)
        .where(
            MarketSourceRun.source_lane == "official_general_chart",
            MarketSourceRun.started_at >= cutoff,
        )
        .order_by(desc(MarketSourceRun.started_at))
        .limit(2_000)
    ).all()
    by_region: dict[str, dict] = {}
    for run in runs:
        region = run.region or "unknown"
        item = by_region.setdefault(region, {
            "region": region, "region_name": (run.details or {}).get("region_name"),
            "runs": 0, "ok_runs": 0, "error_runs": 0, "videos_seen": 0,
            "new_videos": 0, "duplicates": 0, "last_run_at": None,
            "last_error": None,
        })
        item["runs"] += 1
        item["ok_runs"] += int(run.status == "OK")
        item["error_runs"] += int(run.status == "ERROR")
        item["videos_seen"] += int(run.candidates_seen or 0)
        item["new_videos"] += int(run.unique_shorts or 0)
        item["duplicates"] += int(run.duplicate_shorts or 0)
        if item["last_run_at"] is None:
            item["last_run_at"] = run.started_at.isoformat() if run.started_at else None
            item["last_error"] = (run.details or {}).get("error") if run.status == "ERROR" else None
    target = int(status.get("general_video_chart_target_regions", settings.market_general_chart_target_regions))
    healthy = sum(1 for item in by_region.values() if item["ok_runs"])
    return {
        "state": status.get("general_video_chart_state", "waiting"),
        "catalog_regions": int(status.get("general_video_chart_catalog_regions", 0)),
        "target_regions": target,
        "regions_per_run": settings.market_general_chart_regions_per_run,
        "estimated_cycle_minutes": int(status.get("general_video_chart_estimated_cycle_minutes", 0)),
        "last_scan_at": status.get("general_video_chart_last_scan_at"),
        "last_regions": [item for item in status.get("general_video_chart_regions_this_run", "").split(",") if item],
        "healthy_regions_in_last_7d": healthy,
        "error_regions_in_last_7d": sum(1 for item in by_region.values() if item["error_runs"]),
        "regions": sorted(by_region.values(), key=lambda item: item["region"]),
        "methodology": "Region codes come from YouTube's official i18nRegions catalog. The collector rotates a fixed fair slice instead of calling all countries simultaneously. When YouTube supports more than the configured target, the included region window shifts each UTC day so no region is permanently excluded. A regional chart error is recorded for that country and does not interrupt the rest of the cycle.",
    }


@api_router.get("/youtube/market/observation-report")
def market_observation_report(db: Session = Depends(get_db)) -> dict:
    """Rolling 24-hour audit report for Market Trends thesis evidence."""
    cutoff = datetime.now(UTC) - timedelta(hours=24)
    observations = db.scalars(select(MarketVideoObservation).where(MarketVideoObservation.observed_at >= cutoff)).all()
    lanes: dict[str, int] = {}
    regions: dict[str, int] = {}
    for item in observations:
        lanes[item.source_lane] = lanes.get(item.source_lane, 0) + 1
        if item.region:
            regions[item.region] = regions.get(item.region, 0) + 1
    topic_states = dict(db.execute(select(MarketTopic.status, func.count(MarketTopic.id)).group_by(MarketTopic.status)).all())
    verified = db.scalar(select(func.count(MarketVideo.id)).where(MarketVideo.shorts_status == "VERIFIED_SHORTS")) or 0
    total = db.scalar(select(func.count(MarketVideo.id))) or 0
    # The semantic provider/model is configurable. Earlier rows persist the
    # fingerprint under provenance.semantic, while newer rows may use another
    # model name. Count the actual saved semantic payload, never a provider
    # name such as the old Gemini implementation.
    semantic_ready = sum(
        1
        for provenance in db.scalars(select(MarketVideoFeature.provenance)).all()
        if isinstance(provenance, dict) and isinstance(provenance.get("semantic"), dict)
    )
    return {"window": "rolling_24h", "market_videos": total, "verified_shorts": verified, "observations": len(observations), "source_lanes": lanes, "regions": regions, "topic_states": topic_states, "semantic_ready": semantic_ready, "semantic_cooldown": bool(SeedStore().client.exists(_semantic_cooldown_key())), "semantic_model": settings.market_semantic_model, "methodology": "Counts cover only data observed and stored by this local Market Trends system during the rolling window; they are not YouTube-wide totals."}


@api_router.get("/youtube/market/coverage")
def market_coverage(db: Session = Depends(get_db)) -> dict:
    """Human-readable source health: fresh, unique Shorts per lane and region."""
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=settings.market_metadata_window_hours)
    rows = db.execute(
        select(MarketVideo, MarketVideoObservation)
        .join(MarketVideoObservation, MarketVideoObservation.market_video_id == MarketVideo.id)
        .where(MarketVideoObservation.observed_at >= cutoff, MarketVideo.shorts_status == "VERIFIED_SHORTS")
    ).all()
    lane_ids: dict[str, set[int]] = {}
    lane_observations: dict[str, int] = {}
    region_ids: dict[str, set[int]] = {}
    lane_fresh: dict[str, int] = {}
    region_fresh: dict[str, int] = {}
    links_by_video: dict[int, set[tuple[str, str]]] = {}
    buckets = {"0_24h": 0, "24_72h": 0, "72_168h": 0, "older_or_unknown": 0}
    unique_videos: dict[int, MarketVideo] = {}
    for video, observation in rows:
        unique_videos[video.id] = video
        lane = observation.source_lane or "unknown"
        region = observation.region or "unknown"
        lane_ids.setdefault(lane, set()).add(video.id)
        lane_observations[lane] = lane_observations.get(lane, 0) + 1
        region_ids.setdefault(region, set()).add(video.id)
        links_by_video.setdefault(video.id, set()).add((lane, region))
    for video in unique_videos.values():
        age = (now - video.published_at).total_seconds() / 3600 if video.published_at else None
        if age is not None and age < 24:
            bucket = "0_24h"
        elif age is not None and age < 72:
            bucket = "24_72h"
        elif age is not None and age < settings.market_metadata_window_hours:
            bucket = "72_168h"
        else:
            bucket = "older_or_unknown"
        buckets[bucket] += 1
        # A video may have multiple region/lane observations. Count freshness
        # independently within each panel, but never inflate a lane by scans.
        for lane, region in links_by_video.get(video.id, set()):
            if bucket in {"0_24h", "24_72h"}:
                lane_fresh[lane] = lane_fresh.get(lane, 0) + 1
                region_fresh[region] = region_fresh.get(region, 0) + 1
    audit_counts = dict(db.execute(select(MarketContentTruthAudit.status, func.count(MarketContentTruthAudit.id)).group_by(MarketContentTruthAudit.status)).all())
    verification = dict(db.execute(
        select(MarketVideo.shorts_status, func.count(MarketVideo.id)).group_by(MarketVideo.shorts_status)
    ).all())
    verified_total = int(verification.get("VERIFIED_SHORTS", 0))
    semantic_ready = sum(
        1
        for provenance in db.scalars(select(MarketVideoFeature.provenance)).all()
        if isinstance(provenance, dict) and isinstance(provenance.get("semantic"), dict)
    )
    all_observed = db.execute(
        select(MarketVideo.shorts_status, MarketVideoObservation.source_lane, MarketVideoObservation.region, MarketVideo.id)
        .join(MarketVideoObservation, MarketVideoObservation.market_video_id == MarketVideo.id)
        .where(MarketVideoObservation.observed_at >= cutoff)
    ).all()
    lane_format_ids: dict[str, dict[str, set[int]]] = {}
    region_format_ids: dict[str, dict[str, set[int]]] = {}
    for short_status, lane, region, video_id in all_observed:
        lane_key, region_key = lane or "unknown", region or "unknown"
        lane_format_ids.setdefault(lane_key, {}).setdefault(short_status, set()).add(video_id)
        region_format_ids.setdefault(region_key, {}).setdefault(short_status, set()).add(video_id)
    status = SeedStore().status()
    source_run_cutoff = now - timedelta(hours=24)
    source_runs = db.scalars(
        select(MarketSourceRun)
        .where(MarketSourceRun.started_at >= source_run_cutoff)
        .order_by(desc(MarketSourceRun.started_at))
        .limit(500)
    ).all()
    run_health: dict[tuple[str, str], dict] = {}
    for run in source_runs:
        key = (run.source_lane, run.region or "unknown")
        entry = run_health.setdefault(key, {
            "lane": run.source_lane, "region": run.region or "unknown", "runs": 0,
            "ok_runs": 0, "error_runs": 0, "candidates_seen": 0, "accepted_shorts": 0,
            "unique_shorts": 0, "duplicate_shorts": 0, "fresh_0_24h": 0,
            "fresh_24_72h": 0, "rejected_not_shorts": 0, "last_run_at": None,
        })
        entry["runs"] += 1
        entry["ok_runs"] += int(run.status == "OK")
        entry["error_runs"] += int(run.status == "ERROR")
        for field in ("candidates_seen", "accepted_shorts", "unique_shorts", "duplicate_shorts", "fresh_0_24h", "fresh_24_72h", "rejected_not_shorts"):
            entry[field] += int(getattr(run, field) or 0)
        if entry["last_run_at"] is None or (run.started_at and run.started_at > datetime.fromisoformat(entry["last_run_at"])):
            entry["last_run_at"] = run.started_at.isoformat() if run.started_at else None
    apify = {
        "state": status.get("apify_market_state", "not_run"),
        "invalid": int(status.get("apify_market_invalid", 0)),
        "failed_batches": int(status.get("apify_market_failed_batches", 0)),
        "last_scan_at": status.get("apify_market_last_scan_at"),
    }
    shorts_verification = {
        "verified_last_batch": int(status.get("market_shorts_verified", 0)),
        "rejected_last_batch": int(status.get("market_shorts_rejected", 0)),
        "failed_last_batch": int(status.get("market_shorts_failed", 0)),
        "last_verify_at": status.get("market_shorts_last_verify_at"),
    }
    return {
        "window_hours": settings.market_metadata_window_hours,
        "verified_unique_shorts": len(unique_videos),
        "fresh_age_buckets": buckets,
        "source_lanes": [{
            "lane": lane,
            "unique_shorts": len(ids),
            "fresh_0_72h": lane_fresh.get(lane, 0),
            "observations": lane_observations.get(lane, 0),
            "repeat_observations": max(0, lane_observations.get(lane, 0) - len(ids)),
        } for lane, ids in sorted(lane_ids.items())],
        "regions": [{"region": region, "unique_shorts": len(ids), "fresh_0_72h": region_fresh.get(region, 0)} for region, ids in sorted(region_ids.items())],
        "format_by_source": [{"lane": lane, "statuses": {status_name: len(video_ids) for status_name, video_ids in statuses.items()}} for lane, statuses in sorted(lane_format_ids.items())],
        "format_by_region": [{"region": region, "statuses": {status_name: len(video_ids) for status_name, video_ids in statuses.items()}} for region, statuses in sorted(region_format_ids.items())],
        "content_truth": audit_counts,
        "format_verification": verification,
        "last_verification_batch": shorts_verification,
        "apify_health": apify,
        "source_run_health": sorted(run_health.values(), key=lambda item: (item["lane"], item["region"])),
        "semantic_backlog": max(0, verified_total - semantic_ready),
        "methodology": "Unique verified Shorts observed by this system within the active window. A source scan may repeat a video, but coverage counts it once per lane/region. Cohort health reports new versus repeated Shorts separately. This is observed coverage, not all of YouTube.",
    }


@api_router.get("/youtube/market/videos")
def list_market_videos(
    limit: int = Query(default=500, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict:
    """Transparent raw market intake while semantic topic clustering is built."""
    videos = db.scalars(select(MarketVideo).where(MarketVideo.shorts_status == "VERIFIED_SHORTS").order_by(desc(MarketVideo.last_seen_at)).limit(limit)).all()
    latest_by_video = {}
    for observation in db.scalars(
        select(MarketVideoObservation).order_by(desc(MarketVideoObservation.observed_at)).limit(limit * 4)
    ).all():
        latest_by_video.setdefault(observation.market_video_id, observation)
    total_verified = db.scalar(select(func.count(MarketVideo.id)).where(MarketVideo.shorts_status == "VERIFIED_SHORTS")) or 0
    return {"items": [{
        "video_id": video.video_id, "title": video.title, "channel_title": video.channel_title,
        "video_url": video.video_url, "thumbnail_url": video.thumbnail_url,
        "published_at": video.published_at.isoformat() if video.published_at else None,
        "last_seen_at": video.last_seen_at.isoformat(), "category_id": video.category_id,
        "shorts_status": video.shorts_status,
        "view_count": (latest_by_video.get(video.id).view_count if latest_by_video.get(video.id) else 0),
        "source_rank": (latest_by_video.get(video.id).source_rank if latest_by_video.get(video.id) else None),
    } for video in videos], "total_verified": total_verified, "returned": len(videos), "methodology": "Verified Shorts only: duration <=3 minutes plus vertical or square video metadata. These are broad market evidence, not yet semantic topic clusters."}


def _duration_seconds(value: str | None) -> int:
    if not value:
        return 0
    import re
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value)
    if not match:
        return 0
    hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def _duration_label(value: str | None) -> str | None:
    """Turn the official ISO-8601 duration into a compact display label."""
    if not value:
        return None
    import re
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value)
    if not match:
        return None
    hours, minutes, seconds = (int(part or 0) for part in match.groups())
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _general_region_health(
    db: Session,
    regions: list[str],
    *,
    now: datetime,
) -> list[dict]:
    """Report per-region collector freshness for the rotating general chart.

    The collector deliberately polls a fair slice of regions per run, so a
    region without a recent run is not broken. Reporting `stale` separately
    from `failed` keeps that distinction honest instead of presenting every
    listed country as equally fresh.
    """
    if not regions:
        return []
    # One full rotation is the honest freshness budget; anything older than
    # two cycles means this region genuinely missed its turn.
    per_run = max(1, settings.market_general_chart_regions_per_run)
    target = max(1, settings.market_general_chart_target_regions)
    cycle_minutes = ((target + per_run - 1) // per_run) * settings.market_general_chart_interval_minutes
    stale_after = timedelta(minutes=cycle_minutes * 2)
    rows = db.execute(
        select(
            MarketSourceRun.region,
            func.max(MarketSourceRun.started_at),
            func.count(MarketSourceRun.id),
            func.sum(case((MarketSourceRun.status == "ERROR", 1), else_=0)),
        )
        .where(
            MarketSourceRun.source_lane == "official_general_chart",
            MarketSourceRun.region.in_(regions),
            MarketSourceRun.started_at >= now - timedelta(days=7),
        )
        .group_by(MarketSourceRun.region)
    ).all()
    by_region = {
        region: {
            "last_run_at": last_run_at,
            "runs": int(runs or 0),
            "error_runs": int(error_runs or 0),
        }
        for region, last_run_at, runs, error_runs in rows
    }
    health = []
    for region in regions:
        stats = by_region.get(region)
        last_run_at = stats["last_run_at"] if stats else None
        if stats and stats["runs"] and stats["runs"] == stats["error_runs"]:
            state = "failed"
        elif last_run_at is None or (now - last_run_at) > stale_after:
            state = "stale"
        else:
            state = "active"
        health.append({
            "region": region,
            "state": state,
            "last_run_at": last_run_at.isoformat() if last_run_at else None,
            "runs_7d": stats["runs"] if stats else 0,
            "error_runs_7d": stats["error_runs"] if stats else 0,
        })
    return health


@api_router.get("/youtube/video-trends")
def list_general_video_trends(
    region: str | None = Query(default=None, min_length=2, max_length=2),
    period_days: int = Query(default=7, ge=1, le=30),
    category: str | None = Query(default=None, max_length=16),
    min_duration_seconds: int | None = Query(default=None, ge=0),
    max_duration_seconds: int | None = Query(default=None, ge=0),
    min_age_hours: int | None = Query(default=None, ge=0),
    max_age_hours: int | None = Query(default=None, ge=0),
    min_views: int = Query(default=0, ge=0),
    min_engagement: int = Query(default=0, ge=0),
    sort: str = Query(default="rank", pattern="^(rank|rank_gain|views|velocity|engagement|region_breadth|streak|new_entries)$"),
    limit: int = Query(default=100, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    """A separate general-YouTube chart, intentionally not a Shorts feed.

    The dedicated lane rotates official regional charts, independent from the
    older Shorts-market chart. The legacy chart is a short-lived fallback
    while the new 110-region lane completes its first cycle.
    """
    now = datetime.now(UTC)
    cutoff = now - timedelta(days=period_days)
    selected_region = region.upper() if region else None
    # "0" is YouTube's own all-categories sentinel, not a real category id.
    # Treating it as a filter silently emptied the chart.
    selected_category = category if category and category != "0" else None
    rows = db.execute(
        select(MarketVideo, MarketVideoObservation)
        .join(MarketVideoObservation, MarketVideoObservation.market_video_id == MarketVideo.id)
        .where(
            MarketVideoObservation.source_lane == "official_general_chart",
            MarketVideoObservation.observed_at >= cutoff,
            # General Video Trends is a long-form sibling, not a second
            # pathway for uncertain Shorts. A row only appears after the
            # strict Shorts verifier has ruled it out (duration or landscape).
            MarketVideo.shorts_status == "REJECTED_NOT_SHORTS",
        )
        .order_by(desc(MarketVideoObservation.observed_at))
    ).all()

    grouped: dict[int, dict] = {}
    for video, observation in rows:
        if selected_region and observation.region != selected_region:
            continue
        item = grouped.setdefault(video.id, {"video": video, "observations": [], "regions": set()})
        item["observations"].append(observation)
        if observation.region:
            item["regions"].add(observation.region)

    items = []
    for item in grouped.values():
        observations = item["observations"]
        # One video can occur in multiple legacy-category requests at the same
        # scan. Prefer the dedicated all-category chart, then keep its best
        # rank per region+scan so the row is not inflated by duplicate lanes.
        by_scan: dict[tuple[datetime, str | None], MarketVideoObservation] = {}
        for observation in observations:
            key = (observation.observed_at, observation.region)
            current = by_scan.get(key)
            is_dedicated = observation.source_lane == "official_general_chart"
            current_dedicated = bool(current and current.source_lane == "official_general_chart")
            if current is None or (is_dedicated and not current_dedicated) or (is_dedicated == current_dedicated and (observation.source_rank or 9999) < (current.source_rank or 9999)):
                by_scan[key] = observation
        scans = sorted(by_scan.values(), key=lambda value: value.observed_at, reverse=True)
        if not scans:
            continue
        latest = scans[0]
        # Rank and growth comparisons must stay inside same regional chart.
        same_region_scans = [scan for scan in scans if scan.region == latest.region]
        previous = same_region_scans[1] if len(same_region_scans) > 1 else None
        video = item["video"]
        days = {scan.observed_at.date().isoformat() for scan in scans}
        latest_rank = latest.source_rank
        rank_change = None
        if latest_rank is not None and previous and previous.source_rank is not None:
            rank_change = previous.source_rank - latest_rank
        duration_seconds = _duration_seconds(video.duration_iso8601)
        age_hours = ((now - video.published_at).total_seconds() / 3600) if video.published_at else None
        engagement = (latest.like_count or 0) + (latest.comment_count or 0)
        if selected_category and video.category_id != selected_category:
            continue
        if min_duration_seconds is not None and duration_seconds < min_duration_seconds:
            continue
        if max_duration_seconds is not None and duration_seconds > max_duration_seconds:
            continue
        if min_age_hours is not None and (age_hours is None or age_hours < min_age_hours):
            continue
        if max_age_hours is not None and (age_hours is None or age_hours > max_age_hours):
            continue
        if latest.view_count < min_views or engagement < min_engagement:
            continue
        # Calculate growth from oldest observation in same regional chart.
        oldest = same_region_scans[-1] if same_region_scans else latest
        views_gained = latest.view_count - (oldest.view_count or 0)
        observation_span_hours = (latest.observed_at - oldest.observed_at).total_seconds() / 3600
        # A single scan cannot prove a growth rate. Floor the divider at one
        # hour so one observation reports its delta without inventing speed.
        elapsed_days = max(observation_span_hours / 24, 1 / 24)
        velocity_per_day = views_gained / elapsed_days
        items.append({
            "video_id": video.video_id,
            "title": video.title,
            "channel_title": video.channel_title,
            "video_url": video.video_url,
            "thumbnail_url": video.thumbnail_url,
            "published_at": video.published_at.isoformat() if video.published_at else None,
            "duration": _duration_label(video.duration_iso8601),
            "view_count": latest.view_count,
            "views_gained": views_gained,
            "velocity_per_day": round(velocity_per_day),
            "velocity_per_hour": round(velocity_per_day / 24),
            "observation_span_hours": round(observation_span_hours, 2),
            "observation_count": len(same_region_scans),
            "like_count": latest.like_count,
            "comment_count": latest.comment_count,
            "engagement": engagement,
            "duration_seconds": duration_seconds,
            "age_hours": round(age_hours, 2) if age_hours is not None else None,
            "rank": latest_rank,
            "rank_change": rank_change,
            "tracked_regions": sorted(item["regions"]),
            "region_count": len(item["regions"]),
            "observed_days": len(days),
            "last_observed_at": latest.observed_at.isoformat(),
            "source": "Official YouTube popular chart",
            "format": "non_shorts",
        })
    sort_keys = {
        "rank": lambda item: (item["rank"] is None, item["rank"] or 9999),
        "rank_gain": lambda item: (-(item["rank_change"] or 0), item["rank"] is None, item["rank"] or 9999),
        "views": lambda item: -item["velocity_per_day"],
        "velocity": lambda item: -item["velocity_per_day"],
        "engagement": lambda item: -item["engagement"],
        "region_breadth": lambda item: (-item["region_count"], item["rank"] is None, item["rank"] or 9999),
        "streak": lambda item: (-item["observed_days"], item["rank"] is None, item["rank"] or 9999),
        "new_entries": lambda item: (item["observed_days"] != 1, item["observed_days"], item["last_observed_at"] is None, item["last_observed_at"] or ""),
    }
    items.sort(key=sort_keys[sort])
    status = SeedStore().status()
    tracked_regions = sorted({region for item in items for region in item["tracked_regions"]})
    total = len(items)
    page = items[offset : offset + limit]
    return {
        "items": page,
        "tracked_regions": tracked_regions,
        "region_health": _general_region_health(db, tracked_regions, now=now),
        "pagination": {
            "total": total,
            "limit": limit,
            "offset": offset,
            "returned": len(page),
            "has_more": offset + len(page) < total,
        },
        "period_days": period_days,
        "filters": {
            "region": selected_region,
            "category": selected_category,
            "min_duration_seconds": min_duration_seconds,
            "max_duration_seconds": max_duration_seconds,
            "min_age_hours": min_age_hours,
            "max_age_hours": max_age_hours,
            "min_views": min_views,
            "min_engagement": min_engagement,
            "sort": sort,
            "offset": offset,
            "limit": limit,
        },
        "coverage": {
            "target_regions": int(status.get("general_video_chart_target_regions", settings.market_general_chart_target_regions)),
            "catalog_regions": int(status.get("general_video_chart_catalog_regions", 0)),
            "estimated_cycle_minutes": int(status.get("general_video_chart_estimated_cycle_minutes", 0)),
            "last_scan_at": status.get("general_video_chart_last_scan_at"),
            "state": status.get("general_video_chart_state", "waiting"),
        },
        "methodology": "General-video market chart from YouTube's official regional mostPopular endpoint. The dedicated collector rotates through up to 110 YouTube-supported regions and is deliberately isolated from Early Topic Signals and Shorts Trending Topics. 'Observed days' counts this system's chart observations, not an official YouTube-wide streak.",
    }



@api_router.get("/youtube/video-trends/{video_id}/history")
def video_trend_history(
    video_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> dict:
    """Return observation history for one video (rank, region, views, likes, comments)."""
    video = db.scalar(select(MarketVideo).where(MarketVideo.video_id == video_id))
    if not video:
        return {"video": None, "observations": []}
    observations = db.scalars(
        select(MarketVideoObservation)
        .where(MarketVideoObservation.market_video_id == video.id)
        .order_by(desc(MarketVideoObservation.observed_at))
        .limit(limit)
    ).all()
    rows = []
    for obs in observations:
        rows.append({
            "observed_at": obs.observed_at.isoformat() if obs.observed_at else None,
            "region": obs.region,
            "source_lane": obs.source_lane,
            "source_rank": obs.source_rank,
            "view_count": obs.view_count,
            "like_count": obs.like_count,
            "comment_count": obs.comment_count,
            "raw_payload": obs.raw_payload,
        })
    return {
        "video": {
            "video_id": video.video_id,
            "title": video.title,
            "thumbnail_url": video.thumbnail_url,
            "channel_title": video.channel_title,
            "video_url": video.video_url,
            "duration": _duration_label(video.duration_iso8601),
            "published_at": video.published_at.isoformat() if video.published_at else None,
            "view_count": video.view_count,
            "rank": video.rank,
            "rank_change": video.rank_change,
            "velocity_per_hour": video.velocity_per_hour,
            "velocity_per_day": video.velocity_per_day,
            "tracked_regions": sorted({obs.region for obs in observations if obs.region}),
            "region_count": len({obs.region for obs in observations if obs.region}),
            "observed_days": len({obs.observed_at.date().isoformat() for obs in observations if obs.observed_at}),
            "last_observed_at": observations[0].observed_at.isoformat() if observations and observations[0].observed_at else None,
        },
        "observations": rows,
    }



@api_router.get("/admin/apify")
def get_apify_config() -> dict:
    """Return runtime Apify actor config (from Redis status or env defaults)."""
    store = SeedStore()
    status = store.status()
    actor = status.get("apify_actor_id") or settings.apify_actor_id or None
    enabled = status.get("apify_enabled")
    if enabled is None:
        enabled = str(settings.apify_enabled)
    return {"actor_id": actor, "enabled": bool(str(enabled).lower() in ("1", "true", "yes"))}


@api_router.post("/admin/apify")
def set_apify_config(payload: dict, db: Session = Depends(get_db)) -> dict:
    """Set runtime Apify actor config. Payload: {"actor_id": "owner/actor", "enabled": true|false}"""
    actor = payload.get("actor_id")
    enabled = payload.get("enabled")
    store = SeedStore()
    values = {}
    if actor is not None:
        values["apify_actor_id"] = actor
    if enabled is not None:
        values["apify_enabled"] = str(bool(enabled))
    if values:
        store.set_status(**values)
    return {"status": "ok", "apify_actor_id": actor, "apify_enabled": bool(str(enabled).lower() in ("1", "true", "yes")) if enabled is not None else None}


@api_router.get("/admin/apify/costs")
def admin_apify_costs(days: int = Query(default=7, ge=1, le=90), db: Session = Depends(get_db)) -> dict:
    """Return recent Apify spend from the `cost_ledger` table grouped by day and actor_id. If the table is missing, return a helpful error."""
    from datetime import UTC, datetime, timedelta
    from sqlalchemy import text

    cutoff = datetime.now(UTC) - timedelta(days=days)
    try:
        q = text(
            "SELECT date_trunc('day', created_at) AS day, coalesce(actor_id, '') AS actor_id, coalesce(sum(amount_usd),0) AS total_usd "
            "FROM cost_ledger WHERE created_at >= :cutoff GROUP BY day, actor_id ORDER BY day DESC"
        )
        res = db.execute(q, {"cutoff": cutoff})
        rows = []
        total = 0.0
        for r in res.mappings():
            day = r["day"].isoformat() if r["day"] is not None else None
            actor = r["actor_id"]
            amt = float(r["total_usd"] or 0.0)
            total += amt
            rows.append({"day": day, "actor_id": actor, "amount_usd": round(amt, 6)})
        return {"available": True, "days": days, "total_usd": round(total, 6), "rows": rows}
    except Exception as exc:
        return {"available": False, "error": str(exc), "message": "cost_ledger table not available or query failed"}


@api_router.post("/admin/apify/trigger")
def admin_apify_trigger(db: Session = Depends(get_db), _admin: bool = Depends(require_admin)) -> dict:
    """Enqueue a market Apify shorts collection tick. This creates a Celery task
    invocation; the actual work runs in workers and requires configured Apify credentials."""
    try:
        # Use send_task so the HTTP server does not need direct task function import
        async_result = celery_app.send_task("app.tasks.market_apify_tasks.collect_apify_shorts")
        return {"status": "enqueued", "task_id": str(async_result.id)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to enqueue task: {exc}")

def _ranked_topic_payload(topic: MarketRankedTopic, members: list[MarketVideo], snapshots: list[MarketRankedTopicSnapshot]) -> dict:
    quality = topic.quality_flags or {}
    is_v2 = topic.topic_key.startswith("v2|")
    if not is_v2:
        # Existing evidence remains visible during V2 rollout. It is clearly
        # labelled provisional rather than silently deleted or promoted.
        quality = {**quality, "legacy_provisional": True, "history_ready": len(snapshots) >= 3, "publication_rule": "Legacy evidence; awaiting V2 entity/context verification"}
    return {
        "id": topic.id, "label": topic.label, "topic_type": topic.topic_type,
        "status": topic.status, "category_key": topic.category_key,
        "semantic_confidence": topic.semantic_confidence, "trend_score": topic.trend_score,
        "observed_views": topic.observed_views, "observed_velocity_per_hour": topic.observed_velocity_per_hour,
        "organic_velocity_per_hour": topic.organic_velocity_per_hour,
        "acceleration": topic.acceleration, "member_count": topic.member_count,
        "channel_count": topic.channel_count, "region_count": topic.region_count,
        "entity_signature": topic.entity_signature, "context_signature": topic.context_signature,
        "semantic_summary": topic.semantic_summary, "source_mix": topic.source_mix or {},
          "quality_flags": quality, "history_ready": bool(quality.get("history_ready")), "quality_version": "v2" if is_v2 else "legacy_provisional",
        "content_truth": quality.get("content_truth", {"status": "NOT_REQUIRED"}),
        "freshness": quality.get("freshness", {}),
        "why_moving": {"new_member_count": quality.get("new_member_count", 0), "source_count": quality.get("source_count", 0), "entity_verified": bool(quality.get("entity_verified"))},
        "last_observed_at": topic.last_observed_at.isoformat() if topic.last_observed_at else None,
        "evidence": [{"video_id": item.video_id, "title": item.title, "thumbnail_url": item.thumbnail_url, "channel_title": item.channel_title, "video_url": item.video_url} for item in members[:6]],
        "snapshots": [{"observed_at": item.observed_at.isoformat(), "trend_score": item.trend_score, "observed_velocity_per_hour": item.observed_velocity_per_hour, "organic_velocity_per_hour": item.organic_velocity_per_hour, "organic_measurement_ready": item.organic_measurement_ready, "observed_views": item.observed_views, "new_member_count": item.new_member_count, "source_count": item.source_count, "history_ready": item.history_ready} for item in snapshots],
    }


@api_router.get("/youtube/market/ranked-topics")
def list_market_ranked_topics(
    region: str | None = Query(default=None, min_length=2, max_length=2),
    category: str | None = Query(default=None, max_length=32),
    include_legacy: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> dict:
    """Topic-first ranking built only from observed official-chart Shorts."""
    topics = db.scalars(
        select(MarketRankedTopic)
        .where(MarketRankedTopic.status.in_(("THEME", "EMERGING", "ACCELERATING", "CONFIRMED")))
        .order_by(desc(MarketRankedTopic.trend_score), desc(MarketRankedTopic.observed_views))
        .limit(50)
    ).all()
    items = []
    region = region.upper() if region else None
    for topic in topics:
        if not include_legacy and not topic.topic_key.startswith("v2|"):
            continue
        members = db.scalars(
            select(MarketVideo)
            .join(MarketRankedTopicMembership, MarketRankedTopicMembership.market_video_id == MarketVideo.id)
            .where(
                MarketRankedTopicMembership.market_ranked_topic_id == topic.id,
                MarketRankedTopicMembership.evidence_role == "active_evidence",
                MarketVideo.shorts_status == "VERIFIED_SHORTS",
            )
        ).all()
        # Never expose an old cluster whose current evidence no longer passes
        # the canonical Shorts format gate.
        if len(members) < 3 or len({member.channel_id for member in members if member.channel_id}) < 2:
            continue
        category_groups = {
            "entertainment": {"entertainment", "cultural_moment", "product_trend"},
            "music": {"music"},
              "sports": {"sports", "sports_theme"},
            "news": {"news_event", "politics"},
        }
        if category and topic.topic_type not in category_groups.get(category, {category}):
            continue
        if region:
            observed_regions = set(db.scalars(
                select(MarketVideoObservation.region)
                .where(MarketVideoObservation.market_video_id.in_([item.id for item in members]), MarketVideoObservation.source_lane == "official_chart")
            ).all())
            if region not in observed_regions:
                continue
        snapshots = db.scalars(
            select(MarketRankedTopicSnapshot)
            .where(MarketRankedTopicSnapshot.market_ranked_topic_id == topic.id)
            .order_by(desc(MarketRankedTopicSnapshot.observed_at)).limit(12)
        ).all()
        items.append(_ranked_topic_payload(topic, members, list(reversed(snapshots))))
    return {"items": items, "methodology": "Only Shorts published in the active 72-hour window power this ranking. Older videos remain as historical audit evidence but cannot keep a topic on the public leaderboard. Organic growth counts only view changes from Shorts that were already active on the prior scan; newly added Shorts are recorded separately as new evidence. Event claims also require title-to-content verification; mismatched metadata is quarantined rather than shown as a trend."}


@api_router.get("/youtube/market/ranked-topics/{topic_id}")
def get_market_ranked_topic(topic_id: int, db: Session = Depends(get_db)) -> dict:
    topic = db.get(MarketRankedTopic, topic_id)
    if not topic or topic.status in {"WATCHING", "AWAITING_CONTENT_VALIDATION", "QUARANTINED_METADATA_MISMATCH"}:
        raise HTTPException(status_code=404, detail="Ranked topic not found")
    members = db.scalars(
        select(MarketVideo)
        .join(MarketRankedTopicMembership, MarketRankedTopicMembership.market_video_id == MarketVideo.id)
        .where(
            MarketRankedTopicMembership.market_ranked_topic_id == topic.id,
            MarketRankedTopicMembership.evidence_role == "active_evidence",
            MarketVideo.shorts_status == "VERIFIED_SHORTS",
        )
    ).all()
    snapshots = db.scalars(
        select(MarketRankedTopicSnapshot)
        .where(MarketRankedTopicSnapshot.market_ranked_topic_id == topic.id)
        .order_by(MarketRankedTopicSnapshot.observed_at).limit(72)
    ).all()
    return {**_ranked_topic_payload(topic, members, snapshots), "methodology": "Evidence Shorts, source provenance, and score history are retained in PostgreSQL. Public topics require independent multi-channel evidence plus a semantic entity/context label; acceleration is withheld until the third observation."}


@api_router.get("/youtube/market/content-truth-review")
def content_truth_review_queue(db: Session = Depends(get_db)) -> dict:
    """Plain-language audit lane for withheld metadata-driven candidates."""
    topics = db.scalars(
        select(MarketRankedTopic)
        .where(MarketRankedTopic.status.in_(("AWAITING_CONTENT_VALIDATION", "QUARANTINED_METADATA_MISMATCH")))
        .order_by(desc(MarketRankedTopic.last_observed_at), desc(MarketRankedTopic.trend_score))
        .limit(20)
    ).all()
    items = []
    for topic in topics:
        members = db.scalars(
            select(MarketVideo)
            .join(MarketRankedTopicMembership, MarketRankedTopicMembership.market_video_id == MarketVideo.id)
            .where(
                MarketRankedTopicMembership.market_ranked_topic_id == topic.id,
                MarketRankedTopicMembership.evidence_role == "active_evidence",
            )
            .limit(6)
        ).all()
        audits = db.scalars(
            select(MarketContentTruthAudit)
            .where(MarketContentTruthAudit.market_video_id.in_([video.id for video in members]))
        ).all() if members else []
        by_video = {audit.market_video_id: audit for audit in audits}
        truth = (topic.quality_flags or {}).get("content_truth", {})
        items.append({
            "id": topic.id,
            "label": topic.label,
            "status": topic.status,
            "truth": truth,
            "members": [{
                "video_id": video.video_id,
                "title": video.title,
                "thumbnail_url": video.thumbnail_url,
                "video_url": video.video_url,
                "audit_status": by_video.get(video.id).status if video.id in by_video else "PENDING",
                "content_summary": by_video.get(video.id).content_summary if video.id in by_video else None,
                "mismatch_reason": by_video.get(video.id).mismatch_reason if video.id in by_video else None,
            } for video in members],
        })
    return {
        "items": items,
        "methodology": "This is a factual safety check, not an accusation of bots or manipulated views. A topic is withheld only when multiple Shorts do not support the claim in their own titles.",
    }


@api_router.post("/youtube/market/ranked-topics/{topic_id}/review")
def review_market_ranked_topic(topic_id: int, payload: RankedTopicReviewInput, db: Session = Depends(get_db)) -> dict:
    """Store auditable feedback for the next calibration pass; evidence is never overwritten."""
    if payload.decision not in RANKED_TOPIC_REVIEW_DECISIONS:
        raise HTTPException(status_code=422, detail=f"decision must be one of: {', '.join(RANKED_TOPIC_REVIEW_DECISIONS)}")
    topic = db.get(MarketRankedTopic, topic_id)
    if not topic:
        raise HTTPException(status_code=404, detail="Ranked topic not found")
    review = MarketRankedTopicReview(market_ranked_topic_id=topic.id, decision=payload.decision, note=payload.note, reviewer=payload.reviewer)
    db.add(review)
    # Persist the reviewer label on the semantic identity as a bounded
    # active-learning guard. It affects only a future reappearance of this
    # exact topic key; it never deletes videos or alters collection sources.
    topic.quality_flags = {**(topic.quality_flags or {}), "human_feedback": payload.decision, "human_feedback_at": datetime.now(UTC).isoformat()}
    # A human rejection is an explicit instruction not to keep retrying this
    # legacy merge for V2 promotion. Raw videos remain intact and auditable.
    if payload.decision in {"WRONG_MERGE", "TOO_GENERIC", "NOT_A_TREND"}:
        topic.status = "WATCHING"
    db.commit()
    return {"id": review.id, "topic_id": topic.id, "decision": review.decision, "created_at": review.created_at.isoformat() if review.created_at else None}


@api_router.get("/youtube/market/ranked-review-queue")
def ranked_topic_review_queue(db: Session = Depends(get_db)) -> dict:
    """One clear decision at a time for V1→V2 quality calibration."""
    reviewed = set(db.scalars(select(MarketRankedTopicReview.market_ranked_topic_id)).all())
    rows = db.scalars(
        select(MarketRankedTopic)
        .where(MarketRankedTopic.status.in_(("EMERGING", "ACCELERATING", "CONFIRMED")))
        .order_by(desc(MarketRankedTopic.trend_score), desc(MarketRankedTopic.member_count))
        .limit(50)
    ).all()
    items = []
    for topic in rows:
        if topic.id in reviewed:
            continue
        members = db.scalars(
            select(MarketVideo)
            .join(MarketRankedTopicMembership, MarketRankedTopicMembership.market_video_id == MarketVideo.id)
            .where(MarketRankedTopicMembership.market_ranked_topic_id == topic.id)
            .limit(6)
        ).all()
        if len(members) < 2:
            continue
        is_v2 = topic.topic_key.startswith("v2|")
        items.append({
            "id": topic.id, "label": topic.label, "status": topic.status,
            "is_v2": is_v2, "member_count": topic.member_count, "channel_count": topic.channel_count,
            "summary": topic.semantic_summary or "Ini label lama V1, belum dipercaya sebagai topik. Bantu nilai apakah semua video memang membahas satu hal yang sama.",
            "instruction": "Jika semua hanya sama-sama sepak bola tetapi membahas pemain, pertandingan, atau cerita berbeda, pilih 'Bukan satu topik' atau 'Terlalu umum'.",
            "members": [{"video_id": video.video_id, "title": video.title, "channel_title": video.channel_title, "video_url": video.video_url, "thumbnail_url": video.thumbnail_url} for video in members],
        })
    decisions = dict(db.execute(select(MarketRankedTopicReview.decision, func.count(MarketRankedTopicReview.id)).group_by(MarketRankedTopicReview.decision)).all())
    legacy_total = sum(1 for row in rows if not row.topic_key.startswith("v2|"))
    promoted = sum(1 for row in rows if row.topic_key.startswith("v2|"))
    return {
        "items": items[:12], "total_pending": len(items),
        "backfill": {"legacy_clusters": legacy_total, "v2_promoted": promoted, "reviewed": len(reviewed), "decisions": decisions},
        "decisions": RANKED_TOPIC_REVIEW_DECISIONS,
        "methodology": "Review is optional human calibration. It never deletes videos or silently changes discovery rules.",
    }


@api_router.get("/youtube/market/metadata-trends")
def list_market_metadata_trends(db: Session = Depends(get_db)) -> dict:
    items = db.scalars(select(MarketMetadataTrend).order_by(desc(MarketMetadataTrend.burst_score)).limit(40)).all()
    pending = sum(1 for item in items if item.semantic_status == "AI_PENDING")
    generic_entity_labels = {"part", "star", "crazy", "family", "movie", "video", "news"}
    provisional_entities = []
    for item in items:
        if item.signal_type != "ENTITY" or item.label.casefold() in generic_entity_labels or item.member_count < 3 or item.channel_count < 3:
            continue
        evidence = db.scalars(
            select(MarketVideo)
            .join(MarketMetadataTrendMembership, MarketMetadataTrendMembership.market_video_id == MarketVideo.id)
            .where(MarketMetadataTrendMembership.market_metadata_trend_id == item.id)
            .limit(5)
        ).all()
        provisional_entities.append({
            "id": item.id, "label": item.label, "member_count": item.member_count,
            "channel_count": item.channel_count, "region_count": item.region_count,
            "fresh_ratio": item.fresh_ratio, "burst_score": item.burst_score,
            "evidence": [{"video_id": video.video_id, "title": video.title, "thumbnail_url": video.thumbnail_url, "channel_title": video.channel_title} for video in evidence],
        })
    return {"items": [{"id": item.id, "label": item.semantic_label, "raw_signal": item.label, "signal_type": item.signal_type, "status": item.status, "followable": item.followable, "confidence": item.semantic_confidence, "member_count": item.member_count, "channel_count": item.channel_count, "region_count": item.region_count, "fresh_ratio": item.fresh_ratio, "burst_score": item.burst_score, "last_observed_at": item.last_observed_at.isoformat() if item.last_observed_at else None} for item in items if item.followable], "provisional_entities": provisional_entities, "pending_semantic_count": pending, "gemini_cooldown": bool(SeedStore().client.exists("ycgc:youtube:market-gemini-cooldown")), "methodology": "Provisional entity clusters are shown only as cross-channel evidence. They are not declared public trends until semantic context and momentum are confirmed."}

@api_router.get("/youtube/market/topics")
def list_market_topics(db: Session = Depends(get_db)) -> dict:
    """Public Market Topics, promoted only with independent cross-channel evidence."""
    rows = []
    private_candidate_count = 0
    provisional_count = 0
    for topic in db.scalars(select(MarketTopic).order_by(desc(MarketTopic.trend_score), desc(MarketTopic.observed_views))).all():
        members = db.scalars(
            select(MarketVideo)
            .join(MarketTopicMembership, MarketTopicMembership.market_video_id == MarketVideo.id)
            .where(MarketTopicMembership.market_topic_id == topic.id)
        ).all()
        if not members:
            continue
        if topic.status == "PRIVATE_CANDIDATE":
            private_candidate_count += 1
            continue
        if topic.status == "PROVISIONAL":
            provisional_count += 1
            # A single repeated title word is useful for human review, but it
            # is never a public, followable trend title.
            continue
        if topic.status not in PUBLIC_TREND_STATUSES or topic.label.casefold().strip() in NON_FOLLOWABLE_TOPIC_LABELS:
            continue
        snapshots = db.scalars(
            select(MarketTopicSnapshot)
            .where(MarketTopicSnapshot.market_topic_id == topic.id)
            .order_by(desc(MarketTopicSnapshot.observed_at))
            .limit(8)
        ).all()
        rows.append({
            "id": topic.id,
            "label": topic.label,
            "status": topic.status,
            "trend_score": topic.trend_score,
            "observed_views": topic.observed_views,
            "observed_velocity_per_hour": topic.observed_velocity_per_hour,
            "acceleration": topic.acceleration,
            "member_count": topic.member_count,
            "channel_count": topic.channel_count,
            "evidence": [{"video_id": v.video_id, "title": v.title, "thumbnail_url": v.thumbnail_url, "channel_title": v.channel_title} for v in members[:5]],
            "snapshots": [{"observed_at": item.observed_at.isoformat(), "trend_score": item.trend_score, "observed_velocity_per_hour": item.observed_velocity_per_hour} for item in reversed(snapshots)],
        })
    return {
        "items": rows,
        "private_candidate_count": private_candidate_count,
        "provisional_count": provisional_count,
        "methodology": "Public topics require a meaningful semantic label plus independent evidence. Raw title-overlap candidates remain in the private review queue and are never presented as followable trends. Views and momentum are observed in this system, not YouTube-wide totals.",
    }


@api_router.get("/youtube/market/topics/{topic_id}")
def get_market_topic(topic_id: int, db: Session = Depends(get_db)) -> dict:
    """One topic's auditable momentum history and its underlying Shorts."""
    topic = db.get(MarketTopic, topic_id)
    if not topic or topic.status == "PRIVATE_CANDIDATE":
        raise HTTPException(status_code=404, detail="Public Market Topic not found")
    members = db.scalars(
        select(MarketVideo)
        .join(MarketTopicMembership, MarketTopicMembership.market_video_id == MarketVideo.id)
        .where(MarketTopicMembership.market_topic_id == topic.id)
    ).all()
    latest_observations: dict[int, MarketVideoObservation] = {}
    for observation in db.scalars(select(MarketVideoObservation).order_by(desc(MarketVideoObservation.observed_at))).all():
        latest_observations.setdefault(observation.market_video_id, observation)
    source_mix: dict[str, int] = {}
    region_mix: dict[str, int] = {}
    last_source_observed_at = None
    for video in members:
        observation = latest_observations.get(video.id)
        if not observation:
            continue
        source_mix[observation.source_lane] = source_mix.get(observation.source_lane, 0) + 1
        if observation.region:
            region_mix[observation.region] = region_mix.get(observation.region, 0) + 1
        if last_source_observed_at is None or observation.observed_at > last_source_observed_at:
            last_source_observed_at = observation.observed_at
    snapshots = db.scalars(
        select(MarketTopicSnapshot)
        .where(MarketTopicSnapshot.market_topic_id == topic.id)
        .order_by(MarketTopicSnapshot.observed_at)
        .limit(48)
    ).all()
    return {
        "id": topic.id,
        "label": topic.label,
        "status": topic.status,
        "trend_score": topic.trend_score,
        "observed_views": topic.observed_views,
        "observed_velocity_per_hour": topic.observed_velocity_per_hour,
        "acceleration": topic.acceleration,
        "member_count": topic.member_count,
        "channel_count": topic.channel_count,
        "last_observed_at": (topic.last_observed_at or last_source_observed_at).isoformat() if (topic.last_observed_at or last_source_observed_at) else None,
        "source_mix": source_mix,
        "region_mix": region_mix,
        "snapshots": [{"observed_at": item.observed_at.isoformat(), "observed_views": item.observed_views, "trend_score": item.trend_score, "observed_velocity_per_hour": item.observed_velocity_per_hour} for item in snapshots],
        "members": [{
            "video_id": video.video_id, "title": video.title, "channel_title": video.channel_title,
            "video_url": video.video_url, "thumbnail_url": video.thumbnail_url,
            "view_count": latest_observations.get(video.id).view_count if latest_observations.get(video.id) else 0,
        } for video in sorted(members, key=lambda item: latest_observations.get(item.id).view_count if latest_observations.get(item.id) else 0, reverse=True)],
        "methodology": "Evidence cards are verified Shorts in this topic. Momentum is derived from repeated observed public-source measurements, not a YouTube-wide total. Source and region counts show only the coverage observed by this system.",
    }


@api_router.get("/youtube/market/review-queue")
def market_topic_review_queue(db: Session = Depends(get_db)) -> dict:
    """Prioritize uncertain Market Topics; feedback never changes discovery."""
    reviewed_ids = set(db.scalars(select(MarketTopicFeedback.market_topic_id)).all())
    items = []
    for topic in db.scalars(select(MarketTopic).where(MarketTopic.status != "PRIVATE_CANDIDATE")).all():
        if topic.id in reviewed_ids or topic.member_count < 2:
            continue
        members = db.scalars(
            select(MarketVideo)
            .join(MarketTopicMembership, MarketTopicMembership.market_video_id == MarketVideo.id)
            .where(MarketTopicMembership.market_topic_id == topic.id)
        ).all()
        uncertainty = 0.95 if topic.status == "PROVISIONAL" else max(0.15, min(0.85, 1 - (topic.trend_score / 100)))
        items.append({
            "id": topic.id, "label": topic.label, "status": topic.status,
            "member_count": topic.member_count, "channel_count": topic.channel_count,
            "review_uncertainty": round(uncertainty, 2),
            "review_reason": "title-overlap requires semantic validation" if topic.status == "PROVISIONAL" else "semantic topic near calibration boundary",
            "members": [{"video_id": video.video_id, "title": video.title, "channel_title": video.channel_title, "video_url": video.video_url, "thumbnail_url": video.thumbnail_url} for video in members[:8]],
        })
    items.sort(key=lambda item: item["review_uncertainty"], reverse=True)
    return {"items": items[:30], "decisions": REVIEW_DECISIONS, "methodology": "Feedback is immutable calibration evidence for Market Topic grouping. It never alters anonymous discovery inputs, removes raw evidence, or automatically changes a production threshold."}


@api_router.post("/youtube/market/topics/{topic_id}/feedback")
def submit_market_topic_feedback(topic_id: int, payload: TopicFeedbackInput, db: Session = Depends(get_db)) -> dict:
    if payload.decision not in REVIEW_DECISIONS:
        raise HTTPException(status_code=422, detail="Unsupported review decision")
    topic = db.get(MarketTopic, topic_id)
    if not topic:
        raise HTTPException(status_code=404, detail="Market Topic not found")
    snapshot_count = db.scalar(select(func.count(MarketTopicSnapshot.id)).where(MarketTopicSnapshot.market_topic_id == topic_id)) or 0
    feedback = MarketTopicFeedback(market_topic_id=topic_id, reviewer=payload.reviewer.strip(), decision=payload.decision, note=payload.note, feature_model="market-gemini-v2/title-overlap", snapshot_count_at_review=snapshot_count)
    db.add(feedback)
    db.commit()
    db.refresh(feedback)
    return {"id": feedback.id, "decision": feedback.decision, "message": "Market Topic feedback stored for calibration. No discovery input or threshold was changed automatically."}


@api_router.get("/youtube/market/review-calibration")
def market_topic_review_calibration(db: Session = Depends(get_db)) -> dict:
    """Report-only calibration summary; a human must accept any later change."""
    rows = db.execute(select(MarketTopicFeedback.decision, func.count(MarketTopicFeedback.id)).group_by(MarketTopicFeedback.decision)).all()
    totals = {decision: count for decision, count in rows}
    total = sum(totals.values())
    confirmed = totals.get("CONFIRM_CLUSTER", 0)
    rejected = totals.get("REJECT_CLUSTER", 0)
    split = totals.get("SPLIT_NEEDED", 0)
    actionable = confirmed + rejected + split
    precision = round(confirmed / actionable, 3) if actionable else None
    recommendation = "Collect at least 20 reviews before proposing a calibration change."
    if total >= 20:
        if rejected + split > confirmed:
            recommendation = "Review semantic similarity and title-overlap exclusion rules; false merges exceed confirmed topics."
        elif precision is not None and precision >= 0.75:
            recommendation = "Current clustering quality is acceptable in this reviewed sample; continue collecting evidence before any threshold change."
        else:
            recommendation = "Inspect rejected/split evidence by source lane before proposing a targeted clustering adjustment."
    return {"total_reviews": total, "decisions": totals, "actionable_reviews": actionable, "confirmed_precision": precision, "ready_for_recommendation": total >= 20, "recommendation": recommendation, "methodology": "This is a report-only calibration summary. A human must explicitly approve any later configuration change; no discovery or threshold is changed here."}


@api_router.get("/youtube/watchlist")
def list_youtube_watchlist() -> dict:
    """Read-only seed view: transparent activity, explicitly not a signal."""
    store = SeedStore()
    now = datetime.now(UTC)
    items = []
    for video_id in store.list_ids(limit=50):
        seed = store.get(video_id)
        if not seed:
            continue
        age_hours = (now - seed.published_at).total_seconds() / 3600
        if age_hours >= settings.youtube_seed_max_age_hours:
            continue
        snapshots = store.snapshots(video_id)
        latest_snapshot = snapshots[-1] if snapshots else None
        last_observed = datetime.fromisoformat(latest_snapshot["observed_at"]) if latest_snapshot else seed.seeded_at
        if age_hours < settings.youtube_ultra_fresh_max_age_hours:
            cadence_minutes, freshness_lane = settings.youtube_ultra_fresh_poll_minutes, "ULTRA FRESH"
        elif age_hours < settings.youtube_fast_poll_max_age_hours:
            cadence_minutes, freshness_lane = settings.youtube_fast_poll_minutes, "FRESH"
        else:
            cadence_minutes, freshness_lane = settings.youtube_mature_poll_minutes, "FRESH"
        source_parts = seed.source.split(":")
        items.append({
            "video_id": seed.video_id,
            "title": seed.title,
            "channel_title": seed.channel_title,
            "video_url": seed.video_url,
            "thumbnail_url": seed.thumbnail_url,
            "published_at": seed.published_at.isoformat(),
            "seed_view_count": seed.seed_view_count,
            "latest_view_count": int(latest_snapshot["view_count"]) if latest_snapshot else seed.seed_view_count,
            "observations": len(snapshots),
            "required_observations": 2,
            "age_minutes": round(age_hours * 60),
            "freshness_lane": freshness_lane,
            "next_observation_at": (last_observed + timedelta(minutes=cadence_minutes)).isoformat(),
            "profile": source_parts[1] if len(source_parts) > 1 else "anonymous",
        })
    return {"items": sorted(items, key=lambda item: item["published_at"], reverse=True)}


@api_router.get("/youtube/observation-report")
def youtube_observation_report() -> dict:
    """A bounded 24-hour evidence report for the thesis and threshold review."""
    store = SeedStore()
    report = store.observation_report()
    raw_seen = report.get("raw_candidates_seen", 0)
    fresh = report.get("fresh_accepted", 0)
    age_buckets = {bucket: report.get(f"age_{bucket}", 0) for bucket in ("0-2h", "2-6h", "6-12h", "12-24h")}
    transitions = {key.removeprefix("transition_"): value for key, value in report.items() if key.startswith("transition_")}
    states = {state: report.get(f"state_{state}", 0) for state in ("WATCH", "EARLY", "RISING", "BREAKOUT", "COOLED")}
    profiles = [
        {"profile": f"{region}/{language}", "coverage_24h": store.coverage(f"{region}_{language}")}
        for region, language in settings.youtube_profile_list
    ]
    sample_counts = {bucket: store.velocity_sample_count(bucket) for bucket in age_buckets}
    return {
        "window": "rolling_24h",
        "raw_candidates_seen": raw_seen,
        "fresh_accepted": fresh,
        "fresh_rate_percent": round(100 * fresh / raw_seen, 2) if raw_seen else 0.0,
        "duplicates": report.get("duplicates", 0),
        "rejected_old": report.get("rejected_old", 0),
        "errors": {"discovery": report.get("discovery_errors", 0), "media": report.get("media_errors", 0), "enrichment": report.get("enrichment_errors", 0)},
        "age_buckets": age_buckets,
        "tier_states": states,
        "tier_transitions": transitions,
        "velocity_samples_by_age": sample_counts,
        "relative_scoring": {"enabled": settings.youtube_relative_scoring_enabled, "minimum_samples": settings.youtube_relative_min_samples},
        "profiles": profiles,
    }


@api_router.get("/youtube/trends")
def list_youtube_trends(
    limit: int = Query(default=30, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    """Public, cross-channel topic trends only.

    Private one-video candidates are deliberately excluded: seeing a signal is
    not enough to claim that a topic is spreading.
    """
    clusters = db.scalars(
        select(TrendCluster)
        .where(TrendCluster.status.in_(PUBLIC_TREND_STATUSES))
        .order_by(desc(TrendCluster.trend_score), desc(TrendCluster.last_observed_at))
        .limit(limit)
    ).all()
    private_candidate_count = db.scalar(
        select(__import__("sqlalchemy").func.count(TrendCluster.id)).where(
            TrendCluster.status == "PRIVATE_CANDIDATE"
        )
    ) or 0
    return {
        "items": [_trend_payload(db, cluster) for cluster in clusters],
        "private_candidate_count": private_candidate_count,
        "methodology": "Only clusters with independent cross-channel evidence are shown. Views and velocity are observed within this system, not YouTube-wide totals.",
    }


@api_router.get("/youtube/early-topics")
def list_early_topic_signals(
    limit: int = Query(default=30, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    """Early topic leaderboard sourced from fresh, low-view signals.

    Unlike Market Trending Topics, this endpoint is intentionally predictive:
        it ranks cross-channel conversations before the broader chart lane
    has confirmed them. Individual Shorts are evidence, never ranking rows.
    """
    all_clusters = db.scalars(select(TrendCluster).where(TrendCluster.status != "MERGED")).all()
    clusters = sorted(
        (cluster for cluster in all_clusters if cluster.status in PUBLIC_TREND_STATUSES),
        key=lambda cluster: (cluster.trend_score, cluster.last_observed_at or cluster.first_detected_at),
        reverse=True,
    )[:100]
    items = []
    for cluster in clusters:
        summary = cluster.evidence_summary or {}
        metadata = cluster.model_metadata or {}
        if summary.get("early_member_count", 0) < 2 or summary.get("early_channel_count", 0) < 2:
            continue
        if not metadata.get("early_topic_named") or (cluster.label_confidence or 0) < .70:
            continue
        payload = _trend_payload(db, cluster)
        payload["prediction_state"] = (metadata.get("outcome") or {}).get("state", "PENDING")
        payload["early_member_count"] = summary.get("early_member_count", 0)
        payload["early_channel_count"] = summary.get("early_channel_count", 0)
        items.append(payload)
    live_early_clusters = [
        cluster for cluster in all_clusters
        if (cluster.evidence_summary or {}).get("early_phase") not in {"EXPIRED", None}
        and (cluster.evidence_summary or {}).get("lifecycle_age_hours", settings.early_topic_lifecycle_hours + 1)
        <= settings.early_topic_lifecycle_hours
    ]
    diagnostics = {
        "active_seeds": len(SeedStore().list_ids()),
        "clusters_observed": len(all_clusters),
        "cross_channel_candidates": sum(
            1 for cluster in live_early_clusters
            if (cluster.evidence_summary or {}).get("early_member_count", 0) >= 2
            and (cluster.evidence_summary or {}).get("early_channel_count", 0) >= 2
        ),
        "named_candidates": sum(1 for cluster in live_early_clusters if (cluster.model_metadata or {}).get("early_topic_named")),
        "public_topics": len(items),
    }
    return {"items": items[:limit], "diagnostics": diagnostics, "methodology": "Early Topic Signals require at least two fresh, low-view Shorts from two independent channels. Channel size is recorded for analysis but never used as a gate. A later match to Market Trending Topics is stored as an auditable outcome for calibration; it does not automatically change rules."}


@api_router.get("/youtube/early-topics/evaluation")
def early_topic_evaluation(db: Session = Depends(get_db)) -> dict:
    """Auditable active-learning report; outcomes never tune production automatically."""
    clusters = db.scalars(select(TrendCluster).where(TrendCluster.model_metadata.is_not(None))).all()
    outcomes = {"PENDING": 0, "VALIDATED_BY_MARKET": 0, "COOLED_WITHOUT_MARKET": 0}
    validation_hours: list[float] = []
    for cluster in clusters:
        metadata = cluster.model_metadata or {}
        if metadata.get("early_topic_policy") != "fresh_low_view_cross_channel":
            continue
        state = (metadata.get("outcome") or {}).get("state", "PENDING")
        outcomes[state] = outcomes.get(state, 0) + 1
        checked_at = (metadata.get("outcome") or {}).get("checked_at")
        if state == "VALIDATED_BY_MARKET" and checked_at and cluster.first_detected_at:
            try:
                checked = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
                validation_hours.append(max(0, (checked - cluster.first_detected_at).total_seconds() / 3600))
            except ValueError:
                pass
    resolved = outcomes["VALIDATED_BY_MARKET"] + outcomes["COOLED_WITHOUT_MARKET"]
    ranked_reviews = dict(db.execute(
        select(MarketRankedTopicReview.decision, func.count(MarketRankedTopicReview.id)).group_by(MarketRankedTopicReview.decision)
    ).all())
    content_truth = dict(db.execute(
        select(MarketContentTruthAudit.status, func.count(MarketContentTruthAudit.id)).group_by(MarketContentTruthAudit.status)
    ).all())
    topic_feedback = dict(db.execute(
        select(MarketTopicFeedback.decision, func.count(MarketTopicFeedback.id)).group_by(MarketTopicFeedback.decision)
    ).all())
    benchmark = dict(db.execute(
        select(ExternalTrendBenchmark.match_status, func.count(ExternalTrendBenchmark.id)).group_by(ExternalTrendBenchmark.match_status)
    ).all())
    return {"outcomes": outcomes, "resolved": resolved, "precision_when_resolved": round(outcomes["VALIDATED_BY_MARKET"] / resolved, 3) if resolved else None, "average_hours_until_market_validation": round(sum(validation_hours) / len(validation_hours), 1) if validation_hours else None, "ready_for_threshold_calibration": resolved >= 30, "minimum_resolved_for_calibration": 30, "quality_events": {"ranked_topic_reviews": ranked_reviews, "content_truth": content_truth, "market_topic_feedback": topic_feedback, "external_benchmarks": benchmark}, "methodology": "A positive outcome means a later semantic match to a public Market Trending Topic. The timing measures elapsed observation time in this system, not all of YouTube. Outcomes are learning data for a later human-approved calibration, never an automatic self-modifying rule."}


@api_router.get("/youtube/benchmarks")
def list_external_benchmarks(db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(select(ExternalTrendBenchmark).order_by(desc(ExternalTrendBenchmark.observed_on), desc(ExternalTrendBenchmark.id)).limit(100)).all()
    captured = sum(1 for row in rows if row.match_status == "CAPTURED")
    resolved = sum(1 for row in rows if row.match_status in {"CAPTURED", "NOT_CAPTURED", "NO_LOCAL_TOPICS"})
    return {"items": [{"id": row.id, "source": row.source, "label": row.label, "observed_on": row.observed_on.isoformat(), "region": row.region, "category": row.category, "source_rank": row.source_rank, "source_url": row.source_url, "note": row.note, "match_status": row.match_status, "match_confidence": row.match_confidence, "matched_topic_id": row.matched_ranked_topic_id} for row in rows], "summary": {"total": len(rows), "captured": captured, "resolved": resolved, "capture_rate": round(captured / resolved, 3) if resolved else None}, "methodology": "Benchmarks are analyst-entered observations from another platform on the same day. The system compares semantic topics only; it never compares or copies platform-wide view totals."}


@api_router.post("/youtube/benchmarks")
def create_external_benchmark(payload: ExternalBenchmarkInput, db: Session = Depends(get_db)) -> dict:
    observed_on = payload.observed_on or datetime.now(UTC)
    row = ExternalTrendBenchmark(source=payload.source.strip(), label=payload.label.strip(), observed_on=observed_on, region=payload.region.strip().upper() if payload.region else None, category=payload.category.strip() if payload.category else None, source_rank=payload.source_rank, source_url=payload.source_url.strip() if payload.source_url else None, note=payload.note.strip() if payload.note else None)
    db.add(row); db.commit(); db.refresh(row)
    return {"id": row.id, "message": "Benchmark saved. Matching runs automatically and never compares cross-platform view totals."}


@api_router.get("/youtube/trends/export.csv")
def export_youtube_trends_csv(db: Session = Depends(get_db)) -> Response:
    """Export the current public ranking and its auditable evidence rows."""
    clusters = db.scalars(
        select(TrendCluster)
        .where(TrendCluster.status.in_(PUBLIC_TREND_STATUSES))
        .order_by(desc(TrendCluster.trend_score), desc(TrendCluster.last_observed_at))
    ).all()
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=[
        "record_type", "cluster_id", "topic_label", "status", "trend_score",
        "observed_views", "observed_velocity_per_hour", "member_count",
        "channel_count", "observed_at", "video_id", "channel_id",
        "channel_title", "video_title", "video_views", "video_velocity_per_hour",
        "similarity_score", "joined_at", "membership_state",
    ])
    writer.writeheader()
    for cluster in clusters:
        writer.writerow({
            "record_type": "cluster", "cluster_id": str(cluster.id),
            "topic_label": cluster.label, "status": cluster.status,
            "trend_score": cluster.trend_score, "observed_views": cluster.observed_views,
            "observed_velocity_per_hour": cluster.observed_velocity_per_hour,
            "member_count": cluster.member_count, "channel_count": cluster.channel_count,
            "observed_at": cluster.last_observed_at.isoformat() if cluster.last_observed_at else "",
        })
        members = db.execute(
            select(YoutubeSnipe, TrendMembership)
            .join(TrendMembership, TrendMembership.youtube_snipe_id == YoutubeSnipe.id)
            .where(TrendMembership.cluster_id == cluster.id)
            .order_by(TrendMembership.joined_at)
        ).all()
        for row, membership in members:
            writer.writerow({
                "record_type": "evidence_post", "cluster_id": str(cluster.id),
                "topic_label": cluster.label, "status": cluster.status,
                "video_id": row.video_id, "channel_id": row.channel_id,
                "channel_title": row.channel_title, "video_title": row.title,
                "video_views": row.current_view_count,
                "video_velocity_per_hour": row.velocity_per_hour,
                "similarity_score": membership.similarity_score,
                "joined_at": membership.joined_at.isoformat(),
                "membership_state": membership.membership_state,
            })
    return Response(
        content="\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=ycgc-observed-topic-trends.csv"},
    )


@api_router.get("/youtube/trends/review-queue")
def topic_trend_review_queue(
    limit: int = Query(default=10, ge=1, le=30),
    db: Session = Depends(get_db),
) -> dict:
    """Surface ambiguous post-signal clusters for human labelling only."""
    candidates = db.scalars(
        select(TrendCluster)
        .where(TrendCluster.status.in_(("PRIVATE_CANDIDATE", "EMERGING", "ACCELERATING")))
        .order_by(desc(TrendCluster.last_observed_at))
        .limit(100)
    ).all()
    reviewed_ids = set(db.scalars(select(TopicClusterFeedback.cluster_id)).all())
    threshold = settings.topic_lexical_similarity_threshold
    queue = []
    for cluster in candidates:
        # A lone signal is a watch candidate, not a clustering decision. Asking
        # reviewers to label it would add noise rather than useful supervision.
        if cluster.id in reviewed_ids or cluster.member_count < 2:
            continue
        cohesion = cluster.semantic_cohesion or 0.0
        # Near the merge threshold and close to public lifecycle are the most
        # informative examples; one-video candidates are intentionally lower.
        threshold_uncertainty = max(0.0, 1 - min(1.0, abs(cohesion - threshold) / 0.25))
        evidence_uncertainty = min(1.0, cluster.member_count / max(1, settings.topic_trends_min_emerging_videos))
        score = round(0.65 * threshold_uncertainty + 0.35 * evidence_uncertainty, 3)
        payload = _trend_payload(db, cluster, member_limit=12, snapshot_limit=12)
        payload["review_uncertainty"] = score
        payload["review_reason"] = "near similarity threshold with accumulating evidence"
        queue.append(payload)
    queue.sort(key=lambda item: item["review_uncertainty"], reverse=True)
    return {
        "items": queue[:limit],
        "decisions": REVIEW_DECISIONS,
        "methodology": "Human feedback calibrates post-signal clustering only. It never alters anonymous discovery inputs or automatically changes production thresholds.",
    }


@api_router.post("/youtube/trends/{cluster_id}/feedback")
def submit_topic_trend_feedback(cluster_id: str, payload: TopicFeedbackInput, db: Session = Depends(get_db)) -> dict:
    if payload.decision not in REVIEW_DECISIONS:
        raise HTTPException(status_code=422, detail=f"decision must be one of: {', '.join(REVIEW_DECISIONS)}")
    cluster = db.get(TrendCluster, cluster_id)
    if not cluster:
        raise HTTPException(status_code=404, detail="Topic trend not found")
    feature_model = (cluster.model_metadata or {}).get("feature_model")
    snapshot_count = db.scalar(select(__import__("sqlalchemy").func.count(TrendSnapshot.id)).where(TrendSnapshot.cluster_id == cluster.id)) or 0
    feedback = TopicClusterFeedback(
        cluster_id=cluster.id,
        reviewer=payload.reviewer.strip(),
        decision=payload.decision,
        note=payload.note.strip() if payload.note else None,
        feature_model=feature_model,
        snapshot_count_at_review=snapshot_count,
    )
    db.add(feedback)
    db.commit()
    return {"id": feedback.id, "decision": feedback.decision, "message": "Feedback stored for audit and future calibration; no discovery or threshold was changed automatically."}


@api_router.get("/youtube/trends/{cluster_id}")
def get_youtube_trend(cluster_id: str, db: Session = Depends(get_db)) -> dict:
    cluster = db.get(TrendCluster, cluster_id)
    if not cluster:
        raise HTTPException(status_code=404, detail="Topic trend not found")
    # Detail remains available by ID for auditability, while the list only
    # publishes cross-channel clusters.
    return _trend_payload(db, cluster, member_limit=100, snapshot_limit=96)


@api_router.get("/youtube/breakouts")
def list_youtube_breakouts(
    niche: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    # The 24-hour rule belongs to discovery only. A signal that was detected
    # while fresh is retained as research evidence even after it ages out.
    statement = select(YoutubeSnipe).order_by(desc(YoutubeSnipe.detected_at)).limit(limit)
    if niche:
        statement = statement.where(YoutubeSnipe.niche == niche)
    rows = [
        row for row in db.scalars(statement).all()
        if row.signal_tier in {"EARLY", "RISING", "BREAKOUT"}
    ]
    return {
        "items": [
            {
                "video_id": row.video_id,
                "title": row.title,
                "channel_title": row.channel_title,
                "video_url": row.video_url,
                "thumbnail_url": row.thumbnail_url,
                "peak_frame_path": row.peak_frame_path,
                "published_at": row.published_at.isoformat(),
                "detected_at": row.detected_at.isoformat(),
                "current_view_count": row.current_view_count,
                "velocity_per_hour": row.velocity_per_hour,
                "signal_tier": row.signal_tier,
                "snapshot_count": int((row.raw_metadata or {}).get("snapshot_count", 0)),
                "age_bucket": (row.raw_metadata or {}).get("age_bucket"),
                "acceleration": (row.raw_metadata or {}).get("acceleration"),
                "channel_context": (row.raw_metadata or {}).get("channel_context"),
                "media_status": row.media_status,
                "enrichment_status": row.enrichment_status,
                "processing_reason": row.processing_reason,
                "enrichment": (row.raw_metadata or {}).get("enrichment"),
                "ai_mode": (row.ai_analysis or {}).get("mode"),
                "niche": row.niche,
                "visual_facts": (row.visual_facts or {}).get("facts", []),
            }
            for row in rows
        ]
    }
