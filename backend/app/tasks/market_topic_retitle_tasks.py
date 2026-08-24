"""Humanise generic market-topic labels with an evidence-backed LLM review.

Cluster labels are seeded from the FIRST member's per-video theme
(_human_label). A topic that grew since then can still carry a placeholder-ish
generic label ("music clips", "gta 5 superhero mods" is fine, "music videos
featuring named performers" is not something a human would say). This task
targets only topics whose label fails a generic-phrase test and asks the
review model to write one specific, human-readable label from member titles.
"""
from __future__ import annotations

import re

import httpx
from sqlalchemy import select

from app.database import SessionLocal
from app.models.market_trends import MarketTopic, MarketTopicMembership, MarketVideo
from app.services.market_semantic_client import MarketSemanticClient
from app.services.seed_store import SeedStore
from app.tasks.celery_app import celery_app

RETITLE_LOCK = "ycgc:youtube:lock:market-topic-retitle"
COOLDOWN = "ycgc:youtube:market-topic-retitle-cooldown"
RETITLE_CACHE = "ycgc:youtube:market-topic-retitle-attempt:"

# Labels a human would not recognise as a trend. Generic patterns first, then
# exact phrases seen in production.
_GENERIC_PATTERNS = (
    re.compile(r"\bfeaturing\b", re.I),
    re.compile(r"^(new|latest|best|top)\s+\w+\s+(releases?|videos?|clips?)$", re.I),
    re.compile(r"^(music|game|movie|sports?|news?|entertainment)(\s+(clips?|videos?|content))?$", re.I),
    re.compile(r"^(viral|trending)\s+(shorts?\s+)?(mix|clips?|videos?)$", re.I),
    re.compile(r"^(music|movie|game)\s+(lyric|trailer|gameplay)?\s*(videos?|releases?)(\s+and\s+\w+)?$", re.I),
)
_GENERIC_EXACT = {
    "music clips", "music video", "music videos", "viral clips", "funny clips",
    "gaming clips", "gaming videos", "trending shorts", "viral shorts",
    "music track", "music tracks", "artist tracks", "music content",
    "independent music releases", "independent music releases and artist tracks",
    "music lyric videos", "music videos and artist releases", "viral shorts mix",
}


def _is_generic_label(label: str) -> bool:
    value = (label or "").strip().lower()
    if len(value) < 4:
        return True
    if value in _GENERIC_EXACT:
        return True
    return any(pattern.search(value) for pattern in _GENERIC_PATTERNS)


@celery_app.task(name="app.tasks.market_topic_retitle_tasks.retitle_generic_market_topics")
def retitle_generic_market_topics() -> dict[str, int | str]:
    """Replace generic labels with specific, evidence-backed ones."""
    store = SeedStore()
    if store.client.exists(COOLDOWN) or not store.client.set(RETITLE_LOCK, "1", nx=True, ex=540):
        return {"status": "cooldown_or_locked"}
    updated = errors = 0
    try:
        with SessionLocal() as db:
            topics = db.scalars(
                select(MarketTopic)
                .where(MarketTopic.status != "MERGED", MarketTopic.member_count >= 2)
                .order_by(MarketTopic.member_count.desc())
                .limit(400)
            ).all()
            client = MarketSemanticClient()
            for topic in topics:
                if not _is_generic_label(topic.label):
                    continue
                # One attempt per topic per day: stop paying for hopeless
                # labels every 15 minutes.
                if store.client.exists(RETITLE_CACHE + str(topic.id)):
                    continue
                store.client.set(RETITLE_CACHE + str(topic.id), "1", ex=86400)

                members = db.scalars(
                    select(MarketVideo)
                    .join(MarketTopicMembership, MarketTopicMembership.market_video_id == MarketVideo.id)
                    .where(MarketTopicMembership.market_topic_id == topic.id)
                    .limit(8)
                ).all()
                if not members:
                    continue
                evidence = "\n".join(
                    f"- Title: {video.title or ''}" for video in members
                )
                prompt = f"""You rename a YouTube topic cluster so a human instantly understands the trend.
Members of this cluster (titles):
{evidence}

Current label (too generic): "{topic.label}"

Return exactly one JSON object: {{"label": "<new label>"}}
Rules for the new label:
- Indonesian-agnostic: keep it in English, lowercase, 2-6 words.
- Name the SPECIFIC shared subject across these videos: an artist, game, show, person, song, or event — never "music videos" or "artist releases".
- If the videos do NOT share one specific subject, return the single most precise genre/scene phrase instead (e.g. "german rap music", "deep house music").
- No trailing punctuation, no quotes, no emoji, max 60 characters."""
                try:
                    payload = client._request(prompt)
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 429:
                        store.client.set(COOLDOWN, "1", ex=3600)
                        break
                    errors += 1
                    continue
                except (RuntimeError, httpx.HTTPError):
                    errors += 1
                    continue
                new_label = str(payload.get("label") or "").strip().strip('"').strip()
                if 4 <= len(new_label) <= 60 and new_label.lower() != topic.label.strip().lower():
                    topic.label = new_label
                    updated += 1
            db.commit()
            return {"status": "ok", "updated": updated, "errors": errors}
    finally:
        store.client.delete(RETITLE_LOCK)
