"""Bounded factual verification for suspicious Market Topic evidence."""
from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta

from sqlalchemy import desc, func, select

from app.config import settings
from app.database import SessionLocal
from app.models.market_trends import MarketContentTruthAudit, MarketRankedTopic, MarketRankedTopicMembership, MarketVideo, MarketVideoFeature
from app.services.market_semantic_client import MarketSemanticClient
from app.services.content_truth import summarize_content_truth
from app.services.seed_store import SeedStore
from app.services.transcript import fetch_transcript
from app.tasks.celery_app import celery_app

LOCK = "ycgc:youtube:lock:market-content-truth"
PUBLIC_OR_PENDING_STATUSES = ("AWAITING_CONTENT_VALIDATION", "EMERGING", "ACCELERATING", "CONFIRMED")


def _claim_topic(topic: MarketRankedTopic) -> bool:
    flags = topic.quality_flags or {}
    # Some older semantic passes labelled a concrete news/event claim as an
    # entity conversation because it contained a named person/team. The
    # explicit shared-event flag is the safer source of truth for this audit.
    return (
        flags.get("topic_kind") == "event"
        or bool(flags.get("event_context_verified"))
        or (flags.get("topic_kind") == "entity_conversation" and bool(flags.get("entity_verified")))
    )


def _apply_content_truth_gates(db) -> int:
    """Immediately withhold disproved event claims, without rebuilding clusters."""
    withheld = 0
    topics = db.scalars(
        select(MarketRankedTopic).where(
            MarketRankedTopic.status.in_((*PUBLIC_OR_PENDING_STATUSES, "QUARANTINED_METADATA_MISMATCH"))
        )
    ).all()
    for topic in topics:
        if not _claim_topic(topic):
            continue
        member_ids = db.scalars(
            select(MarketRankedTopicMembership.market_video_id).where(
                MarketRankedTopicMembership.market_ranked_topic_id == topic.id,
                MarketRankedTopicMembership.evidence_role == "active_evidence",
            )
        ).all()
        if not member_ids:
            continue
        audits = db.scalars(
            select(MarketContentTruthAudit).where(MarketContentTruthAudit.market_video_id.in_(member_ids))
        ).all()
        truth = summarize_content_truth(audits, len(member_ids), required=True)
        flags = topic.quality_flags or {}
        topic.quality_flags = {**flags, "content_truth": truth.as_dict()}
        if truth.status in {"AWAITING_CONTENT_VALIDATION", "QUARANTINED_METADATA_MISMATCH"}:
            topic.status = truth.status
            withheld += 1
    return withheld


