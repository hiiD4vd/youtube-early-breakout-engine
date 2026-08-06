from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.config import settings
from app.models.youtube_snipe import YoutubeSnipe
from app.services.seed_store import SeedStore

api_router = APIRouter(prefix="/api/v1")


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
