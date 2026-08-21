"""Bounded semantic clustering for the exploratory Market Topic pool."""
from __future__ import annotations

import re
from collections import defaultdict
from math import sqrt

from sqlalchemy import select

from app.database import SessionLocal
from app.models.market_trends import MarketTopic, MarketTopicMembership, MarketVideo, MarketVideoFeature
from app.services.seed_store import SeedStore
from app.tasks.celery_app import celery_app

TOPIC_MUTATION_LOCK = "ycgc:youtube:lock:market-topic-membership-mutation"
SEMANTIC_MODEL = "market-semantic-v6"
MIN_COSINE = 0.62
MIN_EXACT_THEME_COSINE = 0.48


def _dot(a: dict[str, float] | None, b: dict[str, float] | None) -> float:
    return sum(float(value) * float((b or {}).get(key, 0)) for key, value in (a or {}).items())


def _normalize(vector: dict[str, float]) -> dict[str, float]:
    norm = sqrt(sum(float(value) ** 2 for value in vector.values())) or 1.0
    return {key: float(value) / norm for key, value in vector.items()}


def _centroid(features: list[MarketVideoFeature]) -> dict[str, float]:
    total: dict[str, float] = defaultdict(float)
    for feature in features:
        for key, value in (feature.sparse_vector or {}).items():
            total[key] += float(value)
    if not features:
        return {}
    return _normalize({key: value / len(features) for key, value in total.items()})


def _semantic(feature: MarketVideoFeature) -> dict:
    value = (feature.provenance or {}).get("semantic")
    return value if isinstance(value, dict) else {}


def _phrase(value: object) -> str:
    return " ".join(re.findall(r"[\w']+", str(value or "").casefold()))


_THEME_STOPWORDS = {"and", "the", "a", "an", "of", "for", "with", "to", "in", "on", "or"}


def _theme_jaccard(a: str, b: str) -> float:
    """Token-set similarity between normalized theme phrases."""
    if not a or not b:
        return 0.0
    ta = {token for token in a.split() if token not in _THEME_STOPWORDS}
    tb = {token for token in b.split() if token not in _THEME_STOPWORDS}
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _identity(feature: MarketVideoFeature) -> tuple[str, str, set[str], str]:
    semantic = _semantic(feature)
    theme = _phrase(semantic.get("topic_theme"))
    content_format = _phrase(semantic.get("content_format"))
    entities = {_phrase(item) for item in semantic.get("entities", []) if _phrase(item)}
    topic_type = _phrase(semantic.get("topic_type"))
    return theme, content_format, entities, topic_type


def _identity_keys(feature: MarketVideoFeature) -> set[str]:
    """Return bounded lookup keys; generic lexical overlap is never a key."""
    theme, content_format, entities, topic_type = _identity(feature)
    keys: set[str] = set()
    if theme:
        keys.add(f"theme:{theme}")
        tokens = theme.split()
        if len(tokens) >= 2:
            keys.add(f"theme_stem:{' '.join(tokens[:2])}")
    if content_format:
        keys.add(f"format:{content_format}")
    if topic_type:
        keys.update(f"entity:{topic_type}:{entity}" for entity in entities)
    return keys


def _compatible(candidate: MarketVideoFeature, anchors: list[MarketVideoFeature]) -> tuple[bool, bool]:
    """Require semantic agreement before cosine similarity may merge rows."""
    candidate_theme, candidate_format, candidate_entities, candidate_type = _identity(candidate)
    for anchor in anchors[:8]:
        theme, content_format, entities, topic_type = _identity(anchor)
        if candidate_theme and theme and candidate_theme == theme:
            return True, True
        if candidate_format and content_format and candidate_format == content_format:
            return True, True
        if candidate_theme and theme and _theme_jaccard(candidate_theme, theme) >= 0.5:
            return True, False
        if candidate_entities & entities and candidate_type and candidate_type == topic_type:
            return True, False
    return False, False