@celery_app.task(name="app.tasks.market_content_truth_tasks.audit_market_content_truth")
def audit_market_content_truth() -> dict:
    """Audit a small title-claim batch without treating metadata as content proof.

    No raw Short is deleted. Gateway/caption failures are recorded as
    INCONCLUSIVE or ERROR so the publication gate fails closed rather than
    guessing that the title is correct.
    """
    store = SeedStore()
    if not store.client.set(LOCK, "1", nx=True, ex=900):
        return {"status": "locked"}
    scanned = aligned = mismatch = inconclusive = errors = withheld = 0
    try:
        with SessionLocal() as db:
            topics = db.scalars(
                select(MarketRankedTopic)
                .where(MarketRankedTopic.status.in_(PUBLIC_OR_PENDING_STATUSES))
                .order_by(desc(MarketRankedTopic.trend_score))
                .limit(80)
            ).all()
            candidates: list[MarketVideo] = []
            seen: set[int] = set()
            for topic in topics:
                if not _claim_topic(topic):
                    continue
                members = db.scalars(
                    select(MarketVideo)
                    .join(MarketRankedTopicMembership, MarketRankedTopicMembership.market_video_id == MarketVideo.id)
                    .where(
                        MarketRankedTopicMembership.market_ranked_topic_id == topic.id,
                        MarketRankedTopicMembership.evidence_role == "active_evidence",
                        MarketVideo.shorts_status == "VERIFIED_SHORTS",
                    )
                ).all()
                title_counts = Counter((video.title or "").strip().casefold() for video in members)
                for video in sorted(members, key=lambda item: title_counts[(item.title or "").strip().casefold()], reverse=True):
                    if video.id in seen:
                        continue
                    audit = db.scalar(select(MarketContentTruthAudit).where(MarketContentTruthAudit.market_video_id == video.id))
                    # A completed audit is stable. INCONCLUSIVE may be retried
                    # in a later run only after fresh source evidence arrives.
                    if audit and audit.status in {"ALIGNED", "MISMATCH"}:
                        continue
                    candidates.append(video)
                    seen.add(video.id)
                    if len(candidates) >= settings.market_content_truth_batch_size:
                        break
                if len(candidates) >= settings.market_content_truth_batch_size:
                    break

            # Audit repeated long title claims even before the topic builder
            # has decided that they deserve a public row.  This catches the
            # exact SEO-hijacking pattern: content farms paste one breaking
            # headline across unrelated Shorts.  Repetition only nominates a
            # batch for factual review; it is never treated as proof that the
            # claim is real.
            remaining = max(0, settings.market_content_truth_batch_size - len(candidates))
            if remaining:
                fresh_cutoff = datetime.now(UTC) - timedelta(hours=settings.market_topic_active_video_max_age_hours)
                repeated_titles = db.execute(
                    select(MarketVideo.title, func.count(MarketVideo.id).label("copies"))
                    .where(
                        MarketVideo.shorts_status == "VERIFIED_SHORTS",
                        MarketVideo.published_at >= fresh_cutoff,
                        func.length(func.trim(MarketVideo.title)) >= 24,
                    )
                    .group_by(MarketVideo.title)
                    .having(func.count(MarketVideo.id) >= 3)
                    .order_by(desc("copies"))
                    .limit(12)
                ).all()
                for title, _copies in repeated_titles:
                    if not title or len(candidates) >= settings.market_content_truth_batch_size:
                        break
                    copies = db.scalars(
                        select(MarketVideo)
                        .where(MarketVideo.title == title, MarketVideo.shorts_status == "VERIFIED_SHORTS")
                        .order_by(desc(MarketVideo.last_seen_at))
                        .limit(3)
                    ).all()
                    for video in copies:
                        if video.id in seen:
                            continue
                        audit = db.scalar(select(MarketContentTruthAudit).where(MarketContentTruthAudit.market_video_id == video.id))
                        if audit and audit.status in {"ALIGNED", "MISMATCH"}:
                            continue
                        candidates.append(video)
                        seen.add(video.id)
                        if len(candidates) >= settings.market_content_truth_batch_size:
                            break

            client = MarketSemanticClient()
            for video in candidates:
                scanned += 1
                transcript = None
                transcript_error = None
                try:
                    transcript = fetch_transcript(video.video_url)
                except Exception as exc:  # keep a failed downloader auditable
                    transcript_error = type(exc).__name__
                audit = db.scalar(select(MarketContentTruthAudit).where(MarketContentTruthAudit.market_video_id == video.id))
                if audit is None:
                    audit = MarketContentTruthAudit(market_video_id=video.id)
                    db.add(audit)
                try:
                    facts = client.analyze_content_truth(
                        title=video.title or "",
                        transcript=transcript,
                        thumbnail_url=video.thumbnail_url,
                    )
                    audit.status = facts.verdict
                    audit.alignment_score = facts.alignment_score
                    audit.confidence = facts.confidence
                    audit.title_claim = facts.title_claim
                    audit.content_summary = facts.content_summary
                    audit.visual_summary = facts.visual_summary
                    audit.transcript_excerpt = transcript[:6000] if transcript else None
                    audit.mismatch_reason = facts.mismatch_reason
                    audit.evidence = {
                        "video_id": video.video_id,
                        "transcript_available": bool(transcript),
                        "thumbnail_checked": bool(video.thumbnail_url),
                        "transcript_error": transcript_error,
                        "content_entities": facts.content_entities,
                        "content_semantic": {
                            "topic_label": facts.content_topic_label,
                            "topic_type": facts.content_topic_type,
                            "event_context": facts.content_event_context,
                            "confidence": facts.confidence,
                            "source": "transcript_thumbnail_audit",
                        },
                    }
                    # Content semantics are stored beside the existing title
                    # fingerprint. The original metadata is preserved, but a
                    # later clustering pass can now prefer what the Short is
                    # actually about when it has an ALIGNED audit.
                    feature = db.scalar(select(MarketVideoFeature).where(MarketVideoFeature.market_video_id == video.id))
                    if feature is not None:
                        feature.provenance = {
                            **(feature.provenance or {}),
                            "content_semantic": (audit.evidence or {}).get("content_semantic"),
                            "content_semantic_status": facts.verdict,
                        }
                    audit.auditor_model = settings.market_topic_review_model or settings.market_semantic_model
                    audit.reviewed_at = datetime.now(UTC)
                    if facts.verdict == "ALIGNED":
                        aligned += 1
                    elif facts.verdict == "MISMATCH":
                        mismatch += 1
                    else:
                        inconclusive += 1
                except Exception as exc:
                    audit.status = "ERROR"
                    audit.alignment_score = None
                    audit.confidence = None
                    audit.mismatch_reason = "Content verification provider unavailable; not treated as a match."
                    audit.evidence = {"video_id": video.video_id, "transcript_available": bool(transcript), "thumbnail_checked": bool(video.thumbnail_url), "transcript_error": transcript_error, "error_type": type(exc).__name__}
                    audit.auditor_model = settings.market_topic_review_model or settings.market_semantic_model
                    audit.reviewed_at = datetime.now(UTC)
                    errors += 1
            withheld = _apply_content_truth_gates(db)
            db.commit()
        store.set_status(
            market_content_truth_last_run_at=datetime.now(UTC).isoformat(),
            market_content_truth_scanned=scanned,
            market_content_truth_aligned=aligned,
            market_content_truth_mismatch=mismatch,
            market_content_truth_inconclusive=inconclusive,
            market_content_truth_errors=errors,
            market_content_truth_withheld=withheld,
        )
        return {"status": "ok", "scanned": scanned, "aligned": aligned, "mismatch": mismatch, "inconclusive": inconclusive, "errors": errors, "withheld": withheld}
    finally:
        store.client.delete(LOCK)
