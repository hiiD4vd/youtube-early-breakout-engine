import unittest

from app.api.router import (
    _rank_scoped_topic_pool,
    _topic_pool_list_item,
    _topic_pool_response,
)


def candidate(topic_id: str, *, growth: int, velocity: float, channels: int) -> dict:
    return {
        "id": topic_id,
        "label": topic_id,
        "period_growth_views": growth,
        "organic_velocity_per_hour": velocity,
        "channel_count": channels,
        "member_count": channels,
        "_region_count": 1,
        "_fresh_ratio": 1,
        "members": [],
    }


class TopicPoolPerformanceTests(unittest.TestCase):
    def test_scope_ranking_prefers_real_growth(self) -> None:
        ranked = _rank_scoped_topic_pool([
            candidate("moving", growth=100_000, velocity=10_000, channels=3),
            candidate("large-but-flat", growth=0, velocity=0, channels=8),
        ])
        self.assertEqual(ranked[0]["id"], "moving")
        self.assertGreater(ranked[0]["ranking_score"], ranked[1]["ranking_score"])

    def test_list_payload_drops_detail_only_fields(self) -> None:
        item = candidate("compact", growth=1, velocity=1, channels=2)
        item.update({
            "members": [{"video_id": str(index), "thumbnail_url": "x", "current_view_count": index, "transcript": "large"} for index in range(9)],
            "snapshots": [{"observed_velocity_per_hour": index, "large_debug_blob": "x" * 100} for index in range(12)],
            "model_metadata": {"large": "x" * 1_000},
        })
        compact = _topic_pool_list_item(item)
        self.assertEqual(len(compact["members"]), 5)
        self.assertEqual(len(compact["snapshots"]), 8)
        self.assertNotIn("model_metadata", compact)
        self.assertNotIn("transcript", compact["members"][0])

    def test_search_filters_without_changing_saved_score(self) -> None:
        core = {
            "items": [
                {"id": "minecraft", "label": "Minecraft builds", "ranking_score": 42.5},
                {"id": "football", "label": "Football skills", "ranking_score": 80.0},
            ],
            "diagnostics": {},
            "methodology": "test",
        }
        response = _topic_pool_response(
            core,
            scope="shorts",
            period="7d",
            search="minecraft",
            offset=0,
            limit=25,
            cache_state="hit",
        )
        self.assertEqual(response["total_items"], 1)
        self.assertEqual(response["items"][0]["ranking_score"], 42.5)


if __name__ == "__main__":
    unittest.main()
