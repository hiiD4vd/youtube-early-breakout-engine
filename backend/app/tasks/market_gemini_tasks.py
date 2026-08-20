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
from app.services.transcript import fetch_transcript
from app.tasks.celery_app import celery_app

SEMANTIC_FEATURE_MODEL = "market-semantic-v6"
SEMANTIC_VERSION = "market-semantic-v6"
COOLDOWN_KEY_PREFIX = "ycgc:youtube:market-semantic-gateway-cooldown"
STOP = {"the", "and", "for", "with", "this", "that", "from", "shorts", "short", "youtube", "copyright", "fair", "use", "rights", "reserved", "official", "video", "content", "www", "http", "https", "com"}


def _semantic_vector(
    topic_label: str,
    entities: list[str],
    event_context: str,
    summary: str,
    topic_theme: str = "",
    content_format: str = "",
) -> dict[str, float]:
    # The earlier vector ignored ``topic_theme`` and ``content_format`` even
    # though those fields contain the best cross-video grouping signal.  That
    # made two ranking videos about different objects look unrelated.  Weight
    # the shared human-followable theme/format alongside explicit entities.
    strong = " ".join(entities + [event_context, topic_theme, content_format, topic_label])
    tokens = re.findall(r"[\w']+", ((strong + " ") * 6 + summary).casefold())
    counts = Counter(token for token in tokens if len(token) > 2 and token not in STOP)
    norm = sum(value * value for value in counts.values()) ** 0.5 or 1
    return {token: round(value / norm, 5) for token, value in counts.most_common(80)}


def _semantic_request(client: MarketSemanticClient, video: MarketVideo):
    """A single network-bound semantic request for the bounded worker pool.

    Shorts titles are often clickbait or vague, so fetch the transcript for a
    Short to give the semantic model real content signal. Landscape videos
    already carry descriptive titles; fetching their captions would only add
    yt-dlp load without improving the fingerprint.
    """
    transcript = None
    if video.shorts_status == "VERIFIED_SHORTS" and video.video_url:
        transcript = fetch_transcript(video.video_url)
    return client.analyze(video.title or "", video.description, transcript)


def _cooldown_key() -> str:
    """Scope a provider failure to its actual gateway/model configuration."""
    identity = f"{settings.market_semantic_base_url}|{settings.market_semantic_model}"
    return f"{COOLDOWN_KEY_PREFIX}:{sha256(identity.encode()).hexdigest()[:12]}"


@celery_app.task(name="app.tasks.market_gemini_tasks.enrich_market_topics")
def enrich_market_topics() -> dict[str, int | str]:
    """Label a balanced mix of Shorts and ordinary videos.

    A single freshness-sorted queue allowed continuously arriving Shorts to
    starve ordinary videos forever. Each run now reserves capacity for both
    media types, then fills unused slots from the remaining freshest rows.
    """
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
        batch_size = settings.market_gemini_batch_size
        shorts_rows = [row for row in prioritized if row[0].shorts_status == "VERIFIED_SHORTS"]
        video_rows = [row for row in prioritized if row[0].shorts_status == "REJECTED_NOT_SHORTS"]
        total_waiting = max(1, len(shorts_rows) + len(video_rows))
        # Follow the actual backlog while guaranteeing that neither format can
        # consume less than 25% or more than 75% of a mixed batch.
        video_share = len(video_rows) / total_waiting
        video_quota = round(batch_size * min(0.75, max(0.25, video_share))) if shorts_rows and video_rows else (batch_size if video_rows else 0)
        shorts_quota = batch_size - video_quota
        candidates = shorts_rows[:shorts_quota] + video_rows[:video_quota]
        selected_ids = {row[0].id for row in candidates}
        if len(candidates) < batch_size:
            candidates.extend(
                row for row in prioritized
                if row[0].id not in selected_ids
            )
            candidates = candidates[:batch_size]
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
                    store.set_status(
                        market_semantic_provider_state="rate_limited",
                        market_semantic_provider_last_error="HTTP 429",
                        market_semantic_provider_last_error_at=datetime.now(UTC).isoformat(),
                    )
                    db.commit()
                    return {"updated": updated, "skipped": skipped, "status": "quota_cooldown"}
                if isinstance(result, httpx.HTTPError):
                    # DNS/socket failures are infrastructure incidents, not
                    # provider quota. Retry soon after the container network
                    # recovers instead of freezing enrichment for 15 minutes.
                    cooldown_seconds = 120 if isinstance(result, httpx.ConnectError) else 900
                    store.client.set(cooldown_key, "1", ex=cooldown_seconds)
                    status_code = result.response.status_code if isinstance(result, httpx.HTTPStatusError) else None
                    store.set_status(
                        market_semantic_provider_state=("connectivity_error" if isinstance(result, httpx.ConnectError) else "error"),
                        market_semantic_provider_last_error=f"HTTP {status_code}" if status_code else type(result).__name__,
                        market_semantic_provider_last_error_at=datetime.now(UTC).isoformat(),
                    )
                else:
                    store.set_status(
                        market_semantic_provider_state="invalid_response",
                        market_semantic_provider_last_error=type(result).__name__,
                        market_semantic_provider_last_error_at=datetime.now(UTC).isoformat(),
                    )
                skipped += 1
                continue
            facts = result
            feature.feature_model = SEMANTIC_FEATURE_MODEL
            feature.topic_hint = facts.topic_label
            feature.confidence = facts.confidence
            feature.normalized_text = f"{facts.topic_theme} {facts.topic_label} {' '.join(facts.entities)} {facts.event_context} {facts.summary}".strip()
            feature.sparse_vector = _semantic_vector(
                facts.topic_label,
                facts.entities,
                facts.event_context,
                facts.summary,
                facts.topic_theme,
                facts.content_format,
            )
            feature.provenance = {
                **(feature.provenance or {}),
                "market_semantic_version": SEMANTIC_VERSION,
                "semantic_model": settings.market_semantic_model,
                "semantic_source": "title_description_only",
                "semantic": facts.model_dump(),
            }
            updated += 1
        db.commit()
    if updated:
        store.set_status(
            market_semantic_provider_state="healthy",
            market_semantic_provider_last_success_at=datetime.now(UTC).isoformat(),
            market_semantic_provider_last_error="",
        )
    return {"updated": updated, "skipped": skipped, "queued_after": max(0, len(pending) - len(candidates))}
