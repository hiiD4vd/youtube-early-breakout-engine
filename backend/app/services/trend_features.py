"""Auditable provisional representations for post-signal topic clustering."""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from typing import Any

from app.models.youtube_snipe import YoutubeSnipe

FEATURE_MODEL = "lexical-v1"
_TOKEN = re.compile(r"[^\W\d_]{3,}", re.UNICODE)
_STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "from", "you", "your", "short", "shorts", "shortvideo", "viral", "fyp", "video",
    "yang", "dan", "dari", "untuk", "ini", "itu", "dengan", "pada", "ada", "kamu", "saya", "jadi", "banget", "viral", "shorts",
}


def _tokens(value: str | None) -> list[str]:
    return [token.lower() for token in _TOKEN.findall(value or "") if token.lower() not in _STOPWORDS]


def build_feature_payload(snipe: YoutubeSnipe) -> dict[str, Any]:
    """Build a bounded weighted lexical vector with explicit source provenance."""
    ai = snipe.ai_analysis or {}
    facts = (snipe.visual_facts or {}).get("facts", [])
    sources = [
        ("niche", snipe.niche, 5.0),
        ("title", snipe.title, 2.0),
        ("transcript_summary", ai.get("transcript_summary"), 3.0),
        ("visual_facts", " ".join(item for item in facts if isinstance(item, str)), 2.0),
        ("transcript", (snipe.transcript or "")[:4_000], 1.0),
    ]
    weighted: Counter[str] = Counter()
    provenance: dict[str, int] = {}
    text_parts: list[str] = []
    for source, value, weight in sources:
        tokens = _tokens(value if isinstance(value, str) else None)
        if not tokens:
            continue
        provenance[source] = len(tokens)
        text_parts.extend(tokens)
        for token in tokens:
            weighted[token] += weight
    norm = math.sqrt(sum(value * value for value in weighted.values()))
    vector = {token: round(value / norm, 6) for token, value in weighted.most_common(160)} if norm else {}
    normalized_text = " ".join(text_parts[:1_000])
    content_hash = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
    confidence = min(1.0, round(0.15 * len(provenance) + (0.25 if snipe.niche else 0) + (0.2 if snipe.transcript else 0), 2))
    return {
        "feature_model": FEATURE_MODEL,
        "content_hash": content_hash,
        "normalized_text": normalized_text,
        "sparse_vector": vector,
        "source_provenance": provenance,
        "confidence": confidence,
    }


def cosine_similarity(left: dict[str, float] | None, right: dict[str, float] | None) -> float:
    if not left or not right:
        return 0.0
    # Vectors are L2-normalized before persistence; sparse dot product is cosine.
    if len(left) > len(right):
        left, right = right, left
    return round(sum(weight * float(right.get(token, 0)) for token, weight in left.items()), 4)


def provisional_label(vector: dict[str, float] | None) -> str:
    terms = [term.replace("_", " ") for term, _ in sorted((vector or {}).items(), key=lambda item: item[1], reverse=True)[:3]]
    return " · ".join(terms).title() if terms else "Unlabelled emerging topic"
