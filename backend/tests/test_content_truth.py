from types import SimpleNamespace

from app.services.content_truth import summarize_content_truth


def test_two_independent_mismatches_quarantine_a_claim():
    result = summarize_content_truth(
        [SimpleNamespace(status="MISMATCH"), SimpleNamespace(status="MISMATCH")],
        member_count=4,
        required=True,
    )
    assert result.status == "QUARANTINED_METADATA_MISMATCH"
    assert result.mismatch_count == 2


def test_two_aligned_videos_validate_a_claim():
    result = summarize_content_truth(
        [SimpleNamespace(status="ALIGNED"), SimpleNamespace(status="ALIGNED")],
        member_count=3,
        required=True,
    )
    assert result.status == "VALIDATED"
    assert result.aligned_count == 2


def test_inconclusive_evidence_fails_closed_without_a_false_accusation():
    result = summarize_content_truth(
        [SimpleNamespace(status="INCONCLUSIVE"), SimpleNamespace(status="ERROR")],
        member_count=3,
        required=True,
    )
    assert result.status == "AWAITING_CONTENT_VALIDATION"
    assert result.mismatch_count == 0
