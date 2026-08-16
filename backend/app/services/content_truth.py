"""Conservative publication gate for title-to-content verification."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class ContentTruthSummary:
    status: str
    required_audits: int
    audited_count: int
    aligned_count: int
    mismatch_count: int
    inconclusive_count: int
    error_count: int

    def as_dict(self) -> dict[str, int | str]:
        return {
            "status": self.status,
            "required_audits": self.required_audits,
            "audited_count": self.audited_count,
            "aligned_count": self.aligned_count,
            "mismatch_count": self.mismatch_count,
            "inconclusive_count": self.inconclusive_count,
            "error_count": self.error_count,
            "rule_version": "content-truth-v1",
        }


def summarize_content_truth(audits: Iterable[object], member_count: int, *, required: bool) -> ContentTruthSummary:
    """Turn individual factual audits into a cautious topic-level decision.

    Two independent mismatches are needed before a topic is quarantined.
    This avoids one noisy caption or thumbnail suppressing a real topic, while
    preventing a copied title on many unrelated clips from becoming an event.
    """
    statuses = [str(getattr(audit, "status", "")).upper() for audit in audits]
    aligned = statuses.count("ALIGNED")
    mismatches = statuses.count("MISMATCH")
    inconclusive = statuses.count("INCONCLUSIVE")
    errors = statuses.count("ERROR")
    audited = aligned + mismatches + inconclusive
    needed = min(2, max(1, member_count)) if required else 0

    if required and mismatches >= needed and mismatches > aligned:
        outcome = "QUARANTINED_METADATA_MISMATCH"
    elif not required or aligned >= needed:
        outcome = "VALIDATED"
    else:
        outcome = "AWAITING_CONTENT_VALIDATION"
    return ContentTruthSummary(
        status=outcome,
        required_audits=needed,
        audited_count=audited,
        aligned_count=aligned,
        mismatch_count=mismatches,
        inconclusive_count=inconclusive,
        error_count=errors,
    )
