"""Fast rule checks; runnable directly without pytest."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.config import settings
from app.api.router import _early_topic_eligibility
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


def _cluster(*, status="PRIVATE_CANDIDATE", phase="FRESH", age=3.0, members=2, channels=2, metadata=None, confidence=.85, label="Topik baru"):
    return SimpleNamespace(
        status=status,
        label=label,
        label_confidence=confidence,
        evidence_summary={
            "early_phase": phase,
            "lifecycle_age_hours": age,
            "early_member_count": members,
            "early_channel_count": channels,
        },
        model_metadata=metadata or {},
    )


def test_private_semantic_candidate_is_valid_for_early_board():
    candidate = _cluster(metadata={"source": "market_semantic_provider", "semantic_grouping": True, "followable": True})
    assert _early_topic_eligibility(candidate) == (True, [])


def test_cooling_status_does_not_hide_a_still_live_early_candidate():
    candidate = _cluster(status="COOLING", phase="RISING", age=30, metadata={"early_topic_named": True})
    assert _early_topic_eligibility(candidate) == (True, [])


def test_expired_or_single_channel_candidate_is_not_eligible():
    candidate = _cluster(phase="EXPIRED", age=80, channels=1, metadata={"early_topic_named": True})
    eligible, reasons = _early_topic_eligibility(candidate)
    assert not eligible
    assert "outside_live_phase" in reasons
    assert "outside_lifecycle_window" in reasons
    assert "insufficient_early_channels" in reasons


if __name__ == "__main__":
    test_low_view_entry_is_eligible_regardless_of_channel_context()
    test_late_or_high_view_entry_is_not_early_evidence()
    test_private_semantic_candidate_is_valid_for_early_board()
    test_cooling_status_does_not_hide_a_still_live_early_candidate()
    test_expired_or_single_channel_candidate_is_not_eligible()
    print("early topic rule self-check OK")
