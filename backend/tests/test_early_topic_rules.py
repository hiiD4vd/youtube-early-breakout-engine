"""Fast rule checks; runnable directly without pytest."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.config import settings
from app.tasks.youtube_trend_tasks import _is_early_evidence


def _signal(views: int):
    return SimpleNamespace(current_view_count=views)


def test_low_view_entry_is_eligible_regardless_of_channel_context():
    membership = SimpleNamespace(feature_evidence={"entry_view_count": 20_000, "entry_age_hours": 2.0})
    assert _is_early_evidence(membership, _signal(900_000))


def test_late_or_high_view_entry_is_not_early_evidence():
    high = SimpleNamespace(feature_evidence={"entry_view_count": settings.early_topic_max_entry_views + 1, "entry_age_hours": 1.0})
    late = SimpleNamespace(feature_evidence={"entry_view_count": 20_000, "entry_age_hours": settings.early_topic_max_entry_age_hours + 1})
    assert not _is_early_evidence(high, _signal(1))
    assert not _is_early_evidence(late, _signal(1))


if __name__ == "__main__":
    test_low_view_entry_is_eligible_regardless_of_channel_context()
    test_late_or_high_view_entry_is_not_early_evidence()
    print("early topic rule self-check OK")
