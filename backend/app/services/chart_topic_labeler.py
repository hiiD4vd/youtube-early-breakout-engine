"""LLM-first topic extraction for official-chart videos.

Primary path: send all chart titles to Gemini in one call; the LLM identifies
topics semantically and assigns video indices to each topic. This mirrors the
ViralEngine approach (Groq Llama on TikTok captions) and avoids the lexical
grouping failure mode where videos about the same topic but with different
titles never cluster.

Fallback path: deterministic lexical grouper (shared cross-channel tokens)
used when Gemini is unavailable or on cooldown.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

# Tokens that carry no topical meaning; ignored as seed candidates.
_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of",
    "with", "by", "from", "is", "are", "was", "were", "be", "been", "being",
    "this", "that", "these", "those", "it", "its", "as", "vs", "vs.", "official",
    "video", "shorts", "short", "yt", "youtube", "new", "best", "top", "most",
    "part", "ep", "episode", "full", "hd", "4k", "live", "reaction", "react",
    "compilation", "mix", "remix", "feat", "ft", "official", "music", "audio",
    "lyrics", "cover", "version", "edit", "tutorial", "how", "to", "diy",
    "viral", "trending", "fyp", "foryou", "foryoupage", "parati", "topic",
    "1", "2", "3", "4", "5", "6", "7", "8", "9", "10",
})

_TOKEN_RE = re.compile(r"[A-Za-zÀ-ÿ0-9]{3,}")


@dataclass
class ChartVideo:
    """A video observed on an official mostPopular chart."""

    video_id: str
    title: str
    channel_id: str | None
    channel_title: str | None
    region: str | None
    rank: int | None
    views: int
    published_at: str | None = None


@dataclass
class TopicGroup:
    """A cluster of chart videos sharing a cross-channel seed token."""

    label: str
    videos: list[ChartVideo] = field(default_factory=list)
    channel_count: int = 0
    region_count: int = 0
    total_views: int = 0
    confidence: str = "medium"
    trend_type: str = "other"


def _tokens(title: str) -> list[str]:
    """Lowercased meaningful tokens from a title."""
    return [tok.lower() for tok in _TOKEN_RE.findall(title) if tok.lower() not in _STOPWORDS]


def group_chart_videos(videos: list[ChartVideo], min_channels: int = 2) -> list[TopicGroup]:
    """Group videos by shared tokens that appear across >= min_channels channels.

    Each video is assigned to exactly one group: the one whose seed token is
    the *rarest* shared token in that video's title. Rarer tokens are more
    topically specific and produce cleaner group labels.
    """
    if not videos:
        return []

    # Map token -> set of channel_ids that use it.
    token_channels: dict[str, set[str]] = defaultdict(set)
    for video in videos:
        if not video.channel_id:
            continue
        for token in set(_tokens(video.title)):
            token_channels[token].add(video.channel_id)

    # Seed tokens: appear across >= min_channels distinct channels.
    seeds = {token for token, channels in token_channels.items() if len(channels) >= min_channels}
    if not seeds:
        return []

    # Assign each video to its rarest seed token (fewest total occurrences = most specific).
    token_freq = Counter()
    for video in videos:
        for token in _tokens(video.title):
            token_freq[token] += 1

    groups: dict[str, list[ChartVideo]] = defaultdict(list)
    for video in videos:
        video_tokens = _tokens(video.title)
        seed_hits = [t for t in set(video_tokens) if t in seeds]
        if not seed_hits:
            continue
        # Rarest seed wins; tie-break alphabetically for determinism.
        label = min(seed_hits, key=lambda t: (token_freq[t], t))
        groups[label].append(video)

    result: list[TopicGroup] = []
    for label, members in groups.items():
        channels = {v.channel_id for v in members if v.channel_id}
        regions = {v.region for v in members if v.region}
        result.append(TopicGroup(
            label=label,
            videos=members,
            channel_count=len(channels),
            region_count=len(regions),
            total_views=sum(v.views for v in members),
        ))

    # Strongest evidence first: more channels, then more views.
    result.sort(key=lambda g: (g.channel_count, g.total_views), reverse=True)
    return result


def counter_labels(groups: list[TopicGroup]) -> dict[int, str]:
    """Deterministic fallback labels: the seed token, title-cased."""
    return {index: group.label.title() for index, group in enumerate(groups)}


def build_naming_prompt(groups: list[TopicGroup]) -> str:
    """Build a Gemini prompt to name each group from its evidence titles."""
    lines = [
        "You are a trend analyst. For each group of YouTube video titles below,",
        "produce a concise, specific topic label (2-6 words) and a kind category.",
        "Kinds: person, event, product, place, concept, entertainment, sports,",
        "tech, politics, other. If a group has no clear topic, return null.",
        "",
        "Respond as strict JSON: an array of objects with fields",
        '"group" (integer index), "topic" (string|null), "kind" (string|null).',
        "",
    ]
    for index, group in enumerate(groups):
        titles = [v.title for v in group.videos[:12]]
        lines.append(f"Group {index}:")
        for title in titles:
            lines.append(f"  - {title}")
        lines.append("")
    return "\n".join(lines)


def parse_naming_response(text: str, expected_count: int) -> dict[int, dict]:
    """Parse Gemini JSON naming response into {index: {topic, kind}}.

    Only entries with a non-null topic are kept. Malformed JSON returns {}.
    """
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(data, list):
        return {}
    named: dict[int, dict] = {}
    for entry in data:
        if not isinstance(entry, dict):
            continue
        index = entry.get("group")
        topic = entry.get("topic")
        kind = entry.get("kind")
        if not isinstance(index, int) or index < 0 or index >= expected_count:
            continue
        if not topic or not isinstance(topic, str):
            continue
        named[index] = {"topic": topic.strip(), "kind": kind if isinstance(kind, str) else "other"}
    return named


# ---------------------------------------------------------------------------
# LLM-first extraction (primary path — mirrors ViralEngine groq_topic_client)
# ---------------------------------------------------------------------------

def build_extraction_prompt(videos: list[ChartVideo], region: str | None, max_topics: int = 20) -> str:
    """Build a Gemini prompt that extracts trending topics from ALL titles at once.

    The LLM reads every title, identifies semantic topics, and returns which
    video indices belong to each topic. This is the ViralEngine approach: the
    LLM does both grouping AND naming in a single call.
    """
    region_label = region or "global"
    lines = [
        f"You are analyzing YouTube trending content from region: {region_label}",
        "",
        "Below are titles from the top trending YouTube videos right now,",
        "each prefixed with its index number.",
        "Identify the main TOPICS that are trending based on these titles.",
        "",
        "A topic is:",
        '- A news event people are reacting to (e.g. "trump tariff", "earthquake", "election results")',
        '- A sports event or moment (e.g. "Euro 2024 semifinal", "Messi transfer", "Lakers trade")',
        '- A viral video format/template (e.g. "get ready with me", "day in my life", "things I regret buying")',
        "- A cultural moment or meme (e.g. specific meme format spreading)",
        "- A product or entertainment release going viral (e.g. specific movie, game, product launch)",
        "",
        "DO NOT include:",
        '- Generic topics like "funny", "viral", "fyp", "entertainment"',
        '- Person names alone (e.g. "Messi", "Yamal") — use the EVENT instead (e.g. "Messi Inter Miami debut", "Yamal Euro goal")',
        "- Topics with fewer than 2 videos",
        "- Hashtags alone without context",
        "",
        f"Return ONLY a JSON array with max {max_topics} topics.",
        "Format exactly like this example:",
        "[",
        '  {"topic": "Euro 2024 semifinal", "kind": "sports", "confidence": "high", "trend_type": "news_event", "video_indices": [0, 3, 7, 12]},',
        '  {"topic": "Messi Inter Miami debut", "kind": "sports", "confidence": "medium", "trend_type": "sports", "video_indices": [1, 5, 9]}',
        "]",
        "",
        "confidence values: high, medium, low",
        "trend_type values: news_event, sports, video_template, cultural_moment, product_trend, entertainment, other",
        "",
        "The video_indices must reference the index numbers from the list below.",
        "Return ONLY the JSON array. No explanation. No markdown.",
        "",
        "VIDEO TITLES:",
    ]
    for index, video in enumerate(videos):
        lines.append(f"{index}. {video.title}")
    return "\n".join(lines)


def parse_extraction_response(text: str, videos: list[ChartVideo]) -> list[TopicGroup]:
    """Parse Gemini extraction response into TopicGroups.

    The LLM returns topics with video_indices. We build TopicGroups from those
    indices. Malformed JSON returns [].
    """
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, list):
        return []

    groups: list[TopicGroup] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        topic = entry.get("topic")
        indices = entry.get("video_indices")
        kind = entry.get("kind", "other")
        confidence = entry.get("confidence", "medium")
        trend_type = entry.get("trend_type", "other")
        if not topic or not isinstance(topic, str) or not isinstance(indices, list):
            continue
        members: list[ChartVideo] = []
        channels: set[str] = set()
        regions: set[str] = set()
        for idx in indices:
            if not isinstance(idx, int) or idx < 0 or idx >= len(videos):
                continue
            video = videos[idx]
            members.append(video)
            if video.channel_id:
                channels.add(video.channel_id)
            if video.region:
                regions.add(video.region)
        if len(members) < 2:
            continue
        groups.append(TopicGroup(
            label=topic.strip(),
            videos=members,
            channel_count=len(channels),
            region_count=len(regions),
            total_views=sum(v.views for v in members),
            confidence=confidence if isinstance(confidence, str) else "medium",
            trend_type=trend_type if isinstance(trend_type, str) else "other",
        ))

    groups.sort(key=lambda g: (g.channel_count, g.total_views), reverse=True)
    return groups