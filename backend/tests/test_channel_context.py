import unittest

from app.services.channel_context import classify_channel


class ChannelContextTests(unittest.TestCase):
    def test_whale_is_context_not_a_filter(self) -> None:
        status, confidence, _ = classify_channel(2_000_000, [20_000, 50_000, 100_000])
        self.assertEqual(status, "WHALE")
        self.assertGreater(confidence, 0.5)

    def test_consistent_million_view_history_is_proven_winner(self) -> None:
        status, confidence, _ = classify_channel(25_000, [2_000_000, 4_000_000, 7_000_000, 2_300_000, 6_300_000])
        self.assertEqual(status, "PROVEN_WINNER")
        self.assertGreater(confidence, 0.5)

    def test_underdog_requires_two_public_evidence_sources(self) -> None:
        self.assertEqual(classify_channel(500, [200, 400, 700, 1_000, 1_500])[0], "UNDERDOG")
        self.assertEqual(classify_channel(None, [200, 400, 700, 1_000, 1_500])[0], "UNKNOWN")
