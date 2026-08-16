"""Build conservative, entity-aware public Market Topics from Shorts evidence."""
from __future__ import annotations

import math
import re
from hashlib import sha256
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import desc, select

from app.config import settings
from app.database import SessionLocal
from app.models.market_trends import MarketContentTruthAudit, MarketRankedTopic, MarketRankedTopicMembership, MarketRankedTopicSnapshot, MarketVideo, MarketVideoFeature, MarketVideoObservation
from app.services.chart_topic_labeler import ChartVideo, TopicGroup, build_extraction_prompt, group_chart_videos, parse_extraction_response
from app.services.content_truth import summarize_content_truth
from app.services.market_semantic_client import MarketSemanticClient
from app.services.seed_store import SeedStore
from app.tasks.celery_app import celery_app

LOCK = "ycgc:youtube:lock:market-ranked-topics"
def _cooldown_key() -> str:
    identity = f"{settings.market_semantic_base_url}|{settings.market_semantic_model}"
    return f"ycgc:youtube:market-semantic-gateway-cooldown:{sha256(identity.encode()).hexdigest()[:12]}"
BACKFILL_CURSOR_KEY = "ycgc:youtube:market-ranked-topics-backfill-cursor"
GENERIC_LABELS = {"funny", "viral", "trending", "shorts", "music", "sports", "football", "soccer", "news", "movie", "video", "edit", "kick", "goal"}
GENERIC_CONTEXT = {"viral", "moment", "moments", "content", "short", "shorts", "tribute", "tributes", "fan", "fans", "discussion", "clips", "clip", "latest", "new"}
EVENT_TERMS = {"trial", "transfer", "trade", "match", "final", "goal", "interview", "dating", "relationship", "wedding", "film", "movie", "release", "tour", "concert", "challenge", "controversy", "award", "election", "arrest", "announcement"}
FOOTBALL_TERMS = {"football", "soccer", "ronaldo", "messi", "neymar", "mbappe", "yamal", "vinicius", "barcelona", "real", "madrid", "premier", "fifa", "worldcup"}


@dataclass
class SemanticGroup:
    group: TopicGroup
    label: str
    topic_type: str
    summary: str
    entities: list[str]
    confidence: float


