from datetime import UTC, datetime, timedelta
import unittest

from app.services.youtube_client import YoutubeAnonymousClient


class YoutubeClientParsingTests(unittest.TestCase):
    def test_view_count_parser_handles_compact_and_raw_values(self) -> None:
        self.assertEqual(YoutubeAnonymousClient._parse_view_count("1.2M views"), 1_200_000)
        self.assertEqual(YoutubeAnonymousClient._parse_view_count("987 views"), 987)
        self.assertEqual(YoutubeAnonymousClient._parse_view_count("12,345"), 12_345)

    def test_fresh_complete_candidate_becomes_seed(self) -> None:
        seed, reason = YoutubeAnonymousClient._to_seed(
            {
                "video_id": "abcdefghijk",
                "channel_id": "UCabcdefghijk1234567890",
                "view_count": 10,
                "published_at": datetime.now(UTC) - timedelta(hours=2),
            }
        )
        self.assertIsNone(reason)
        self.assertIsNotNone(seed)
        self.assertEqual(seed.seed_view_count, 10)

    def test_old_candidate_is_dropped(self) -> None:
        seed, reason = YoutubeAnonymousClient._to_seed(
            {
                "video_id": "abcdefghijk",
                "channel_id": "UCabcdefghijk1234567890",
                "view_count": 10,
                "published_at": datetime.now(UTC) - timedelta(hours=25),
            }
        )
        self.assertIsNone(seed)
        self.assertEqual(reason, "too_old")
