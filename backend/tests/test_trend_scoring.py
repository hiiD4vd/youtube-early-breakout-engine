import unittest

from app.services.trend_scoring import lifecycle_status, trend_score


class TrendScoringTests(unittest.TestCase):
    def test_one_video_remains_private(self) -> None:
        score = trend_score(member_count=1, channel_count=1, observed_velocity=100_000, acceleration=1, new_member_count=1, duplicate_weight=0)
        self.assertEqual(lifecycle_status(member_count=1, channel_count=1, score=score, acceleration=1, stale_hours=0), "PRIVATE_CANDIDATE")

    def test_independent_pair_becomes_emerging(self) -> None:
        score = trend_score(member_count=2, channel_count=2, observed_velocity=5_000, acceleration=0.2, new_member_count=2, duplicate_weight=0)
        self.assertEqual(lifecycle_status(member_count=2, channel_count=2, score=score, acceleration=0.2, stale_hours=0), "EMERGING")

    def test_stale_independent_cluster_cools(self) -> None:
        self.assertEqual(lifecycle_status(member_count=3, channel_count=2, score=70, acceleration=0, stale_hours=7), "COOLING")
