from datetime import UTC, datetime, timedelta
import unittest

from app.services.signal_scoring import age_bucket, score_tier


def _snapshots(seeded_at: datetime, counts: list[int]) -> list[dict]:
    return [
        {"observed_at": (seeded_at + timedelta(hours=index + 1)).isoformat(), "view_count": count}
        for index, count in enumerate(counts)
    ]


class SignalScoringTests(unittest.TestCase):
    def test_one_snapshot_never_becomes_public_early_signal(self) -> None:
        now = datetime.now(UTC)
        tier, _, _, _ = score_tier(100, now, _snapshots(now, [5_000]), "2-6h", 250, 500, 1_000)
        self.assertEqual(tier, "WATCH")

    def test_two_positive_intervals_can_become_early(self) -> None:
        now = datetime.now(UTC)
        tier, _, _, _ = score_tier(100, now, _snapshots(now, [500, 950]), "2-6h", 250, 500, 1_000)
        self.assertEqual(tier, "EARLY")

    def test_declining_growth_is_cooled(self) -> None:
        now = datetime.now(UTC)
        tier, _, _, _ = score_tier(100, now, _snapshots(now, [1_100, 1_250]), "2-6h", 250, 500, 1_000)
        self.assertEqual(tier, "COOLED")

    def test_late_age_bucket_requires_stronger_velocity(self) -> None:
        now = datetime.now(UTC)
        tier, _, _, _ = score_tier(100, now, _snapshots(now, [700, 1_300]), "12-24h", 250, 500, 1_000)
        self.assertEqual(tier, "WATCH")
        self.assertEqual(age_bucket(now - timedelta(hours=24, seconds=1), now), "excluded-over-24h")
