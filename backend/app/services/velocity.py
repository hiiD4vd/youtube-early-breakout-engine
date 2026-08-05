from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class VelocitySignal:
    elapsed_hours: float
    view_delta: int
    velocity_per_hour: float
    passes: bool


def calculate_velocity(seed_views: int, current_views: int, seeded_at: datetime, now: datetime, min_delta: int, min_velocity: float) -> VelocitySignal:
    elapsed_hours = max((now - seeded_at).total_seconds() / 3600, 1 / 60)
    view_delta = max(current_views - seed_views, 0)
    velocity = view_delta / elapsed_hours
    return VelocitySignal(elapsed_hours, view_delta, velocity, view_delta >= min_delta and velocity >= min_velocity)
