import unittest

from app.services.trend_features import cosine_similarity, provisional_label


class TrendFeatureTests(unittest.TestCase):
    def test_shared_weighted_terms_are_more_similar(self) -> None:
        related = cosine_similarity({"balloon": 0.8, "prank": 0.6}, {"balloon": 0.7, "prank": 0.7})
        unrelated = cosine_similarity({"balloon": 0.8, "prank": 0.6}, {"football": 0.8, "penalty": 0.6})
        self.assertGreater(related, 0.8)
        self.assertEqual(unrelated, 0.0)

    def test_provisional_label_uses_strongest_terms(self) -> None:
        self.assertEqual(provisional_label({"balloon": 0.8, "prank": 0.6, "challenge": 0.4}), "Balloon · Prank · Challenge")