def _tokens(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", value.casefold())


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")[:220]


def _is_followable_label(label: str) -> bool:
    words = _tokens(label)
    return len(words) >= 2 and not set(words).issubset(GENERIC_LABELS)


def _entity_signature(entities: list[str]) -> str:
    cleaned = sorted({_key(entity) for entity in entities if len(_tokens(entity)) >= 1 and _key(entity)})
    return "|".join(cleaned[:3])


def _context_signature(label: str, summary: str, entities: list[str], topic_type: str) -> str:
    entity_words = {word for entity in entities for word in _tokens(entity)}
    words = [word for word in _tokens(f"{label} {summary}") if word not in entity_words and word not in GENERIC_CONTEXT]
    event = next((word for word in words if word in EVENT_TERMS), "general")
    return f"{_key(topic_type or 'other')}:{event}"


def _entity_matches(video: ChartVideo, entities: list[str]) -> bool:
    """Exact token matching deliberately rejects Jamal as evidence for Yamal."""
    if not entities:
        return True
    haystack = set(_tokens(video.title))
    for entity in entities:
        words = _tokens(entity)
        # Full matching is preferred. A distinctive surname is accepted for
        # people because titles commonly say "Ronaldo" rather than the full
        # "Cristiano Ronaldo"; token equality still rejects Jamal vs Yamal.
        if words and (all(word in haystack for word in words) or (len(words) > 1 and words[-1] in haystack)):
            return True
    return False


def _shared_event_context(label: str, summary: str, videos: list[ChartVideo]) -> tuple[bool, float, list[str]]:
    """A person/category is not a trend; require one explicit shared event cue."""
    cues = {word for word in _tokens(f"{label} {summary}") if word in EVENT_TERMS}
    if not cues or not videos:
        return False, 0.0, []
    matching = sum(1 for video in videos if cues.intersection(_tokens(video.title)))
    coverage = matching / len(videos)
    return coverage >= .60, round(coverage, 3), sorted(cues)


def _dedupe_videos(videos: list[ChartVideo]) -> list[ChartVideo]:
    return list({item.video_id: item for item in videos}.values())


def _momentum(observations: list[MarketVideoObservation]) -> tuple[int, float, float | None]:
    ordered = sorted(observations, key=lambda item: item.observed_at)
    if not ordered:
        return 0, 0.0, None
    latest = ordered[-1]
    if len(ordered) < 2:
        return latest.view_count or 0, 0.0, None
    previous = ordered[-2]
    hours = (latest.observed_at - previous.observed_at).total_seconds() / 3600
    velocity = max(0.0, ((latest.view_count or 0) - (previous.view_count or 0)) / hours) if hours > 0 else 0.0
    acceleration = None
    if len(ordered) >= 3:
        earlier = ordered[-3]
        previous_hours = (previous.observed_at - earlier.observed_at).total_seconds() / 3600
        prior_velocity = max(0.0, ((previous.view_count or 0) - (earlier.view_count or 0)) / previous_hours) if previous_hours > 0 else 0.0
        if prior_velocity > 0:
            acceleration = (velocity - prior_velocity) / prior_velocity
    return latest.view_count or 0, round(velocity, 2), round(acceleration, 4) if acceleration is not None else None


def _market_age_weight(age_hours: float) -> float:
    """Keep seven-day context without allowing old reach to outrank fresh movement."""
    if age_hours <= settings.market_topic_full_weight_hours:
        return 1.0
    if age_hours <= settings.market_topic_strong_weight_hours:
        return 0.75
    if age_hours <= settings.market_topic_supporting_weight_hours:
        return 0.40
    return 0.15


def _score(*, members: float, channels: int, regions: int, views: int, velocity: float, acceleration: float | None, new_members: float, source_count: int, concentration: float, history_ready: bool) -> float:
    evidence = min(1.0, members / 8)
    diversity = min(1.0, channels / 5)
    coverage = min(1.0, regions / 3)
    reach = min(1.0, math.log1p(max(0, views)) / math.log1p(20_000_000))
    pace = min(1.0, math.log1p(max(0, velocity)) / math.log1p(300_000))
    novelty = min(1.0, new_members / 4)
    sources = min(1.0, source_count / 3)
    acceleration_bonus = min(1.0, max(0.0, acceleration or 0.0)) if history_ready else 0.0
    concentration_penalty = max(0.0, concentration - .55) * .30
    # Current topic momentum is primarily fresh independent evidence entering
    # the conversation, not accumulated lifetime views from old videos.
    return round(100 * (.18 * evidence + .22 * diversity + .08 * coverage + .05 * reach + .12 * pace + .25 * novelty + .06 * sources + .04 * acceleration_bonus - concentration_penalty), 2)


def _status(*, semantic: bool, members: int, channels: int, history_ready: bool, score: float, quiet_runs: int, organic_velocity: float, prior_organic_velocity: float) -> str:
    if not semantic or members < 3 or channels < 2:
        return "WATCHING"
    if not history_ready:
        return "EMERGING"
    # Do not let a topic remain "hot" only because old fresh evidence still
    # sits in the 72h window. It cools after three quiet scans *and* clear
    # organic slowdown. A later influx can promote it again automatically.
    organic_floor = max(1_000.0, prior_organic_velocity * .45)
    if quiet_runs >= 3 and prior_organic_velocity > 0 and organic_velocity <= organic_floor:
        return "COOLING"
    if members >= 5 and channels >= 3 and score >= 66:
        return "CONFIRMED"
    if score >= 48:
        return "ACCELERATING"
    return "EMERGING"


def _semantic_groups(videos: list[ChartVideo]) -> list[SemanticGroup]:
    # Per-video GLM fingerprints are the semantic source of truth. We do not
    # call Gemini here: the stronger 9Router reviewer is used later, only on
    # a handful of evidence-backed candidate groups.
    return []


def _fingerprint_groups(db, videos: list[ChartVideo]) -> list[SemanticGroup]:
    """Cluster pre-extracted entity/context fingerprints before title words.

    Each video is independently interpreted by Gemini first. This lets titles
    with different phrasing join through the same explicit entity or event.
    """
    if not videos:
        return []
    by_id = {video.video_id: video for video in videos}
    features = db.execute(
        select(MarketVideo.video_id, MarketVideoFeature.provenance, MarketContentTruthAudit.status, MarketContentTruthAudit.evidence)
        .join(MarketVideoFeature, MarketVideoFeature.market_video_id == MarketVideo.id)
        .outerjoin(MarketContentTruthAudit, MarketContentTruthAudit.market_video_id == MarketVideo.id)
        .where(MarketVideo.video_id.in_(by_id))
    ).all()
    grouped: dict[str, list[tuple[ChartVideo, dict]]] = defaultdict(list)
    for video_id, provenance, truth_status, truth_evidence in features:
        semantic = (provenance or {}).get("semantic")
        if not isinstance(semantic, dict) or float(semantic.get("confidence") or 0) < .55:
            continue
        # A factual MISMATCH is stronger than title metadata and must never
        # contribute to a semantic group. For ALIGNED clips, the named
        # entities observed in the content must agree with title entities.
        if truth_status == "MISMATCH":
            continue
        # Once an audit says the title agrees with the Short, prefer its
        # content-derived identity. Title/description semantics remain the
        # fallback only for evidence that has not yet been audited.
        content_semantic = (truth_evidence or {}).get("content_semantic") or (provenance or {}).get("content_semantic") or {}
        use_content = truth_status == "ALIGNED" and isinstance(content_semantic, dict) and bool(content_semantic.get("topic_label")) and float(content_semantic.get("confidence") or 0) >= .55
        content_entities = [item for item in (truth_evidence or {}).get("content_entities", []) if isinstance(item, str) and item.strip()]
        entities = [item for item in (content_entities if use_content and content_entities else semantic.get("entities", [])) if isinstance(item, str) and item.strip()]
        # Keep an entity only where it is supported by actual content. This
        # prevents a misleading title from dictating a person/event label.
        title_entities = [item for item in semantic.get("entities", []) if isinstance(item, str) and item.strip()]
        if truth_status == "ALIGNED" and title_entities and content_entities:
            title_entity_tokens = {_key(item) for item in title_entities}
            content_entity_tokens = {_key(item) for item in content_entities}
            if not title_entity_tokens.intersection(content_entity_tokens):
                continue
        event = str((content_semantic.get("event_context") if use_content else semantic.get("event_context")) or "").strip()
        label = str((content_semantic.get("topic_label") if use_content else semantic.get("topic_label")) or "").strip()
        topic_type = str((content_semantic.get("topic_type") if use_content else semantic.get("topic_type")) or "other")
        if event:
            key = f"event|{_entity_signature(entities) or _key(label)}|{_key(event)}"
        elif entities:
            key = f"entity|{_entity_signature(entities)}"
        else:
            continue
        if video_id in by_id:
            grouped[key].append((by_id[video_id], {**semantic, "entities": entities, "event_context": event, "topic_label": label, "topic_type": topic_type, "semantic_source": "content_audit" if use_content else "metadata"}))
    result: list[SemanticGroup] = []
    for members in grouped.values():
        clips = _dedupe_videos([item[0] for item in members])
        channels = {clip.channel_id for clip in clips if clip.channel_id}
        if len(clips) < 3 or len(channels) < 2:
            continue
        exemplar = members[0][1]
        entities = [item for item in exemplar.get("entities", []) if isinstance(item, str)]
        label = str(exemplar.get("topic_label") or "").strip()
        if not _is_followable_label(label):
            continue
        result.append(SemanticGroup(
            group=TopicGroup(label=label, videos=clips, channel_count=len(channels), region_count=len({clip.region for clip in clips if clip.region}), total_views=sum(clip.views for clip in clips), confidence="high", trend_type=str(exemplar.get("topic_type") or "other")),
            label=label,
            topic_type=str(exemplar.get("topic_type") or "other"),
            summary=str(exemplar.get("summary") or label),
            entities=entities,
            confidence=float(exemplar.get("confidence") or .55),
        ))
    return result


def _theme_fingerprint_groups(db, videos: list[ChartVideo]) -> list[SemanticGroup]:
    """Join truthful broad themes when individual Shorts have micro-labels.

    This is deliberately a separate, low-claim layer: it can create
    ``football player highlights`` from independently labelled player clips,
    but never invent a specific match, film, or event. Exact event claims
    continue through the stricter entity/context and content-truth gates.
    """
    if not videos:
        return []
    by_id = {video.video_id: video for video in videos}
    rows = db.execute(
        select(MarketVideo.video_id, MarketVideoFeature.provenance, MarketContentTruthAudit.status)
        .join(MarketVideoFeature, MarketVideoFeature.market_video_id == MarketVideo.id)
        .outerjoin(MarketContentTruthAudit, MarketContentTruthAudit.market_video_id == MarketVideo.id)
        .where(MarketVideo.video_id.in_(by_id))
    ).all()
    grouped: dict[tuple[str, str], list[tuple[ChartVideo, dict]]] = defaultdict(list)
    for video_id, provenance, truth_status in rows:
        if truth_status == "MISMATCH":
            continue
        semantic = (provenance or {}).get("semantic") or {}
        theme = str(semantic.get("topic_theme") or "").strip()
        theme_confidence = float(semantic.get("theme_confidence") or 0)
        topic_type = str(semantic.get("topic_type") or "other").strip()
        if not theme or theme_confidence < .65 or not _is_followable_label(theme):
            continue
        grouped[(topic_type, _key(theme))].append((by_id[video_id], semantic))
    result: list[SemanticGroup] = []
    for (topic_type, _theme_key), members in grouped.items():
        clips = _dedupe_videos([item[0] for item in members])
        channels = {clip.channel_id for clip in clips if clip.channel_id}
        if len(clips) < 3 or len(channels) < 2:
            continue
        exemplar = members[0][1]
        label = str(exemplar.get("topic_theme") or "").strip()
        result.append(SemanticGroup(
            group=TopicGroup(label=label, videos=clips, channel_count=len(channels), region_count=len({clip.region for clip in clips if clip.region}), total_views=sum(clip.views for clip in clips), confidence="medium", trend_type=f"{topic_type}_theme"),
            label=label,
            topic_type=f"{topic_type}_theme",
            summary=f"A cross-channel Shorts conversation grouped by the verified semantic theme: {label}.",
            entities=[],
            confidence=round(sum(float(item[1].get("theme_confidence") or 0) for item in members) / len(members), 3),
        ))
    return result


def _exclude_audited_mismatches(db, videos: list[ChartVideo]) -> tuple[list[ChartVideo], int]:
    """Remove only evidence disproved by its own transcript/visual audit.

    Unchecked evidence remains a candidate. This avoids silently shrinking
    market coverage while ensuring a known metadata hijack cannot re-enter a
    future cluster through title overlap.
    """
    if not videos:
        return [], 0
    mismatched = set(db.scalars(
        select(MarketVideo.video_id)
        .join(MarketContentTruthAudit, MarketContentTruthAudit.market_video_id == MarketVideo.id)
        .where(MarketVideo.video_id.in_([item.video_id for item in videos]), MarketContentTruthAudit.status == "MISMATCH")
    ).all())
    return [item for item in videos if item.video_id not in mismatched], len(mismatched)


def _name_evidence_groups(groups: list[TopicGroup]) -> list[SemanticGroup]:
    client = MarketSemanticClient()
    named: list[SemanticGroup] = []
    for group in groups[:settings.market_topic_review_batch_size]:
        evidence = "\n".join(f"- Title: {item.title}\n  Channel: {item.channel_title or 'unknown'}" for item in group.videos[:10])
        facts = client.review_topic_cluster(evidence)
        if not facts.followable or facts.confidence < .72 or not _is_followable_label(facts.topic_title):
            continue
        retained = _dedupe_videos([item for item in group.videos if _entity_matches(item, facts.entities)])
        channels = {item.channel_id for item in retained if item.channel_id}
        regions = {item.region for item in retained if item.region}
        # No entity means the cluster is still allowed only if it already has
        # strong independent evidence. Entity labels demand exact match.
        event_ok, _, _ = _shared_event_context(facts.topic_title, facts.summary, retained)
        if len(retained) < 3 or len(channels) < 2 or not event_ok:
            continue
        clean_group = TopicGroup(label=facts.topic_title.strip(), videos=retained, channel_count=len(channels), region_count=len(regions), total_views=sum(item.views for item in retained), confidence="high" if facts.confidence >= .86 else "medium", trend_type=facts.topic_type)
        named.append(SemanticGroup(group=clean_group, label=facts.topic_title.strip(), topic_type=facts.topic_type, summary=facts.summary.strip(), entities=facts.entities, confidence=facts.confidence))
    return named


def _market_themes(groups: list[SemanticGroup]) -> list[SemanticGroup]:
    """Retain broad recurring conversations without mislabelling them as events."""
    sports_videos = _dedupe_videos([video for item in groups if item.topic_type == "sports" for video in item.group.videos])
    sports_channels = {video.channel_id for video in sports_videos if video.channel_id}
    if len(sports_videos) < 6 or len(sports_channels) < 4:
        return []
    football_share = sum(bool(FOOTBALL_TERMS.intersection(_tokens(video.title))) for video in sports_videos) / len(sports_videos)
    label = "Football player highlights, comparisons & fan clips" if football_share >= .45 else "Sports highlights, comparisons & fan clips"
    summary_subject = "football" if football_share >= .45 else "sports"
    return [SemanticGroup(
        group=TopicGroup(label=label, videos=sports_videos, channel_count=len(sports_channels), region_count=len({video.region for video in sports_videos if video.region}), total_views=sum(video.views for video in sports_videos), confidence="high", trend_type="sports_theme"),
        label=label, topic_type="sports_theme",
        summary=f"A broad {summary_subject} conversation spanning player highlights, comparisons, fan clips and related reactions. This is a theme, not one claimed match or event.",
        entities=[], confidence=.82,
    )]


def _canonical_groups(groups: list[SemanticGroup]) -> list[SemanticGroup]:
    """Return the few broad, human-readable rows used by the leaderboard.

    Gemini is allowed to find granular evidence (Ronaldo, Messi, Yamal, etc.),
    but the primary product must not turn every overlapping name into a row.
    Specific events survive separately only when their explicit event gate has
    already passed.  Everything else becomes one market theme per conversation.
    """
    events: list[SemanticGroup] = []
    broad: list[SemanticGroup] = []
    entity_conversations: list[SemanticGroup] = []
    semantic_themes: list[SemanticGroup] = []
    for item in groups:
        # These are already broader, AI-derived conversation labels such as
        # "celebrity interviews and trivia".  Do not feed them back through
        # the old sports-only fallback or they silently disappear.
        if item.topic_type.endswith("_theme"):
            semantic_themes.append(item)
            continue
        event_ok, _, _ = _shared_event_context(item.label, item.summary, item.group.videos)
        if event_ok:
            events.append(item)
        elif item.entities and item.topic_type not in {"sports", "sports_theme"}:
            # A repeated named person/pair across independent channels is a
            # useful market conversation even before one explicit event word
            # appears. Keep it distinct from a claimed film/news event.
            entity_conversations.append(item)
        else:
            broad.append(item)

    themes = semantic_themes + _market_themes(broad)
    # A specific event is useful only when it is not merely a small slice of a
    # much broader row with the same evidence. This prevents duplicate rows
    # such as "Cristiano Ronaldo..." repeated beside a football theme.
    theme_ids = {video.video_id for theme in themes for video in theme.group.videos}
    retained_events: list[SemanticGroup] = []
    for event in events:
        event_ids = {video.video_id for video in event.group.videos}
        overlap = len(event_ids & theme_ids) / max(1, len(event_ids))
        if overlap < .75:
            retained_events.append(event)

    # Canonical key prevents wording variants of the same event from producing
    # several leaderboard rows in a single run.
    unique: dict[str, SemanticGroup] = {}
    for item in themes + retained_events + entity_conversations:
        key = (
            f"entity|{_entity_signature(item.entities)}"
            if item in entity_conversations
            else f"{item.topic_type}|{_key(item.label)}"
        )
        previous = unique.get(key)
        if previous is None or item.group.total_views > previous.group.total_views:
            unique[key] = item
    return list(unique.values())


def _source_mix(db, video_ids: list[int], cutoff: datetime) -> dict[str, int]:
    rows = db.execute(select(MarketVideoObservation.source_lane, MarketVideoObservation.market_video_id).where(MarketVideoObservation.market_video_id.in_(video_ids), MarketVideoObservation.observed_at >= cutoff)).all()
    return dict(Counter(row.source_lane for row in rows))


def _suppress_public_duplicates(db, now: datetime) -> int:
    """Hide duplicate public rows by evidence overlap while retaining audit data."""
    # A duplicate must stay hidden while its canonical topic is public.  The
    # previous implementation restored every hidden row on every pass, then
    # tried to hide it again. That made the result depend on task timing and
    # allowed identical topics to reappear together.
    public_topic_ids = set(
        db.scalars(
            select(MarketRankedTopic.id).where(
                MarketRankedTopic.status.in_(("EMERGING", "ACCELERATING", "CONFIRMED"))
            )
        ).all()
    )
    for archived in db.scalars(select(MarketRankedTopic).where(MarketRankedTopic.status == "WATCHING")).all():
        flags = archived.quality_flags or {}
        canonical_id = flags.get("deduplicated_into") or flags.get("deduplicated_into_theme")
        # Restore only when the original canonical topic has genuinely gone
        # away. This preserves audit history without permanently burying an
        # otherwise distinct topic.
        if canonical_id and canonical_id not in public_topic_ids:
            archived.status = "EMERGING"
            archived.quality_flags = {key: value for key, value in flags.items() if key not in {"deduplicated_into", "deduplicated_into_theme", "deduplication_overlap", "deduplicated_at"}}
    topics = db.scalars(select(MarketRankedTopic).where(MarketRankedTopic.status.in_(("EMERGING", "ACCELERATING", "CONFIRMED")))).all()
    memberships: dict[int, set[int]] = defaultdict(set)
    for topic_id, video_id in db.execute(
        select(MarketRankedTopicMembership.market_ranked_topic_id, MarketRankedTopicMembership.market_video_id)
    ).all():
        memberships[topic_id].add(video_id)

    def priority(topic: MarketRankedTopic) -> tuple[float, float, int]:
        flags = topic.quality_flags or {}
        kind = 2.0 if flags.get("topic_kind") == "event" else 1.0 if flags.get("entity_verified") else 0.0
        return kind, float(topic.trend_score or 0), len(topic.label or "")

    hidden = 0

    def hide(loser: MarketRankedTopic, winner: MarketRankedTopic, overlap: float) -> None:
        nonlocal hidden
        if loser.status not in {"EMERGING", "ACCELERATING", "CONFIRMED"}:
            return
        loser.status = "WATCHING"
        loser.quality_flags = {
            **(loser.quality_flags or {}),
            "deduplicated_into": winner.id,
            "deduplication_overlap": round(overlap, 3),
            "deduplicated_at": now.isoformat(),
        }
        hidden += 1

    # Exact member-set equality is not a judgement call: two rows backed by
    # the same Shorts are the same topic candidate. Resolve this first so a
    # UI can never show the identical evidence more than once.
    canonical_by_members: dict[tuple[int, ...], MarketRankedTopic] = {}
    for topic in topics:
        member_key = tuple(sorted(memberships.get(topic.id, set())))
        if not member_key or topic.status not in {"EMERGING", "ACCELERATING", "CONFIRMED"}:
            continue
        incumbent = canonical_by_members.get(member_key)
        if incumbent is None:
            canonical_by_members[member_key] = topic
            continue
        winner, loser = (incumbent, topic) if priority(incumbent) >= priority(topic) else (topic, incumbent)
        canonical_by_members[member_key] = winner
        hide(loser, winner, 1.0)

    # Different source batches can surface the same named event with mostly
    # different videos. Exact event identity is therefore also a duplicate
    # key, even where proof-set overlap is low. We keep the better-supported
    # row and retain the other row plus its raw evidence for audit.
    canonical_by_identity: dict[tuple[str, str], MarketRankedTopic] = {}
    for topic in topics:
        identity = (topic.entity_signature or "", topic.context_signature or "")
        if topic.status not in {"EMERGING", "ACCELERATING", "CONFIRMED"} or not all(identity):
            continue
        incumbent = canonical_by_identity.get(identity)
        if incumbent is None:
            canonical_by_identity[identity] = topic
            continue
        winner, loser = (incumbent, topic) if priority(incumbent) >= priority(topic) else (topic, incumbent)
        canonical_by_identity[identity] = winner
        hide(loser, winner, 0.0)

    for index, left in enumerate(topics):
        if left.status not in {"EMERGING", "ACCELERATING", "CONFIRMED"}:
            continue
        for right in topics[index + 1:]:
            if right.status not in {"EMERGING", "ACCELERATING", "CONFIRMED"}:
                continue
            a, b = memberships.get(left.id, set()), memberships.get(right.id, set())
            overlap = len(a & b) / max(1, min(len(a), len(b)))
            # Only collapse near-identical proof sets. Similar sports clips or
            # neighbouring conversations must remain distinct candidates.
            if overlap < 0.90:
                continue
            winner, loser = (left, right) if priority(left) >= priority(right) else (right, left)
            hide(loser, winner, overlap)

    return hidden


@celery_app.task(name="app.tasks.market_trending_topics_tasks.build_trending_topics")
def build_trending_topics() -> dict[str, int | str]:
    """Write only entity-verified, cross-channel, history-aware topic records."""
    store = SeedStore()
    if not store.client.set(LOCK, "1", nx=True, ex=840):
        return {"status": "skipped_locked"}
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=settings.market_metadata_window_hours)
    active_video_cutoff = now - timedelta(hours=settings.market_topic_active_video_max_age_hours)
    try:
        with SessionLocal() as db:
            # Native anonymous reel feed is the primary Shorts-only market
            # source. Charts and Apify only broaden coverage around it.
            observations = db.scalars(select(MarketVideoObservation).where(MarketVideoObservation.source_lane.in_(("anonymous_shorts_feed", "official_chart", "official_latest_sample", "apify")), MarketVideoObservation.observed_at >= cutoff).order_by(desc(MarketVideoObservation.observed_at))).all()
            by_video: dict[int, list[MarketVideoObservation]] = defaultdict(list)
            for item in observations:
                by_video[item.market_video_id].append(item)
            if not by_video:
                return {"status": "no_market_evidence", "topics": 0}
            videos = db.scalars(select(MarketVideo).where(MarketVideo.id.in_(by_video), MarketVideo.shorts_status == "VERIFIED_SHORTS")).all()
            latest = {video_id: max(items, key=lambda item: item.observed_at) for video_id, items in by_video.items()}
            # Observations can be recent even when the video is weeks old.
            # Only recently published Shorts may power a public "moving now"
            # topic; old rows stay in PostgreSQL as historical audit evidence.
            chart_videos = [ChartVideo(video_id=video.video_id, title=video.title or "", channel_id=video.channel_id, channel_title=video.channel_title, region=latest[video.id].region, rank=latest[video.id].source_rank, views=latest[video.id].view_count or 0, published_at=video.published_at.isoformat()) for video in videos if video.id in latest and video.published_at and video.published_at >= active_video_cutoff]
            chart_videos, excluded_metadata_mismatches = _exclude_audited_mismatches(db, chart_videos)
            if not chart_videos:
                return {"status": "no_verified_chart_shorts", "topics": 0}
            # Stored per-video fingerprints remain usable during a Gemini
            # cooldown; only *new* LLM requests are paused.
            fingerprint_groups = _fingerprint_groups(db, chart_videos)
            theme_groups = _theme_fingerprint_groups(db, chart_videos)
            groups: list[SemanticGroup] = list(fingerprint_groups) + list(theme_groups)
            if not store.client.exists(_cooldown_key()):
                try:
                    # Focused pass is intentionally always used: it gives the
                    # entity list required to reject false lexical matches.
                    broad = _semantic_groups(chart_videos)
                    lexical_seeds = [item.group for item in broad] or group_chart_videos(chart_videos, min_channels=2)
                    # The lexical fallback is only a candidate generator. Do
                    # not spend the naming budget on common filler tokens
                    # such as "ini", "funny", or "moments" while a repeated
                    # named entity/event waits below the cut.
                    meaningful_lexical_seeds = [
                        group for group in lexical_seeds
                        if group.label.casefold() not in GENERIC_LABELS
                        and group.label.casefold() not in GENERIC_CONTEXT
                    ]
                    # Revalidate the strongest pre-V2 clusters first. They are
                    # retained evidence, not public V2 results, and give the
                    # semantic model rich cross-channel material to promote.
                    chart_by_external_id = {item.video_id: item for item in chart_videos}
                    legacy_seeds: list[TopicGroup] = []
                    legacy_pool = db.scalars(select(MarketRankedTopic).where(~MarketRankedTopic.topic_key.like("v2|%"), MarketRankedTopic.status.in_(("EMERGING", "ACCELERATING", "CONFIRMED"))).order_by(desc(MarketRankedTopic.trend_score)).limit(50)).all()
                    # Rotate through retained V1 evidence rather than spending
                    # every Gemini batch on the same four high-score rows.
                    cursor = int(store.client.get(BACKFILL_CURSOR_KEY) or 0)
                    if legacy_pool:
                        start = cursor % len(legacy_pool)
                        legacy_topics = (legacy_pool[start:] + legacy_pool[:start])[:4]
                        store.client.set(BACKFILL_CURSOR_KEY, str((start + len(legacy_topics)) % len(legacy_pool)))
                    else:
                        legacy_topics = []
                    for legacy in legacy_topics:
                        member_ids = db.scalars(select(MarketRankedTopicMembership.market_video_id).where(MarketRankedTopicMembership.market_ranked_topic_id == legacy.id)).all()
                        member_models = db.scalars(select(MarketVideo).where(MarketVideo.id.in_(member_ids))).all() if member_ids else []
                        members = [chart_by_external_id[item.video_id] for item in member_models if item.video_id in chart_by_external_id]
                        channels = {item.channel_id for item in members if item.channel_id}
                        if len(members) >= 3 and len(channels) >= 2:
                            legacy_seeds.append(TopicGroup(label=legacy.label, videos=members, channel_count=len(channels), region_count=len({item.region for item in members if item.region}), total_views=sum(item.views for item in members)))
                    seeds = (meaningful_lexical_seeds[:10] + legacy_seeds)[:12]
                    groups = fingerprint_groups + theme_groups + _name_evidence_groups(seeds)
                    # The all-chart Gemini pass can already identify a real
                    # event across differently worded titles. Do not throw it
                    # away merely because a later per-cluster naming pass is
                    # too cautious. The shared-event gate below still decides
                    # whether it may become public.
                    if not groups and broad:
                        groups = broad
                    # Broad semantic evidence is intentionally collapsed
                    # before persistence. Individual people are useful proof,
                    # not automatically separate public topics.
                    groups = _canonical_groups(groups + broad)
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 429:
                        store.client.set(_cooldown_key(), "1", ex=3600)
                    store.set_status(market_ranked_topics_ai_error=f"http_{exc.response.status_code}")
                except Exception as exc:
                    store.set_status(market_ranked_topics_ai_error=type(exc).__name__)
            video_by_external_id = {video.video_id: video for video in videos}
            existing = {topic.topic_key: topic for topic in db.scalars(select(MarketRankedTopic)).all()}
            written = published = 0
            touched_topic_ids: set[int] = set()
            canonical_sports_theme_written = False
            for semantic_group in groups:
                group = semantic_group.group
                is_theme = semantic_group.topic_type.endswith("_theme")
                is_entity_conversation = bool(semantic_group.entities) and semantic_group.topic_type not in {"sports", "sports_theme"}
                entity_signature = _entity_signature(semantic_group.entities)
                context_signature = _context_signature(semantic_group.label, semantic_group.summary, semantic_group.entities, semantic_group.topic_type)
                event_ok, event_coverage, event_cues = _shared_event_context(semantic_group.label, semantic_group.summary, group.videos)
                if not is_theme and not event_ok and not is_entity_conversation:
                    continue
                # V2 key keeps stable identity through wording changes and
                # deliberately separates a real new event from a general topic.
                topic_key = f"v2|{entity_signature or _key(semantic_group.label)}|{context_signature}"[:255]
                prior_topic = existing.get(topic_key)
                prior_feedback = (prior_topic.quality_flags or {}).get("human_feedback") if prior_topic else None
                # Human review is an explicit active-learning signal. It does
                # not change discovery sources, but prevents the exact same
                # rejected semantic identity from cycling back onto the board.
                if prior_feedback in {"WRONG_MERGE", "TOO_GENERIC", "NOT_A_TREND"}:
                    continue
                member_models = [video_by_external_id[item.video_id] for item in group.videos if item.video_id in video_by_external_id]
                if len(member_models) < 3:
                    continue
                topic = existing.get(topic_key)
                if topic is None:
                    topic = MarketRankedTopic(topic_key=topic_key, label=semantic_group.label, scope="official_chart")
                    db.add(topic); db.flush(); existing[topic_key] = topic
                touched_topic_ids.add(topic.id)
                memberships = db.scalars(select(MarketRankedTopicMembership).where(MarketRankedTopicMembership.market_ranked_topic_id == topic.id)).all()
                prior_active_ids = {membership.market_video_id for membership in memberships if membership.evidence_role == "active_evidence"}
                active_ids = {video.id for video in member_models}
                previous = db.scalars(select(MarketRankedTopicSnapshot).where(MarketRankedTopicSnapshot.market_ranked_topic_id == topic.id).order_by(desc(MarketRankedTopicSnapshot.observed_at)).limit(1)).first()
                observation_count = db.scalar(select(MarketRankedTopicSnapshot.id).where(MarketRankedTopicSnapshot.market_ranked_topic_id == topic.id).count()) if False else len(db.scalars(select(MarketRankedTopicSnapshot).where(MarketRankedTopicSnapshot.market_ranked_topic_id == topic.id)).all())
                history_ready = observation_count + 1 >= 3
                total_views = weighted_views = total_velocity = organic_velocity = effective_members = 0.0
                for video in member_models:
                    view_count, velocity, acceleration = _momentum(by_video[video.id])
                    age_hours = max(0.0, (now - video.published_at).total_seconds() / 3600) if video.published_at else settings.market_topic_active_video_max_age_hours
                    age_weight = _market_age_weight(age_hours)
                    total_views += view_count
                    weighted_views += view_count * age_weight
                    total_velocity += velocity * age_weight
                    effective_members += age_weight
                    # A new Short expands coverage, but its lifetime views must
                    # never be counted as a growth spike for this topic.
                    if video.id in prior_active_ids:
                        organic_velocity += velocity * age_weight
                channels = Counter(video.channel_id or f"unknown:{video.id}" for video in member_models)
                average_velocity = total_velocity / max(1.0, effective_members)
                organic_measurement_ready = bool(prior_active_ids)
                prior_organic_velocity = previous.organic_velocity_per_hour if previous and previous.organic_measurement_ready else 0.0
                organic_acceleration = ((organic_velocity - prior_organic_velocity) / prior_organic_velocity) if organic_measurement_ready and prior_organic_velocity > 0 else None
                mix = _source_mix(db, [video.id for video in member_models], cutoff)
                new_members = len(active_ids - prior_active_ids)
                weighted_new_members = sum(
                    _market_age_weight(max(0.0, (now - video.published_at).total_seconds() / 3600))
                    for video in member_models if video.id not in prior_active_ids and video.published_at
                )
                previous_flags = topic.quality_flags or {}
                quiet_runs = 0 if new_members else int(previous_flags.get("quiet_runs", 0)) + 1
                concentration = max(channels.values()) / len(member_models)
                score = _score(members=effective_members, channels=len(channels), regions=group.region_count, views=int(weighted_views), velocity=organic_velocity, acceleration=organic_acceleration, new_members=weighted_new_members, source_count=len(mix), concentration=concentration, history_ready=history_ready)
                category_counts = Counter(video.category_id for video in member_models if video.category_id)
                fresh_buckets = {"0_24h": 0, "24_72h": 0, "72_120h": 0, "120_168h": 0}
                ages = []
                for video in member_models:
                    if not video.published_at:
                        continue
                    age_hours = max(0.0, (now - video.published_at).total_seconds() / 3600)
                    ages.append(age_hours)
                    if age_hours < 24:
                        fresh_buckets["0_24h"] += 1
                    elif age_hours < 72:
                        fresh_buckets["24_72h"] += 1
                    elif age_hours < 120:
                        fresh_buckets["72_120h"] += 1
                    else:
                        fresh_buckets["120_168h"] += 1
                topic.label, topic.topic_type, topic.category_key = semantic_group.label, semantic_group.topic_type or "other", category_counts.most_common(1)[0][0] if category_counts else None
                topic.entity_signature, topic.context_signature, topic.semantic_summary = entity_signature or None, context_signature, semantic_group.summary
                topic.semantic_confidence, topic.member_count, topic.channel_count, topic.region_count = semantic_group.confidence, len(member_models), len(channels), group.region_count
                topic.observed_views, topic.observed_velocity_per_hour, topic.organic_velocity_per_hour, topic.acceleration, topic.trend_score = int(total_views), round(average_velocity, 2), round(organic_velocity, 2), round(organic_acceleration, 4) if organic_acceleration is not None else None, score
                topic.source_mix = mix
                # Metadata is only a discovery hint. A shared title/event term
                # cannot become a public event until the content-truth lane
                # has checked real transcripts/visual evidence for at least
                # two independent Shorts. This catches SEO title hijacking
                # without deleting the original raw evidence.
                member_ids = [video.id for video in member_models]
                truth_audits = db.scalars(
                    select(MarketContentTruthAudit).where(MarketContentTruthAudit.market_video_id.in_(member_ids))
                ).all()
                requires_content_truth = not is_theme and (event_ok or is_entity_conversation)
                truth = summarize_content_truth(truth_audits, len(member_models), required=requires_content_truth)
                topic.quality_flags = {
                    "topic_kind": "theme" if is_theme else ("entity_conversation" if is_entity_conversation else "event"),
                    "entity_verified": bool(entity_signature),
                    "event_context_verified": event_ok,
                    "event_context_coverage": event_coverage,
                    "event_cues": event_cues,
                    "content_truth": truth.as_dict(),
                    "history_ready": history_ready,
                    "new_member_count": new_members,
                    "weighted_new_member_count": round(weighted_new_members, 2),
                    "quiet_runs": quiet_runs,
                    "channel_concentration": round(concentration, 3),
                    "source_count": len(mix),
                    "freshness": {"window_hours": settings.market_topic_active_video_max_age_hours, "buckets": fresh_buckets, "oldest_hours": round(max(ages), 1) if ages else None, "newest_hours": round(min(ages), 1) if ages else None, "effective_members": round(effective_members, 2), "recency_weights": "0-24h:1.0, 24-72h:0.75, 72-120h:0.40, 120-168h:0.15"},
                    "publication_rule": "broad cross-channel theme" if is_theme else ("3 Shorts / 2 channels / shared named entity + content verification" if is_entity_conversation else "3 Shorts / 2 channels / shared explicit event + content verification"),
                }
                base_status = _status(semantic=True, members=len(member_models), channels=len(channels), history_ready=history_ready, score=score, quiet_runs=quiet_runs, organic_velocity=organic_velocity, prior_organic_velocity=prior_organic_velocity)
                topic.status = truth.status if truth.status != "VALIDATED" else base_status
                topic.last_observed_at = now
                canonical_sports_theme_written = canonical_sports_theme_written or semantic_group.topic_type == "sports_theme"
                known = {membership.market_video_id: membership for membership in memberships}
                for membership in memberships:
                    if membership.market_video_id not in active_ids:
                        membership.evidence_role = "historical"
                for video in member_models:
                    if video.id not in known:
                        db.add(MarketRankedTopicMembership(market_ranked_topic_id=topic.id, market_video_id=video.id, evidence_role="active_evidence", confidence=semantic_group.confidence))
                    else:
                        known[video.id].evidence_role = "active_evidence"
                db.add(MarketRankedTopicSnapshot(market_ranked_topic_id=topic.id, observed_at=now, observed_views=int(total_views), observed_velocity_per_hour=round(average_velocity, 2), organic_velocity_per_hour=round(organic_velocity, 2), organic_measurement_ready=organic_measurement_ready, acceleration=round(organic_acceleration, 4) if organic_acceleration is not None else None, member_count=len(member_models), channel_count=len(channels), region_count=group.region_count, new_member_count=new_members, source_count=len(mix), history_ready=history_ready, trend_score=score))
                written += 1
                if topic.status in {"EMERGING", "ACCELERATING", "CONFIRMED"}: published += 1
            if canonical_sports_theme_written:
                # Older player-name clusters remain auditable but stop
                # competing with the single canonical football/sports row.
                for duplicate in db.scalars(select(MarketRankedTopic).where(MarketRankedTopic.topic_key.like("v2|%"), MarketRankedTopic.topic_type == "sports")).all():
                    duplicate.status = "WATCHING"
            suppressed_duplicates = _suppress_public_duplicates(db, now)
            # Never hide a public topic merely because one semantic pass did
            # not return a group (for example Gemini cooldown or a transient
            # source gap). It may be cooled only after its *stored evidence*
            # is objectively outside the active freshness window.
            for stale_topic in db.scalars(select(MarketRankedTopic).where(MarketRankedTopic.topic_key.like("v2|%"))).all():
                if stale_topic.id not in touched_topic_ids and stale_topic.status not in {"WATCHING", "AWAITING_CONTENT_VALIDATION", "QUARANTINED_METADATA_MISMATCH"}:
                    fresh_members = db.scalar(
                        select(MarketRankedTopicMembership.id)
                        .join(MarketVideo, MarketVideo.id == MarketRankedTopicMembership.market_video_id)
                        .where(
                            MarketRankedTopicMembership.market_ranked_topic_id == stale_topic.id,
                            MarketRankedTopicMembership.evidence_role == "active_evidence",
                            MarketVideo.published_at >= active_video_cutoff,
                        )
                        .limit(1)
                    )
                    if fresh_members is None:
                        stale_topic.status = "COOLING"
            for topic in db.scalars(select(MarketRankedTopic).where(MarketRankedTopic.last_observed_at < now - timedelta(hours=settings.market_metadata_window_hours))).all():
                if topic.status not in {"WATCHING", "AWAITING_CONTENT_VALIDATION", "QUARANTINED_METADATA_MISMATCH"}:
                    topic.status = "COOLING"
            archive_cutoff = now - timedelta(hours=settings.market_topic_archive_after_hours)
            for topic in db.scalars(select(MarketRankedTopic).where(MarketRankedTopic.last_observed_at < archive_cutoff)).all():
                # Quarantined evidence stays in the quality-audit queue;
                # normal cooled topics become archive-only after two weeks.
                if topic.status == "COOLING":
                    topic.status = "ARCHIVED"
            db.commit()
        store.set_status(market_ranked_topics_last_run_at=now.isoformat(), market_ranked_topics_written=written, market_ranked_topics_public=published, market_ranked_topics_suppressed_duplicates=suppressed_duplicates, market_ranked_topics_metadata_mismatches_excluded=excluded_metadata_mismatches, market_ranked_topics_semantic=bool(groups), market_ranked_topics_version="v3_content_truth_lifecycle")
        return {"status": "ok", "topics": written, "public": published, "suppressed_duplicates": suppressed_duplicates, "metadata_mismatches_excluded": excluded_metadata_mismatches, "semantic": bool(groups)}
    finally:
        store.client.delete(LOCK)
