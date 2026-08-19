"""Post-signal feature collection and provisional topic-candidate clustering."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from statistics import median
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
import re

from celery import Task
from sqlalchemy import func, select

from app.config import settings
from app.database import SessionLocal
from app.models.trend_cluster import TrendCluster, TrendMembership, TrendSignalFeature, TrendSnapshot
from app.models.market_trends import MarketRankedTopic
from app.models.youtube_snipe import YoutubeSnipe
from app.services.seed_store import SeedStore
from app.services.market_semantic_client import MarketSemanticClient
from app.services.gemini_client import TopicClusterGroupFacts
from app.services.trend_features import build_feature_payload, cosine_similarity, provisional_label
from app.services.trend_scoring import SCORING_VERSION, lifecycle_status, trend_score
from app.tasks.celery_app import celery_app

FEATURE_LOCK = "ycgc:youtube:lock:trend-features"
CLUSTER_LOCK = "ycgc:youtube:lock:trend-clustering"
ACTIVE_TIERS = ("EARLY", "RISING", "BREAKOUT")
CLUSTERING_VERSION = "semantic-format-v2"
GROUPING_BATCH_SIZE = 48
logger = logging.getLogger(__name__)
HOOK_ONLY_TERMS = {
    "apa", "jika", "saling", "yang", "inilah", "terjadi", "ketika",
    "top", "best", "moments", "moment", "who", "her", "his", "they",
    "can", "relate", "into", "turn",
}


def _human_topic_label(label: str | None) -> bool:
    """Reject keyword soup and hook fragments before they reach the UI."""
    value = (label or "").strip()
    if len(value) < 8 or " · " in value:
        return False
    tokens = re.findall(r"[^\W\d_]+", value.casefold(), re.UNICODE)
    meaningful = [token for token in tokens if token not in HOOK_ONLY_TERMS]
    return len(tokens) >= 2 and len(meaningful) >= 1


def _format_hint(signal: YoutubeSnipe) -> str | None:
    text = " ".join(filter(None, [signal.title, signal.transcript and signal.transcript[:500]])).casefold()
    ranking_terms = ("ranking", "ranked", "rank ", "top 5", "top 10", "tier list", "best moments", "worst moments", "rating ", "rate ")
    what_if_terms = ("what happens if", "what would happen if", "what if", "apa yang terjadi jika", "inilah yang terjadi jika", "apa jadinya jika")
    if any(term in text for term in ranking_terms):
        return "format_ranking"
    if any(term in text for term in what_if_terms):
        return "format_what_if"
    return None


def _early_lifecycle_phase(cluster: TrendCluster, now: datetime) -> tuple[str, float]:
    age_hours = max(0.0, (now - cluster.first_detected_at).total_seconds() / 3600)
    if age_hours < settings.early_topic_fresh_phase_hours:
        return "FRESH", age_hours
    if age_hours < settings.early_topic_rising_phase_hours:
        return "RISING", age_hours
    if age_hours < settings.early_topic_lifecycle_hours:
        return "VALIDATING", age_hours
    return "EXPIRED", age_hours


@celery_app.task(bind=True, name="app.tasks.youtube_trend_tasks.build_trend_signal_features", soft_time_limit=300, time_limit=360)
def build_trend_signal_features(self: Task) -> dict[str, int | str]:
    store = SeedStore()
    if not store.client.set(FEATURE_LOCK, "1", nx=True, ex=280):
        return {"status": "skipped_locked"}
    created = updated = skipped = 0
    try:
        with SessionLocal() as db:
            signals = db.scalars(select(YoutubeSnipe).where(YoutubeSnipe.signal_tier.in_(ACTIVE_TIERS))).all()
            existing = {item.youtube_snipe_id: item for item in db.scalars(select(TrendSignalFeature)).all()}
            for signal in signals:
                payload = build_feature_payload(signal)
                feature = existing.get(signal.id)
                if feature and feature.content_hash == payload["content_hash"] and feature.feature_model == payload["feature_model"]:
                    skipped += 1
                    continue
                if feature:
                    for key, value in payload.items():
                        setattr(feature, key, value)
                    updated += 1
                else:
                    db.add(TrendSignalFeature(youtube_snipe_id=signal.id, **payload))
                    created += 1
            db.commit()
        store.set_status(last_trend_feature_run_at=datetime.now(UTC).isoformat(), last_trend_features_created=created, last_trend_features_updated=updated)
        return {"created": created, "updated": updated, "skipped": skipped}
    finally:
        store.client.delete(FEATURE_LOCK)


def _cluster_vector(db, cluster_id) -> dict[str, float]:
    features = db.execute(
        select(TrendSignalFeature.sparse_vector)
        .join(TrendMembership, TrendMembership.youtube_snipe_id == TrendSignalFeature.youtube_snipe_id)
        .where(TrendMembership.cluster_id == cluster_id)
    ).scalars().all()
    aggregate: dict[str, float] = {}
    for vector in features:
        for token, weight in (vector or {}).items():
            aggregate[token] = aggregate.get(token, 0) + float(weight)
    norm = sum(value * value for value in aggregate.values()) ** 0.5
    return {token: value / norm for token, value in aggregate.items()} if norm else {}


def _slugify_topic_label(label: str, fallback: str) -> str:
    tokens = re.findall(r"[a-z0-9]+", (label or "").casefold())
    slug = "-".join(tokens[:8]).strip("-")
    return slug[:80] or fallback


def _build_semantic_grouping_evidence(signals: list[YoutubeSnipe]) -> str:
    lines: list[str] = [
        "Group these independently collected YouTube Shorts by the same underlying topic.",
        "Only create groups with at least 2 videos. Use the exact zero-based indices in video_indices.",
        "Do not force videos together if they only share a broad category.",
        "",
        "EVIDENCE LIST:",
    ]
    for idx, signal in enumerate(signals):
        title = (signal.title or signal.video_id or "Untitled").strip()
        channel = (signal.channel_title or "unknown").strip()
        niche = (signal.niche or "").strip() or "unknown"
        region = ((signal.raw_metadata or {}).get("region") or "").strip()
        parts = [f"[{idx}]", f"Title: {title}", f"Channel: {channel}", f"Niche: {niche}"]
        if region:
            parts.append(f"Region: {region}")
        format_hint = _format_hint(signal)
        if format_hint:
            parts.append(f"Format hint: {format_hint}")
        parts.append(f"Views: {signal.current_view_count}")
        parts.append(f"Velocity/hr: {round(signal.velocity_per_hour, 2)}")
        if signal.transcript:
            parts.append(f"Transcript hint: {(signal.transcript[:140]).replace(chr(10), ' ')}")
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def _label_similarity(left: str | None, right: str | None) -> float:
    left_tokens = _token_set(left)
    right_tokens = _token_set(right)
    if not left_tokens or not right_tokens:
        return 0.0
    overlap = len(left_tokens & right_tokens)
    return overlap / max(1, min(len(left_tokens), len(right_tokens)))


def _best_existing_cluster_for_label(db, clusters: list[TrendCluster], label: str | None, niche: str | None) -> tuple[TrendCluster | None, float]:
    best: TrendCluster | None = None
    best_score = 0.0
    for cluster in clusters:
        if cluster.status == "MERGED":
            continue
        score = max(
            _label_similarity(label, cluster.label),
            _label_similarity(label, cluster.cluster_reason),
        )
        if niche and cluster.niche and niche.casefold() == cluster.niche.casefold():
            score = min(1.0, score + 0.08)
        if score > best_score:
            best, best_score = cluster, score
    return best, best_score


def _attach_group_to_cluster(db, *, cluster: TrendCluster, members: list[YoutubeSnipe], similarity: float, cluster_label: str | None, cluster_type: str | None, cluster_summary: str | None, cluster_entities: list[str] | None, confidence: float) -> int:
    existing_member_ids = set(
        db.scalars(
            select(TrendMembership.youtube_snipe_id).where(TrendMembership.cluster_id == cluster.id)
        ).all()
    )
    existing_channels = set(
        db.scalars(
            select(YoutubeSnipe.channel_id)
            .join(TrendMembership, TrendMembership.youtube_snipe_id == YoutubeSnipe.id)
            .where(TrendMembership.cluster_id == cluster.id)
        ).all()
    )
    added = 0
    for signal in members:
        if signal.id in existing_member_ids:
            continue
        same_channel = signal.channel_id in existing_channels
        db.add(TrendMembership(
            cluster_id=cluster.id,
            youtube_snipe_id=signal.id,
            similarity_score=similarity,
            membership_state="LLM_GROUPED" if cluster.status == "PRIVATE_CANDIDATE" else "PROVISIONAL",
            is_same_channel_duplicate=same_channel,
            weight=0.25 if same_channel else 1.0,
            feature_evidence={
                "source": "semantic_grouping",
                "similarity": similarity,
                "entry_view_count": signal.current_view_count,
                "entry_detected_at": signal.detected_at.isoformat(),
                "entry_age_hours": round(max(0.0, (signal.detected_at - signal.published_at).total_seconds() / 3600), 3) if signal.published_at else 0.0,
                "cluster_label": cluster_label,
                "cluster_type": cluster_type,
                "cluster_summary": cluster_summary,
            },
        ))
        existing_channels.add(signal.channel_id)
        cluster.member_count += 1
        cluster.channel_count = len(existing_channels)
        cluster.observed_views += signal.current_view_count
        cluster.observed_velocity_per_hour += signal.velocity_per_hour
        cluster.last_member_at = datetime.now(UTC)
        cluster.last_observed_at = datetime.now(UTC)
        added += 1
    if cluster_label and (not cluster.label or confidence >= (cluster.label_confidence or 0)):
        cluster.label = cluster_label[:255]
        cluster.label_confidence = confidence
    if cluster_type and not cluster.niche:
        cluster.niche = cluster_type[:128]
    if cluster_summary and not cluster.cluster_reason:
        cluster.cluster_reason = cluster_summary[:1000]
    metadata = dict(cluster.model_metadata or {})
    metadata.update({
        "clustering_version": CLUSTERING_VERSION,
        "semantic_grouping": True,
        "semantic_entities": cluster_entities or [],
    })
    cluster.model_metadata = metadata
    return added


def _merge_known_format_topics(db, signals: list[YoutubeSnipe]) -> tuple[int, int, int]:
    """Move only strongly identified format evidence into canonical topics.

    This intentionally avoids a destructive full-pool rebuild. Unrelated
    legacy clusters remain visible until a real replacement exists.
    """
    definitions = {
        "format_ranking": (
            "Video ranking dan hitung mundur",
            "Video yang membandingkan atau mengurutkan momen, objek, dan kejadian dalam format ranking atau hitung mundur.",
        ),
        "format_what_if": (
            "Eksperimen 'apa yang terjadi jika'",
            "Video eksperimen dan penjelasan yang menunjukkan hasil dari pertanyaan 'apa yang terjadi jika'.",
        ),
    }
    created = moved = superseded = 0
    for topic_type, (label, summary) in definitions.items():
        members = [signal for signal in signals if _format_hint(signal) == topic_type]
        if len(members) < 2 or len({signal.channel_id for signal in members}) < 2:
            continue
        target = db.scalar(
            select(TrendCluster).where(
                ((TrendCluster.label == label) | (TrendCluster.niche == topic_type)),
                TrendCluster.status != "MERGED",
            ).order_by(TrendCluster.first_detected_at)
        )
        if target is None:
            target = TrendCluster(
                public_slug=_slugify_topic_label(label, f"format-{topic_type}"),
                label=label,
                label_confidence=0.98,
                niche=topic_type,
                status="PRIVATE_CANDIDATE",
                cluster_reason=summary,
                model_metadata={"clustering_version": CLUSTERING_VERSION, "source": "deterministic_format", "followable": True, "early_topic_named": True},
            )
            db.add(target)
            db.flush()
            created += 1
        else:
            target.label = label
            target.label_confidence = 0.98
            target.niche = topic_type
            target.cluster_reason = summary
            target.status = "PRIVATE_CANDIDATE"
            target.model_metadata = {
                **(target.model_metadata or {}),
                "clustering_version": CLUSTERING_VERSION,
                "source": "deterministic_format",
                "followable": True,
                "early_topic_named": True,
            }

        moved += _attach_group_to_cluster(
            db,
            cluster=target,
            members=members,
            similarity=0.98,
            cluster_label=label,
            cluster_type=topic_type,
            cluster_summary=summary,
            cluster_entities=[],
            confidence=0.98,
        )

        member_ids = {signal.id for signal in members}
        affected_clusters: set = set()
        old_memberships = db.scalars(
            select(TrendMembership).where(
                TrendMembership.youtube_snipe_id.in_(member_ids),
                TrendMembership.cluster_id != target.id,
            )
        ).all()
        for membership in old_memberships:
            affected_clusters.add(membership.cluster_id)
            db.delete(membership)
        db.flush()

        for cluster_id in affected_clusters:
            cluster = db.get(TrendCluster, cluster_id)
            if cluster is None or cluster.status == "MERGED":
                continue
            remaining = db.scalars(
                select(YoutubeSnipe)
                .join(TrendMembership, TrendMembership.youtube_snipe_id == YoutubeSnipe.id)
                .where(TrendMembership.cluster_id == cluster_id)
            ).all()
            cluster.member_count = len(remaining)
            cluster.channel_count = len({signal.channel_id for signal in remaining})
            cluster.observed_views = sum(signal.current_view_count for signal in remaining)
            cluster.observed_velocity_per_hour = sum(signal.velocity_per_hour for signal in remaining)
            if len(remaining) < 2:
                cluster.status = "MERGED"
                cluster.model_metadata = {
                    **(cluster.model_metadata or {}),
                    "superseded_by_policy": CLUSTERING_VERSION,
                    "superseded_by_format": label,
                    "superseded_at": datetime.now(UTC).isoformat(),
                }
                superseded += 1

        # Recalculate the canonical cluster rather than trusting historical
        # counters that may predate this migration.
        canonical = db.scalars(
            select(YoutubeSnipe)
            .join(TrendMembership, TrendMembership.youtube_snipe_id == YoutubeSnipe.id)
            .where(TrendMembership.cluster_id == target.id)
        ).all()
        target.member_count = len(canonical)
        target.channel_count = len({signal.channel_id for signal in canonical})
        target.observed_views = sum(signal.current_view_count for signal in canonical)
        target.observed_velocity_per_hour = sum(signal.velocity_per_hour for signal in canonical)
        target.last_member_at = datetime.now(UTC)
        target.last_observed_at = datetime.now(UTC)
    return created, moved, superseded


def _semantic_group_recent_signals(db, signals: list[YoutubeSnipe], existing_clusters: list[TrendCluster], vectors: dict) -> tuple[int, int, set[int]]:
    if not signals:
        return 0, 0, set()
    created = assigned = 0
    assigned_indexes: set[int] = set()
    groups_with_indexes: list[tuple[TopicClusterGroupFacts, list[int]]] = []
    client = MarketSemanticClient()

    # Format is sometimes the honest shared topic even when the objects shown
    # differ. Detect the two strongest mechanics across the *whole* pool first
    # so a batch boundary cannot split one ranking/what-if conversation into
    # several keyword-soup clusters.
    format_groups = {
        "format_ranking": {
            "title": "Video ranking dan hitung mundur",
            "summary": "Video yang membandingkan atau mengurutkan beberapa momen, objek, atau kejadian dalam format ranking dan hitung mundur.",
        },
        "format_what_if": {
            "title": "Eksperimen 'apa yang terjadi jika'",
            "summary": "Video eksperimen dan penjelasan yang memperlihatkan hasil dari pertanyaan 'apa yang terjadi jika'.",
        },
    }
    deterministic_indexes: set[int] = set()
    for topic_type, copy in format_groups.items():
        indexes = [idx for idx, signal in enumerate(signals) if _format_hint(signal) == topic_type]
        distinct_channels = {signals[idx].channel_id for idx in indexes}
        if len(indexes) < 2 or len(distinct_channels) < 2:
            continue
        groups_with_indexes.append((TopicClusterGroupFacts(
            topic_title=copy["title"],
            topic_type=topic_type,
            summary=copy["summary"],
            entities=[],
            confidence=0.98,
            followable=True,
            video_indices=indexes,
        ), indexes))
        deterministic_indexes.update(indexes)

    residual_indexes = [idx for idx in range(len(signals)) if idx not in deterministic_indexes]
    batches = []
    for start in range(0, len(residual_indexes), GROUPING_BATCH_SIZE):
        index_map = residual_indexes[start:start + GROUPING_BATCH_SIZE]
        batches.append((index_map, [signals[idx] for idx in index_map]))
    # Provider calls are network-bound. Running a small bounded pool avoids a
    # 300-video backfill taking seven serial two-minute request windows while
    # still protecting the gateway from an unbounded burst.
    with ThreadPoolExecutor(max_workers=min(2, max(1, len(batches)))) as pool:
        futures = {
            pool.submit(client.group_topic_candidates, _build_semantic_grouping_evidence(batch)): index_map
            for index_map, batch in batches
        }
        for future in as_completed(futures):
            index_map = futures[future]
            try:
                groups = future.result()
            except Exception as exc:
                logger.warning("Semantic grouping batch failed (%s videos): %s", len(index_map), exc)
                continue
            for group in groups:
                global_indexes = [index_map[idx] for idx in group.video_indices if 0 <= idx < len(index_map)]
                groups_with_indexes.append((group, global_indexes))
    for group, member_indexes in groups_with_indexes:
        if len(group.video_indices) < 2:
            continue
        # Accept if followable OR confidence is high enough
        if not group.followable and group.confidence < 0.5:
            continue
        if group.confidence < 0.3:
            continue
        if len(member_indexes) < 2:
            continue
        members = [signals[idx] for idx in member_indexes if idx not in assigned_indexes]
        if len(members) < 2:
            continue
        if not _human_topic_label(group.topic_title):
            continue
        best_cluster, best_score = _best_existing_cluster_for_label(db, existing_clusters, group.topic_title, group.topic_type)
        if best_cluster is None or best_score < settings.topic_lexical_similarity_threshold:
            best_cluster = TrendCluster(
                public_slug=_slugify_topic_label(group.topic_title, f"candidate-{members[0].video_id.lower()}"),
                label=group.topic_title,
                label_confidence=group.confidence,
                niche=group.topic_type,
                status="PRIVATE_CANDIDATE",
                cluster_reason=group.summary,
                model_metadata={"clustering_version": CLUSTERING_VERSION, "source": "market_semantic_provider", "entities": group.entities, "followable": group.followable},
            )
            db.add(best_cluster)
            db.flush()
            existing_clusters.append(best_cluster)
            created += 1
        added = _attach_group_to_cluster(
            db,
            cluster=best_cluster,
            members=members,
            similarity=max(best_score, group.confidence),
            cluster_label=group.topic_title,
            cluster_type=group.topic_type,
            cluster_summary=group.summary,
            cluster_entities=group.entities,
            confidence=group.confidence,
        )
        vectors[best_cluster.id] = _cluster_vector(db, best_cluster.id)
        assigned += added
        assigned_indexes.update(member_indexes)
    return created, assigned, assigned_indexes


@celery_app.task(bind=True, name="app.tasks.youtube_trend_tasks.cluster_recent_signals", soft_time_limit=720, time_limit=780)
def cluster_recent_signals(self: Task) -> dict[str, int | str]:
    """Provider-backed semantic clustering with lexical fallback.

    Strategy:
    1. Safely merge strong cross-cluster content-format evidence.
    2. Dissolve 1-member PRIVATE_CANDIDATE clusters — those are not real topics.
    3. Send only genuinely unassigned signals to the semantic provider.
    4. Remaining ungrouped signals only get a cluster if they match an existing
       multi-member cluster. Lone signals stay unassigned until next run.
    """
    store = SeedStore()
    if not store.client.set(CLUSTER_LOCK, "1", nx=True, ex=840):
        return {"status": "skipped_locked"}
    created = assigned = dissolved = 0
    try:
        with SessionLocal() as db:
            signals = db.scalars(
                select(YoutubeSnipe)
                .where(YoutubeSnipe.signal_tier.in_(ACTIVE_TIERS))
                .order_by(YoutubeSnipe.detected_at)
            ).all()
            signals_by_id = {s.id: s for s in signals}
            features = {item.youtube_snipe_id: item for item in db.scalars(select(TrendSignalFeature)).all()}

            format_created, format_moved, format_superseded = _merge_known_format_topics(db, signals)
            created += format_created
            assigned += format_moved
            dissolved += format_superseded
            # The deterministic migration is independently valid. Persist it
            # before slower provider calls so a gateway timeout cannot hide or
            # roll back these proven format merges.
            db.commit()
            dissolved_signal_ids: set[int] = set()

            # Find 1-member PRIVATE_CANDIDATE clusters — dissolve them.
            # These are fake "topics" created by the old lexical fallback.
            lone_clusters = db.scalars(
                select(TrendCluster).where(
                    TrendCluster.status == "PRIVATE_CANDIDATE",
                    TrendCluster.member_count <= 1,
                )
            ).all()

            for cluster in lone_clusters:
                memberships = db.scalars(
                    select(TrendMembership).where(TrendMembership.cluster_id == cluster.id)
                ).all()
                for m in memberships:
                    dissolved_signal_ids.add(m.youtube_snipe_id)
                    db.delete(m)
                db.delete(cluster)
                dissolved += 1
            db.flush()  # ensure deletes are visible to subsequent queries

            # Truly unassigned signals (never had any cluster)
            existing_member_ids = set(db.scalars(select(TrendMembership.youtube_snipe_id)).all())
            truly_unassigned = {s.id for s in signals if s.id not in existing_member_ids}

            # Pool for semantic grouping: dissolved + truly unassigned
            pool_ids = dissolved_signal_ids | truly_unassigned
            pool_signals = [signals_by_id[sid] for sid in pool_ids if sid in signals_by_id]

            # Existing healthy clusters (multi-member, non-MERGED)
            clusters = db.scalars(
                select(TrendCluster).where(
                    TrendCluster.status.in_(("PRIVATE_CANDIDATE", "EMERGING", "ACCELERATING", "CONFIRMED"))
                )
            ).all()
            vectors = {cluster.id: _cluster_vector(db, cluster.id) for cluster in clusters}

            # Phase 1: LLM semantic grouping on the full pool
            semantic_created, semantic_assigned, semantic_indexes = _semantic_group_recent_signals(
                db, pool_signals, clusters, vectors
            )
            created += semantic_created
            assigned += semantic_assigned

            # Track which pool signals got assigned
            assigned_pool_ids: set[int] = set()
            if semantic_indexes:
                for idx in semantic_indexes:
                    if 0 <= idx < len(pool_signals):
                        assigned_pool_ids.add(pool_signals[idx].id)

            # Phase 2: Lexical fallback — only assign to existing multi-member clusters.
            # Lone signals that don't match any existing cluster stay unassigned.
            remaining_ids = pool_ids - assigned_pool_ids
            remaining_signals = [signals_by_id[sid] for sid in remaining_ids if sid in signals_by_id]

            for signal in remaining_signals:
                feature = features.get(signal.id)
                if not feature or not feature.sparse_vector:
                    continue
                best = None
                best_score = 0.0
                for cluster in clusters:
                    # Only match against clusters with >= 2 members
                    if cluster.member_count < 2:
                        continue
                    score = cosine_similarity(feature.sparse_vector, vectors.get(cluster.id))
                    if cluster.niche and signal.niche and cluster.niche.casefold() == signal.niche.casefold():
                        score = min(1.0, score + 0.08)
                    if score > best_score:
                        best, best_score = cluster, score
                if best is None or best_score < settings.topic_lexical_similarity_threshold:
                    # Don't create a 1-video cluster. Skip — wait for more signals.
                    continue
                existing_channels = set(
                    db.scalars(
                        select(YoutubeSnipe.channel_id)
                        .join(TrendMembership, TrendMembership.youtube_snipe_id == YoutubeSnipe.id)
                        .where(TrendMembership.cluster_id == best.id)
                    ).all()
                )
                same_channel = signal.channel_id in existing_channels
                db.add(TrendMembership(
                    cluster_id=best.id,
                    youtube_snipe_id=signal.id,
                    similarity_score=best_score,
                    membership_state="PROVISIONAL",
                    is_same_channel_duplicate=same_channel,
                    weight=0.25 if same_channel else 1.0,
                    feature_evidence={
                        "feature_model": feature.feature_model,
                        "similarity": best_score,
                        "source_provenance": feature.source_provenance,
                        "entry_view_count": signal.current_view_count,
                        "entry_detected_at": signal.detected_at.isoformat(),
                        "entry_age_hours": round(
                            max(0.0, (signal.detected_at - signal.published_at).total_seconds() / 3600), 3
                        ) if signal.published_at else 0.0,
                    },
                ))
                best.member_count += 1
                best.channel_count = len(existing_channels | {signal.channel_id})
                best.observed_views += signal.current_view_count
                best.observed_velocity_per_hour += signal.velocity_per_hour
                best.last_member_at = datetime.now(UTC)
                best.last_observed_at = datetime.now(UTC)
                assigned += 1

            db.commit()
        store.set_status(
            last_trend_cluster_run_at=datetime.now(UTC).isoformat(),
            last_trend_candidates_created=created,
            last_trend_memberships_assigned=assigned,
            last_trend_lone_clusters_dissolved=dissolved,
        )
        return {
            "candidates_created": created,
            "memberships_assigned": assigned,
            "lone_clusters_dissolved": dissolved,
            "pool_size": len(pool_signals),
        }
    finally:
        store.client.delete(CLUSTER_LOCK)


def _cluster_members(db, cluster_id):
    return db.execute(
        select(TrendMembership, YoutubeSnipe)
        .join(YoutubeSnipe, YoutubeSnipe.id == TrendMembership.youtube_snipe_id)
        .where(TrendMembership.cluster_id == cluster_id)
    ).all()


def _merge_private_clusters(db) -> int:
    """Merge only provisional candidates and retain a tombstone audit record."""
    clusters = db.scalars(select(TrendCluster).where(TrendCluster.status == "PRIVATE_CANDIDATE").order_by(TrendCluster.first_detected_at)).all()
    vectors = {cluster.id: _cluster_vector(db, cluster.id) for cluster in clusters}
    merged = 0
    for index, target in enumerate(clusters):
        if target.status != "PRIVATE_CANDIDATE":
            continue
        for source in clusters[index + 1:]:
            if source.status != "PRIVATE_CANDIDATE":
                continue
            if target.niche and source.niche and target.niche.casefold() != source.niche.casefold():
                continue
            similarity = cosine_similarity(vectors[target.id], vectors[source.id])
            if similarity < settings.topic_lexical_similarity_threshold:
                continue
            memberships = db.scalars(select(TrendMembership).where(TrendMembership.cluster_id == source.id)).all()
            for membership in memberships:
                membership.cluster_id = target.id
                membership.membership_state = "MERGED_PROVISIONAL"
                evidence = dict(membership.feature_evidence or {})
                evidence["cluster_merge_similarity"] = similarity
                membership.feature_evidence = evidence
            source.status = "MERGED"
            source.cooling_at = datetime.now(UTC)
            source.model_metadata = {**(source.model_metadata or {}), "merged_into": str(target.id), "merge_similarity": similarity, "merged_at": datetime.now(UTC).isoformat()}
            merged += 1
    return merged


def _token_set(value: str | None) -> set[str]:
    return {word for word in (value or "").casefold().replace("&", " ").replace("-", " ").split() if len(word) >= 3}


def _market_outcome(db, cluster: TrendCluster, now: datetime) -> dict[str, str]:
    """Record a conservative delayed label for active-learning evaluation.

    This never changes discovery thresholds automatically. It only records
    whether an early organic conversation later resembles a public Market
    Topic observed through the separate, whale-inclusive chart lane.
    """
    prior = (cluster.model_metadata or {}).get("outcome") or {}
    if prior.get("state") in {"VALIDATED_BY_MARKET", "COOLED_WITHOUT_MARKET"}:
        return prior
    try:
        if prior.get("checked_at") and now - datetime.fromisoformat(prior["checked_at"]) < timedelta(hours=6):
            return prior
    except ValueError:
        pass
    cluster_terms = _token_set(cluster.label)
    if cluster_terms:
        public = db.scalars(select(MarketRankedTopic).where(MarketRankedTopic.status.in_(("THEME", "EMERGING", "ACCELERATING", "CONFIRMED")))).all()
        for market in public:
            market_terms = _token_set(market.label)
            overlap = len(cluster_terms & market_terms) / max(1, min(len(cluster_terms), len(market_terms)))
            if overlap >= .35:
                try:
                    same_topic, confidence = MarketSemanticClient().same_topic(cluster.label or "", market.label)
                except Exception:
                    same_topic, confidence = False, 0.0
                if same_topic:
                    return {"state": "VALIDATED_BY_MARKET", "matched_topic": market.label, "semantic_confidence": confidence, "checked_at": now.isoformat()}
    age_hours = (now - cluster.first_detected_at).total_seconds() / 3600
    if cluster.status == "COOLING" and age_hours >= 24:
        return {"state": "COOLED_WITHOUT_MARKET", "checked_at": now.isoformat()}
    return {"state": "PENDING", "checked_at": now.isoformat()}


def _is_early_evidence(membership: TrendMembership, signal: YoutubeSnipe) -> bool:
    """Use only the state at first observation, never creator popularity."""
    evidence = membership.feature_evidence or {}
    if "entry_view_count" not in evidence:
        # Old evidence predates the baseline field. Preserve it as explicitly
        # inferred rather than deleting research history during rollout.
        evidence = {**evidence, "entry_view_count": min(signal.current_view_count, settings.early_topic_max_entry_views), "entry_age_hours": 0.0, "entry_baseline": "inferred_legacy"}
        membership.feature_evidence = evidence
    entry_views = int(evidence.get("entry_view_count", signal.current_view_count) or 0)
    entry_age = float(evidence.get("entry_age_hours", 0) or 0)
    return entry_views <= settings.early_topic_max_entry_views and entry_age <= settings.early_topic_max_entry_age_hours


def _name_early_topic(cluster: TrendCluster, signals: list[YoutubeSnipe]) -> None:
    """Give a small, organic cluster a human topic label once, not per poll."""
    metadata = cluster.model_metadata or {}
    if metadata.get("early_topic_named") or (cluster.niche or "").startswith("format_") or len(signals) < 2:
        return
    evidence = "\n".join(f"- Title: {signal.title or signal.video_id}\n  Channel: {signal.channel_title or 'unknown'}" for signal in signals[:10])
    try:
        facts = MarketSemanticClient().review_topic_cluster(evidence)
    except Exception:
        return
    if not facts.followable or facts.confidence < .70:
        return
    cluster.label = facts.topic_title.strip()
    cluster.label_confidence = facts.confidence
    cluster.niche = facts.topic_type
    cluster.cluster_reason = facts.summary.strip()
    cluster.model_metadata = {**metadata, "early_topic_named": True, "early_topic_entities": facts.entities, "early_topic_model": settings.market_topic_review_model}


def _keep_canonical_format_label(cluster: TrendCluster, signals: list[YoutubeSnipe]) -> bool:
    """Keep proven format topics stable across later scoring/name passes."""
    hints = [_format_hint(signal) for signal in signals]
    for topic_type, label, summary in (
        ("format_ranking", "Video ranking dan hitung mundur", "Video yang membandingkan atau mengurutkan momen, objek, dan kejadian dalam format ranking atau hitung mundur."),
        ("format_what_if", "Eksperimen 'apa yang terjadi jika'", "Video eksperimen dan penjelasan yang menunjukkan hasil dari pertanyaan 'apa yang terjadi jika'."),
    ):
        matches = sum(1 for hint in hints if hint == topic_type)
        if matches < 2 or matches / max(1, len(signals)) < 0.6:
            continue
        cluster.label = label
        cluster.label_confidence = 0.98
        cluster.niche = topic_type
        cluster.cluster_reason = summary
        cluster.model_metadata = {
            **(cluster.model_metadata or {}),
            "clustering_version": CLUSTERING_VERSION,
            "source": "deterministic_format",
            "followable": True,
            "early_topic_named": True,
        }
        return True
    return False


@celery_app.task(bind=True, name="app.tasks.youtube_trend_tasks.score_topic_trends", soft_time_limit=300, time_limit=360)
def score_topic_trends(self: Task) -> dict[str, int | str]:
    """Aggregate observed evidence, snapshot it, and transition lifecycle states."""
    store = SeedStore()
    # Scoring and reclustering mutate/read the same membership graph. They
    # must share one lock so scoring never waits on half-rebuilt DB rows.
    if not store.client.set(CLUSTER_LOCK, "score", nx=True, ex=420):
        return {"status": "skipped_locked"}
    snapshots = merged = 0
    try:
        with SessionLocal() as db:
            merged = _merge_private_clusters(db)
            clusters = db.scalars(select(TrendCluster).where(TrendCluster.status != "MERGED")).all()
            now = datetime.now(UTC)
            names_used = 0
            for cluster in clusters:
                members = _cluster_members(db, cluster.id)
                if not members:
                    continue
                signals = [signal for _, signal in members]
                memberships = [membership for membership, _ in members]
                # Channel size is not a gate. Early evidence means the Short
                # was first captured while fresh and below the view ceiling.
                early_members = [(membership, signal) for membership, signal in members if _is_early_evidence(membership, signal)]
                early_signals = [signal for _, signal in early_members]
                early_memberships = [membership for membership, _ in early_members]
                previous = db.scalar(select(TrendSnapshot).where(TrendSnapshot.cluster_id == cluster.id).order_by(TrendSnapshot.observed_at.desc()))
                observed_views = sum(signal.current_view_count for signal in early_signals)
                observed_velocity = sum(signal.velocity_per_hour * membership.weight for membership, signal in early_members)
                velocities = [signal.velocity_per_hour for signal in early_signals]
                median_velocity = median(velocities) if velocities else 0.0
                member_count = len(early_signals)
                channels = {signal.channel_id for signal in early_signals}
                channel_count = len(channels)
                canonical_format = _keep_canonical_format_label(cluster, signals)
                if not canonical_format and names_used < 4 and member_count >= 2 and not (cluster.model_metadata or {}).get("early_topic_named"):
                    _name_early_topic(cluster, early_signals)
                    names_used += 1
                acceleration = None if not previous or previous.observed_velocity_per_hour <= 0 else round((observed_velocity - previous.observed_velocity_per_hour) / previous.observed_velocity_per_hour, 4)
                new_members = member_count if not previous else sum(1 for membership, _ in early_members if membership.joined_at > previous.observed_at)
                new_channels = channel_count if not previous else len({signal.channel_id for membership, signal in early_members if membership.joined_at > previous.observed_at})
                duplicate_weight = sum(1 - membership.weight for membership in early_memberships) / max(1, member_count)
                score = trend_score(member_count=member_count, channel_count=channel_count, observed_velocity=observed_velocity, acceleration=acceleration, new_member_count=new_members, duplicate_weight=duplicate_weight)
                stale_hours = (now - (cluster.last_member_at or cluster.first_detected_at)).total_seconds() / 3600
                status = lifecycle_status(member_count=member_count, channel_count=channel_count, score=score, acceleration=acceleration, stale_hours=stale_hours)
                early_phase, lifecycle_age_hours = _early_lifecycle_phase(cluster, now)
                # Early Signals remain visible only through their 72-hour
                # validation window. The evidence is retained for audit and
                # active-learning outcomes after it leaves the public board.
                if early_phase == "EXPIRED":
                    status = "COOLING"
                context_mix: dict[str, int] = {}
                for signal in signals:
                    value = ((signal.raw_metadata or {}).get("channel_context") or {}).get("status", "UNKNOWN")
                    context_mix[value] = context_mix.get(value, 0) + 1
                cluster.member_count = member_count
                cluster.channel_count = channel_count
                cluster.observed_views = observed_views
                cluster.observed_velocity_per_hour = observed_velocity
                cluster.acceleration = acceleration
                cluster.trend_score = score
                cluster.status = status
                cluster.last_observed_at = now
                cluster.cooling_at = now if status == "COOLING" else None
                cluster.channel_context_mix = context_mix
                cluster.semantic_cohesion = round(sum((membership.similarity_score or 0) for membership in memberships) / max(1, member_count), 4)
                cluster.evidence_summary = {"distinct_channels": channel_count, "new_members": new_members, "new_channels": new_channels, "duplicate_weight": round(duplicate_weight, 3), "early_member_count": member_count, "early_channel_count": channel_count, "all_member_count": len(signals), "early_phase": early_phase, "lifecycle_age_hours": round(lifecycle_age_hours, 1), "lifecycle_window_hours": settings.early_topic_lifecycle_hours, "observed_only": True}
                cluster.model_metadata = {**(cluster.model_metadata or {}), "early_topic_policy": "fresh_low_view_cross_channel", "early_topic_lifecycle": "0-24h:FRESH, 24-48h:RISING, 48-72h:VALIDATING", "outcome": _market_outcome(db, cluster, now)}
                db.add(TrendSnapshot(cluster_id=cluster.id, observed_at=now, observed_views=observed_views, observed_velocity_per_hour=observed_velocity, median_velocity_per_hour=median_velocity, acceleration=acceleration, member_count=member_count, channel_count=channel_count, new_member_count=new_members, new_channel_count=new_channels, trend_score=score, scoring_version=SCORING_VERSION, reason=f"lifecycle:{status}"))
                snapshots += 1
            db.commit()
        store.set_status(last_trend_score_run_at=datetime.now(UTC).isoformat(), last_trend_snapshots=snapshots, last_trend_merges=merged)
        return {"snapshots": snapshots, "merged": merged}
    finally:
        store.client.delete(CLUSTER_LOCK)
