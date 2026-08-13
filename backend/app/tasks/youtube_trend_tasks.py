"""Post-signal feature collection and provisional topic-candidate clustering."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from statistics import median

from celery import Task
from sqlalchemy import func, select

from app.config import settings
from app.database import SessionLocal
from app.models.trend_cluster import TrendCluster, TrendMembership, TrendSignalFeature, TrendSnapshot
from app.models.market_trends import MarketRankedTopic
from app.models.youtube_snipe import YoutubeSnipe
from app.services.seed_store import SeedStore
from app.services.market_semantic_client import MarketSemanticClient
from app.services.trend_features import build_feature_payload, cosine_similarity, provisional_label
from app.services.trend_scoring import SCORING_VERSION, lifecycle_status, trend_score
from app.tasks.celery_app import celery_app

FEATURE_LOCK = "ycgc:youtube:lock:trend-features"
CLUSTER_LOCK = "ycgc:youtube:lock:trend-clustering"
ACTIVE_TIERS = ("EARLY", "RISING", "BREAKOUT")


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


@celery_app.task(bind=True, name="app.tasks.youtube_trend_tasks.cluster_recent_signals", soft_time_limit=300, time_limit=360)
def cluster_recent_signals(self: Task) -> dict[str, int | str]:
    """Make private, reversible candidate clusters. Public ranking comes in T2."""
    store = SeedStore()
    if not store.client.set(CLUSTER_LOCK, "1", nx=True, ex=280):
        return {"status": "skipped_locked"}
    created = assigned = 0
    try:
        with SessionLocal() as db:
            signals = db.scalars(select(YoutubeSnipe).where(YoutubeSnipe.signal_tier.in_(ACTIVE_TIERS)).order_by(YoutubeSnipe.detected_at)).all()
            features = {item.youtube_snipe_id: item for item in db.scalars(select(TrendSignalFeature)).all()}
            member_signal_ids = set(db.scalars(select(TrendMembership.youtube_snipe_id)).all())
            clusters = db.scalars(select(TrendCluster).where(TrendCluster.status.in_(("PRIVATE_CANDIDATE", "EMERGING", "ACCELERATING", "CONFIRMED")))).all()
            vectors = {cluster.id: _cluster_vector(db, cluster.id) for cluster in clusters}
            for signal in signals:
                if signal.id in member_signal_ids:
                    continue
                feature = features.get(signal.id)
                if not feature or not feature.sparse_vector:
                    continue
                best = None
                best_score = 0.0
                for cluster in clusters:
                    score = cosine_similarity(feature.sparse_vector, vectors.get(cluster.id))
                    if cluster.niche and signal.niche and cluster.niche.casefold() == signal.niche.casefold():
                        score = min(1.0, score + 0.08)
                    if score > best_score:
                        best, best_score = cluster, score
                if best is None or best_score < settings.topic_lexical_similarity_threshold:
                    best = TrendCluster(
                        public_slug=f"candidate-{signal.video_id.lower()}",
                        label=provisional_label(feature.sparse_vector),
                        label_confidence=feature.confidence,
                        niche=signal.niche,
                        status="PRIVATE_CANDIDATE",
                        cluster_reason="single signal candidate; awaits independent cross-channel evidence",
                        model_metadata={"feature_model": feature.feature_model, "clustering_version": "lexical-v1"},
                    )
                    db.add(best)
                    db.flush()
                    clusters.append(best)
                    vectors[best.id] = dict(feature.sparse_vector)
                    created += 1
                    best_score = 1.0
                existing_channels = set(db.scalars(select(YoutubeSnipe.channel_id).join(TrendMembership, TrendMembership.youtube_snipe_id == YoutubeSnipe.id).where(TrendMembership.cluster_id == best.id)).all())
                same_channel = signal.channel_id in existing_channels
                db.add(TrendMembership(
                    cluster_id=best.id,
                    youtube_snipe_id=signal.id,
                    similarity_score=best_score,
                    membership_state="PROVISIONAL",
                    is_same_channel_duplicate=same_channel,
                    weight=0.25 if same_channel else 1.0,
                    feature_evidence={"feature_model": feature.feature_model, "similarity": best_score, "source_provenance": feature.source_provenance, "entry_view_count": signal.current_view_count, "entry_detected_at": signal.detected_at.isoformat(), "entry_age_hours": round(max(0.0, (signal.detected_at - signal.published_at).total_seconds() / 3600), 3) if signal.published_at else 0.0},
                ))
                best.member_count += 1
                best.channel_count = len(existing_channels | {signal.channel_id})
                best.observed_views += signal.current_view_count
                best.observed_velocity_per_hour += signal.velocity_per_hour
                best.last_member_at = datetime.now(UTC)
                best.last_observed_at = datetime.now(UTC)
                assigned += 1
            db.commit()
        store.set_status(last_trend_cluster_run_at=datetime.now(UTC).isoformat(), last_trend_candidates_created=created, last_trend_memberships_assigned=assigned)
        return {"candidates_created": created, "memberships_assigned": assigned}
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
    if metadata.get("early_topic_named") or len(signals) < 2:
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


@celery_app.task(bind=True, name="app.tasks.youtube_trend_tasks.score_topic_trends", soft_time_limit=300, time_limit=360)
def score_topic_trends(self: Task) -> dict[str, int | str]:
    """Aggregate observed evidence, snapshot it, and transition lifecycle states."""
    store = SeedStore()
    if not store.client.set(CLUSTER_LOCK + ":score", "1", nx=True, ex=280):
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
                if names_used < 4 and member_count >= 2 and not (cluster.model_metadata or {}).get("early_topic_named"):
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
        store.client.delete(CLUSTER_LOCK + ":score")
