from __future__ import annotations

from datetime import datetime


SCORING_VERSION = "phase-a-v1"


def age_bucket(published_at: datetime, now: datetime) -> str:
    hours = (now - published_at).total_seconds() / 3600
    if hours < 2:
        return "0-2h"
    if hours < 6:
        return "2-6h"
    if hours < 12:
        return "6-12h"
    if hours < 24:
        return "12-24h"
    return "excluded-over-24h"


def _hours(start: datetime, end: datetime) -> float:
    return max((end - start).total_seconds() / 3600, 1 / 60)


def interval_velocities(seed_views: int, seeded_at: datetime, snapshots: list[dict]) -> list[float]:
    """Return comparable view/hour intervals, beginning at seed -> snapshot 1."""
    values: list[float] = []
    previous_views, previous_at = seed_views, seeded_at
    for snapshot in snapshots:
        observed_at = datetime.fromisoformat(snapshot["observed_at"])
        view_count = int(snapshot["view_count"])
        values.append(max(0, view_count - previous_views) / _hours(previous_at, observed_at))
        previous_views, previous_at = view_count, observed_at
    return values


def age_threshold_multiplier(bucket: str) -> float:
    """A temporary guardrail until Phase B percentile calibration is available."""
    return {"0-2h": 0.75, "2-6h": 1.0, "6-12h": 2.0, "12-24h": 4.0}.get(bucket, float("inf"))


def score_tier(
    seed_views: int,
    seeded_at: datetime,
    snapshots: list[dict],
    age_bucket_name: str,
    early_min: float,
    rising_min: float,
    breakout_min: float,
    relative_percentile: float | None = None,
    relative_enabled: bool = False,
    relative_early: float = 80.0,
    relative_rising: float = 92.0,
    relative_breakout: float = 97.0,
) -> tuple[str, float, float, list[float]]:
    """Classify evidence. A single follow-up is deliberately never public."""
    intervals = interval_velocities(seed_views, seeded_at, snapshots)
    if len(intervals) < 2:
        return "WATCH", 0.0, 0.0, intervals

    latest, previous = intervals[-1], intervals[-2]
    acceleration = latest - previous
    multiplier = age_threshold_multiplier(age_bucket_name)
    if latest <= 0 or latest < previous * 0.5:
        return "COOLED", 0.0, acceleration, intervals
    if latest < early_min * multiplier or previous <= 0:
        return "WATCH", 0.0, acceleration, intervals
    if relative_enabled and (relative_percentile is None or relative_percentile < relative_early):
        return "WATCH", 0.0, acceleration, intervals
    # EARLY requires two positive intervals and no material slowdown.
    if len(intervals) < 3:
        return "EARLY", 0.0, acceleration, intervals
    if latest >= breakout_min * multiplier and acceleration > 0 and (not relative_enabled or relative_percentile >= relative_breakout):
        return "BREAKOUT", 0.0, acceleration, intervals
    if latest >= rising_min * multiplier and acceleration > 0 and (not relative_enabled or relative_percentile >= relative_rising):
        return "RISING", 0.0, acceleration, intervals
    return "EARLY", 0.0, acceleration, intervals
