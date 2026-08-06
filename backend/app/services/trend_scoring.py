"""Versioned scoring and lifecycle rules for observed topic clusters."""

from __future__ import annotations

import math

from app.config import settings

SCORING_VERSION = "topic-v1"


def trend_score(*, member_count: int, channel_count: int, observed_velocity: float, acceleration: float | None, new_member_count: int, duplicate_weight: float) -> float:
    """Return a bounded observed-momentum score; it is never a global popularity claim."""
    evidence = min(1.0, member_count / 5)
    diversity = min(1.0, channel_count / 4)
    velocity = min(1.0, math.log1p(max(0.0, observed_velocity)) / math.log1p(settings.topic_velocity_reference_per_hour))
    growth = min(1.0, new_member_count / 3)
    acceleration_component = max(0.0, min(1.0, (acceleration or 0.0) / 1.0))
    duplicate_penalty = max(0.0, min(0.5, duplicate_weight))
    return round(100 * max(0.0, 0.25 * evidence + 0.30 * diversity + 0.25 * velocity + 0.10 * growth + 0.10 * acceleration_component - duplicate_penalty), 2)


def lifecycle_status(*, member_count: int, channel_count: int, score: float, acceleration: float | None, stale_hours: float) -> str:
    if member_count < settings.topic_trends_min_emerging_videos or channel_count < settings.topic_trends_min_emerging_channels:
        return "PRIVATE_CANDIDATE"
    if stale_hours >= settings.topic_cooling_after_hours:
        return "COOLING"
    if member_count >= settings.topic_confirmed_min_videos and channel_count >= settings.topic_confirmed_min_channels and score >= 60:
        return "CONFIRMED"
    if member_count >= settings.topic_accelerating_min_videos and channel_count >= settings.topic_accelerating_min_channels and score >= 35 and (acceleration or 0) >= 0:
        return "ACCELERATING"
    return "EMERGING"
