"""Semantic topic labels for Market Shorts, kept separate from raw intake."""

from __future__ import annotations

import re
from hashlib import sha256
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models.market_trends import MarketVideo, MarketVideoFeature
from app.services.market_semantic_client import MarketSemanticClient
from app.services.seed_store import SeedStore
from app.tasks.celery_app import celery_app

SEMANTIC_FEATURE_MODEL = "market-semantic-v5"
SEMANTIC_VERSION = "market-semantic-v5"
COOLDOWN_KEY_PREFIX = "ycgc:youtube:market-semantic-gateway-cooldown"
STOP = {"the", "and", "for", "with", "this", "that", "from", "shorts", "short", "youtube", "copyright", "fair", "use", "rights", "reserved", "official", "video", "content", "www", "http", "https", "com"}


def _semantic_vector(topic_label: str, entities: list[str], event_context: str, summary: str) -> dict[str, float]:
    strong = " ".join(entities + [event_context, topic_label])
    tokens = re.findall(r"[\w']+", ((strong + " ") * 6 + summary).casefold())
    counts = Counter(token for token in tokens if len(token) > 2 and token not in STOP)
    norm = sum(value * value for value in counts.values()) ** 0.5 or 1
    return {token: round(value / norm, 5) for token, value in counts.most_common(80)}


def _semantic_request(client: MarketSemanticClient, video: MarketVideo):
    """A single network-bound semantic request for the bounded worker pool."""
    return client.analyze(video.title or "", video.description)


def _cooldown_key() -> str:
    """Scope a provider failure to its actual gateway/model configuration.

    A 429 from a retired provider must never freeze semantic enrichment after
    the operator switches to a working OpenAI-compatible gateway or model.
    The digest keeps credentials out of Redis key names and worker logs.
    """
    identity = f"{settings.market_semantic_base_url}|{settings.market_semantic_model}"
    return f"{COOLDOWN_KEY_PREFIX}:{sha256(identity.encode()).hexdigest()[:12]}"


@celery_app.task(name="app.tasks.market_gemini_tasks.enrich_market_topics")
def enrich_market_topics() -> dict[str, int | str]:
    """Label fresh Shorts first while safely draining the semantic backlog."""
    store = SeedStore()
    cooldown_key = _cooldown_key()
    if store.client.exists(cooldown_key):
        return {"updated": 0, "status": "cooldown"}

    updated = skipped = 0
    with SessionLocal() as db:
        all_rows = db.execute(
            select(MarketVideo, MarketVideoFeature).join(
                MarketVideoFeature, MarketVideoFeature.market_video_id == MarketVideo.id
            )
        ).all()
        stale = [
            row for row in all_rows
            if row[1].feature_model == SEMANTIC_FEATURE_MODEL
            and (
                (row[1].provenance or {}).get("market_semantic_version") != SEMANTIC_VERSION
                or (row[1].provenance or {}).get("semantic_model") != settings.market_semantic_model
            )
        ]
        pending = [row for row in all_rows if row[1].feature_model != SEMANTIC_FEATURE_MODEL]
        fresh_cutoff = datetime.now(UTC) - timedelta(hours=settings.market_topic_active_video_max_age_hours)
        prioritized = stale + pending
        prioritized.sort(key=lambda row: (
            row[0].published_at is None or row[0].published_at < fresh_cutoff,
            -(row[0].published_at.timestamp() if row[0].published_at else 0),
            -(row[0].last_seen_at.timestamp() if row[0].last_seen_at else 0),
        ))
        candidates = prioritized[:settings.market_gemini_batch_size]
        client = MarketSemanticClient()
        responses: dict[int, object] = {}
        with ThreadPoolExecutor(max_workers=max(1, settings.market_semantic_concurrency)) as pool:
            futures = {pool.submit(_semantic_request, client, video): video for video, _feature in candidates}
            for future in as_completed(futures):
                video = futures[future]
                try:
                    responses[video.id] = future.result()
                except Exception as exc:
                    responses[video.id] = exc

        for video, feature in candidates:
            result = responses.get(video.id)
            if result is None:
                skipped += 1
                continue
            if isinstance(result, Exception):
                if isinstance(result, httpx.HTTPStatusError) and result.response.status_code == 429:
                    store.client.set(cooldown_key, "1", ex=3600)
                    db.commit()
                    return {"updated": updated, "skipped": skipped, "status": "quota_cooldown"}
                if isinstance(result, httpx.HTTPError):
                    store.client.set(cooldown_key, "1", ex=900)
                skipped += 1
                continue
            facts = result
            feature.feature_model = SEMANTIC_FEATURE_MODEL
            feature.topic_hint = facts.topic_label
            feature.confidence = facts.confidence
            feature.normalized_text = f"{facts.topic_theme} {facts.topic_label} {' '.join(facts.entities)} {facts.event_context} {facts.summary}".strip()
            feature.sparse_vector = _semantic_vector(facts.topic_label, facts.entities, facts.event_context, facts.summary)
            feature.provenance = {
                **(feature.provenance or {}),
                "market_semantic_version": SEMANTIC_VERSION,
                "semantic_model": settings.market_semantic_model,
                "semantic_source": "title_description_only",
                "semantic": facts.model_dump(),
            }
            updated += 1
        db.commit()
    return {"updated": updated, "skipped": skipped, "queued_after": max(0, len(pending) - len(candidates))}
