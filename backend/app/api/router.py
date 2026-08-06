import csv
from datetime import UTC, datetime, timedelta
from io import StringIO

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.config import settings
from app.models.youtube_snipe import YoutubeSnipe
from app.models.trend_cluster import TrendCluster, TrendMembership, TrendSnapshot
from app.services.seed_store import SeedStore

api_router = APIRouter(prefix="/api/v1")


PUBLIC_TREND_STATUSES = ("EMERGING", "ACCELERATING", "CONFIRMED")


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