def _human_label(feature: MarketVideoFeature) -> str:
    semantic = _semantic(feature)
    try:
        confidence = float(semantic.get("theme_confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0
    theme = str(semantic.get("topic_theme") or "").strip()
    return (theme if confidence >= 0.60 and len(theme) >= 8 else feature.topic_hint or "Topik belum dinamai")[:255]


@celery_app.task(name="app.tasks.market_cluster_tasks.cluster_market_topics")
def cluster_market_topics() -> dict[str, int | str]:
    """Assign only unclustered semantic rows; never rewrite raw evidence."""
    store = SeedStore()
    if not store.client.set(TOPIC_MUTATION_LOCK, "1", nx=True, ex=280):
        return {"status": "skipped_locked", "created": 0, "assigned": 0}
    created = assigned = rejected_attraction = 0
    try:
        with SessionLocal() as db:
            features = [
                feature for feature in db.scalars(
                    select(MarketVideoFeature).where(MarketVideoFeature.feature_model == SEMANTIC_MODEL)
                ).all()
                if (feature.provenance or {}).get("market_semantic_version") == SEMANTIC_MODEL
                and isinstance((feature.provenance or {}).get("semantic"), dict)
                and float(feature.confidence or 0) >= 0.55
                and _identity_keys(feature)
            ]
            membership_rows = db.scalars(select(MarketTopicMembership)).all()
            assigned_video_ids = {membership.market_video_id for membership in membership_rows}
            topics = db.scalars(select(MarketTopic).where(MarketTopic.member_count > 0)).all()
            topic_ids = {topic.id for topic in topics}
            features_by_video = {feature.market_video_id: feature for feature in features}
            topic_features: dict[int, list[MarketVideoFeature]] = defaultdict(list)
            for membership in membership_rows:
                feature = features_by_video.get(membership.market_video_id)
                if feature and membership.market_topic_id in topic_ids:
                    topic_features[membership.market_topic_id].append(feature)
            centroids = {topic.id: _centroid(topic_features[topic.id]) for topic in topics}
            # Avoid O(videos x every historical topic). A video may only be
            # compared with topics sharing a real semantic identity key.
            topics_by_identity: dict[str, set[int]] = defaultdict(set)
            topics_by_id = {topic.id: topic for topic in topics}
            for topic in topics:
                for anchor in topic_features[topic.id][:8]:
                    for key in _identity_keys(anchor):
                        topics_by_identity[key].add(topic.id)
            # Do not persist thousands of one-video "topics". Unmatched rows
            # wait in memory until an independent second video with compatible
            # semantics appears in this batch. If no peer appears, the video
            # remains safely unassigned and can be reconsidered next run.
            unmatched_by_identity: dict[str, list[MarketVideoFeature]] = defaultdict(list)
            paired_pending_ids: set[int] = set()

            for feature in features:
                if feature.market_video_id in assigned_video_ids or feature.market_video_id in paired_pending_ids:
                    continue
                candidates: list[tuple[float, MarketTopic]] = []
                candidate_topic_ids: set[int] = set()
                for key in _identity_keys(feature):
                    candidate_topic_ids.update(topics_by_identity.get(key, set()))
                normalized_feature = _normalize(feature.sparse_vector or {})
                for topic_id in candidate_topic_ids:
                    topic = topics_by_id[topic_id]
                    compatible, exact_identity = _compatible(feature, topic_features[topic.id])
                    if not compatible:
                        continue
                    score = min(1.0, _dot(normalized_feature, centroids.get(topic.id)))
                    threshold = MIN_EXACT_THEME_COSINE if exact_identity else MIN_COSINE
                    if score >= threshold:
                        candidates.append((score, topic))
                    else:
                        rejected_attraction += 1
                if candidates:
                    score, best = max(candidates, key=lambda item: item[0])
                else:
                    pending_candidates: dict[int, MarketVideoFeature] = {}
                    for key in _identity_keys(feature):
                        for pending in unmatched_by_identity.get(key, []):
                            if pending.market_video_id not in paired_pending_ids:
                                pending_candidates[pending.market_video_id] = pending
                    pair: tuple[float, MarketVideoFeature] | None = None
                    normalized_feature = _normalize(feature.sparse_vector or {})
                    for pending in pending_candidates.values():
                        compatible, exact_identity = _compatible(feature, [pending])
                        if not compatible:
                            continue
                        score = min(1.0, _dot(normalized_feature, _normalize(pending.sparse_vector or {})))
                        threshold = MIN_EXACT_THEME_COSINE if exact_identity else MIN_COSINE
                        if score >= threshold and (pair is None or score > pair[0]):
                            pair = (score, pending)
                    if pair is None:
                        for key in _identity_keys(feature):
                            unmatched_by_identity[key].append(feature)
                        continue
                    score, pending = pair
                    best = MarketTopic(label=_human_label(feature))
                    db.add(best)
                    db.flush()
                    topics.append(best)
                    topics_by_id[best.id] = best
                    topic_features[best.id] = [pending]
                    centroids[best.id] = _centroid(topic_features[best.id])
                    db.add(MarketTopicMembership(
                        market_topic_id=best.id,
                        market_video_id=pending.market_video_id,
                        similarity_score=1.0,
                    ))
                    paired_pending_ids.add(pending.market_video_id)
                    created += 1
                    assigned += 1
                db.add(MarketTopicMembership(
                    market_topic_id=best.id,
                    market_video_id=feature.market_video_id,
                    similarity_score=score,
                ))
                topic_features[best.id].append(feature)
                centroids[best.id] = _centroid(topic_features[best.id])
                for key in _identity_keys(feature):
                    topics_by_identity[key].add(best.id)
                assigned += 1

            db.flush()
            grouped_members: dict[int, list[MarketVideo]] = defaultdict(list)
            for membership, video in db.execute(
                select(MarketTopicMembership, MarketVideo).join(
                    MarketVideo, MarketVideo.id == MarketTopicMembership.market_video_id
                )
            ).all():
                grouped_members[membership.market_topic_id].append(video)
            for topic in topics:
                members = grouped_members.get(topic.id, [])
                topic.member_count = len(members)
                topic.channel_count = len({video.channel_id for video in members if video.channel_id})
                topic.status = "EMERGING" if topic.member_count >= 2 and topic.channel_count >= 2 else "PRIVATE_CANDIDATE"
            db.commit()
        return {
            "status": "ok",
            "created": created,
            "assigned": assigned,
            "rejected_broad_attraction": rejected_attraction,
        }
    finally:
        store.client.delete(TOPIC_MUTATION_LOCK)
