"""Isolated raw evidence store for broad Market Trends coverage."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MarketVideo(Base):
    """A public video observed by a broad market source, not an Early Breakout."""

    __tablename__ = "market_videos"
    __table_args__ = (Index("ix_market_videos_published_at", "published_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    video_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    channel_id: Mapped[str | None] = mapped_column(String(64), index=True)
    channel_title: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    video_url: Mapped[str] = mapped_column(String(512), nullable=False)
    thumbnail_url: Mapped[str | None] = mapped_column(String(1024))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    category_id: Mapped[str | None] = mapped_column(String(16))
    duration_iso8601: Mapped[str | None] = mapped_column(String(64))
    shorts_status: Mapped[str] = mapped_column(String(32), nullable=False, default="UNVERIFIED")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    source_provenance: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class MarketVideoObservation(Base):
    """Append-only observed statistics with lane and locale provenance."""

    __tablename__ = "market_video_observations"
    __table_args__ = (
        UniqueConstraint("market_video_id", "observed_at", "source_lane", "region", "category_id", name="uq_market_observation_source_time"),
        Index("ix_market_observations_observed_at", "observed_at"),
        Index("ix_market_observations_lane_region", "source_lane", "region"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_video_id: Mapped[int] = mapped_column(ForeignKey("market_videos.id", ondelete="CASCADE"), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    source_lane: Mapped[str] = mapped_column(String(48), nullable=False)
    region: Mapped[str | None] = mapped_column(String(8))
    language: Mapped[str | None] = mapped_column(String(16))
    category_id: Mapped[str | None] = mapped_column(String(16))
    view_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    like_count: Mapped[int | None] = mapped_column(BigInteger)
    comment_count: Mapped[int | None] = mapped_column(BigInteger)
    source_rank: Mapped[int | None] = mapped_column(Integer)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class MarketSourceRun(Base):
    """One auditable collection cohort from a Market discovery surface.

    A source run is deliberately separate from a video observation.  This
    lets the product answer the operational question that matters most for
    unbiased discovery: did a region/surface add new, fresh Shorts, or did it
    merely rediscover the same rows?  It never participates in topic ranking.
    """

    __tablename__ = "market_source_runs"
    __table_args__ = (
        Index("ix_market_source_runs_started_lane_region", "started_at", "source_lane", "region"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_lane: Mapped[str] = mapped_column(String(48), nullable=False)
    region: Mapped[str | None] = mapped_column(String(8))
    language: Mapped[str | None] = mapped_column(String(16))
    cohort_key: Mapped[str | None] = mapped_column(String(96))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="RUNNING")
    candidates_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    accepted_shorts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unique_shorts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duplicate_shorts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fresh_0_24h: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fresh_24_72h: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rejected_not_shorts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_type: Mapped[str | None] = mapped_column(String(128))
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class MarketVideoFeature(Base):
    __tablename__ = "market_video_features"
    __table_args__ = (Index("ix_market_video_features_model", "feature_model"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_video_id: Mapped[int] = mapped_column(ForeignKey("market_videos.id", ondelete="CASCADE"), unique=True, nullable=False)
    feature_model: Mapped[str] = mapped_column(String(64), nullable=False, default="lexical-v1")
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    sparse_vector: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    topic_hint: Mapped[str | None] = mapped_column(String(255))
    confidence: Mapped[float] = mapped_column(nullable=False, default=0)
    provenance: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class MarketContentTruthAudit(Base):
    """Auditable title-to-content verification for suspicious Market evidence.

    This is deliberately separate from discovery and topic features. A failed
    audit never deletes a Short; it only prevents poisoned metadata from being
    presented as an event/topic claim.
    """

    __tablename__ = "market_content_truth_audits"
    __table_args__ = (
        Index("ix_market_content_truth_status_seen", "status", "reviewed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_video_id: Mapped[int] = mapped_column(ForeignKey("market_videos.id", ondelete="CASCADE"), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    alignment_score: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    title_claim: Mapped[str | None] = mapped_column(Text)
    content_summary: Mapped[str | None] = mapped_column(Text)
    transcript_excerpt: Mapped[str | None] = mapped_column(Text)
    visual_summary: Mapped[str | None] = mapped_column(Text)
    mismatch_reason: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    auditor_model: Mapped[str | None] = mapped_column(String(96))
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class MarketTopic(Base):
    __tablename__ = "market_topics"
    __table_args__ = (Index("ix_market_topics_status_score", "status", "trend_score"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PRIVATE_CANDIDATE")
    member_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    channel_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    observed_views: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    observed_velocity_per_hour: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    acceleration: Mapped[float | None] = mapped_column(Float)
    trend_score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

class MarketTopicMembership(Base):
    __tablename__ = "market_topic_memberships"
    __table_args__ = (UniqueConstraint("market_video_id", name="uq_market_video_topic"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_topic_id: Mapped[int] = mapped_column(ForeignKey("market_topics.id", ondelete="CASCADE"), nullable=False)
    market_video_id: Mapped[int] = mapped_column(ForeignKey("market_videos.id", ondelete="CASCADE"), nullable=False)
    similarity_score: Mapped[float] = mapped_column(nullable=False, default=0)


class MarketTopicSnapshot(Base):
    """Auditable aggregate measurements for a broad Market Topic."""

    __tablename__ = "market_topic_snapshots"
    __table_args__ = (Index("ix_market_topic_snapshots_topic_observed", "market_topic_id", "observed_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_topic_id: Mapped[int] = mapped_column(ForeignKey("market_topics.id", ondelete="CASCADE"), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    observed_views: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    observed_velocity_per_hour: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    acceleration: Mapped[float | None] = mapped_column(Float)
    member_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    channel_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    new_member_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    trend_score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    scoring_version: Mapped[str] = mapped_column(String(64), nullable=False, default="market-topic-v1")


class MarketTopicFeedback(Base):
    """Immutable human review for Market Topic calibration only."""

    __tablename__ = "market_topic_feedback"
    __table_args__ = (Index("ix_market_topic_feedback_topic_created", "market_topic_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_topic_id: Mapped[int] = mapped_column(ForeignKey("market_topics.id", ondelete="CASCADE"), nullable=False)
    reviewer: Mapped[str] = mapped_column(String(96), nullable=False, default="local_reviewer")
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    feature_model: Mapped[str | None] = mapped_column(String(64))
    snapshot_count_at_review: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class MarketMetadataTrend(Base):
    """Observed metadata-burst candidate; separate from confirmed semantic topics."""

    __tablename__ = "market_metadata_trends"
    __table_args__ = (Index("ix_market_metadata_trends_status_score", "status", "burst_score"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    signal_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    signal_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="WATCHING")
    member_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    channel_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    region_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fresh_ratio: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    burst_score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    semantic_label: Mapped[str | None] = mapped_column(String(255))
    semantic_summary: Mapped[str | None] = mapped_column(Text)
    semantic_confidence: Mapped[float | None] = mapped_column(Float)
    semantic_status: Mapped[str] = mapped_column(String(32), nullable=False, default="AI_PENDING")
    followable: Mapped[bool] = mapped_column(nullable=False, default=False)
    last_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MarketMetadataTrendMembership(Base):
    __tablename__ = "market_metadata_trend_memberships"
    __table_args__ = (UniqueConstraint("market_metadata_trend_id", "market_video_id", name="uq_market_metadata_trend_video"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_metadata_trend_id: Mapped[int] = mapped_column(ForeignKey("market_metadata_trends.id", ondelete="CASCADE"), nullable=False)
    market_video_id: Mapped[int] = mapped_column(ForeignKey("market_videos.id", ondelete="CASCADE"), nullable=False)
    matched_term: Mapped[str] = mapped_column(String(255), nullable=False)


class MarketMetadataTrendSnapshot(Base):
    __tablename__ = "market_metadata_trend_snapshots"
    __table_args__ = (Index("ix_market_metadata_snapshots_trend_time", "market_metadata_trend_id", "observed_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_metadata_trend_id: Mapped[int] = mapped_column(ForeignKey("market_metadata_trends.id", ondelete="CASCADE"), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    member_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    channel_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    region_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fresh_ratio: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    burst_score: Mapped[float] = mapped_column(Float, nullable=False, default=0)


class MarketRankedTopic(Base):
    """A durable, public-facing topic built from auditable Market evidence."""

    __tablename__ = "market_ranked_topics"
    __table_args__ = (
        Index("ix_market_ranked_topics_status_score", "status", "trend_score"),
        Index("ix_market_ranked_topics_scope_seen", "scope", "last_observed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    topic_type: Mapped[str] = mapped_column(String(64), nullable=False, default="other")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="WATCHING")
    scope: Mapped[str] = mapped_column(String(32), nullable=False, default="official_chart")
    category_key: Mapped[str | None] = mapped_column(String(32), index=True)
    # Canonical semantic identity.  Labels may evolve as the AI sees more
    # evidence; identity must not, otherwise the same subject fragments into
    # several rows just because the wording changed.
    entity_signature: Mapped[str | None] = mapped_column(String(255), index=True)
    context_signature: Mapped[str | None] = mapped_column(String(255), index=True)
    semantic_summary: Mapped[str | None] = mapped_column(Text)
    source_mix: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    quality_flags: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    semantic_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    member_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    channel_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    region_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    observed_views: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    observed_velocity_per_hour: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    organic_velocity_per_hour: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    acceleration: Mapped[float | None] = mapped_column(Float)
    trend_score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MarketRankedTopicMembership(Base):
    __tablename__ = "market_ranked_topic_memberships"
    __table_args__ = (UniqueConstraint("market_ranked_topic_id", "market_video_id", name="uq_market_ranked_topic_video"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_ranked_topic_id: Mapped[int] = mapped_column(ForeignKey("market_ranked_topics.id", ondelete="CASCADE"), nullable=False)
    market_video_id: Mapped[int] = mapped_column(ForeignKey("market_videos.id", ondelete="CASCADE"), nullable=False)
    evidence_role: Mapped[str] = mapped_column(String(32), nullable=False, default="topic_evidence")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0)


class MarketRankedTopicSnapshot(Base):
    __tablename__ = "market_ranked_topic_snapshots"
    __table_args__ = (Index("ix_market_ranked_snapshots_topic_time", "market_ranked_topic_id", "observed_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_ranked_topic_id: Mapped[int] = mapped_column(ForeignKey("market_ranked_topics.id", ondelete="CASCADE"), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    observed_views: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    observed_velocity_per_hour: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    # Growth from evidence that was already in the topic at the previous scan.
    # This deliberately excludes the lifetime views of newly admitted videos.
    organic_velocity_per_hour: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    organic_measurement_ready: Mapped[bool] = mapped_column(nullable=False, default=False)
    acceleration: Mapped[float | None] = mapped_column(Float)
    member_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    channel_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    region_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    new_member_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    history_ready: Mapped[bool] = mapped_column(nullable=False, default=False)
    trend_score: Mapped[float] = mapped_column(Float, nullable=False, default=0)


class MarketRankedTopicReview(Base):
    """Human feedback used to calibrate publication rules, never to rewrite evidence."""

    __tablename__ = "market_ranked_topic_reviews"
    __table_args__ = (Index("ix_market_ranked_topic_reviews_topic_created", "market_ranked_topic_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_ranked_topic_id: Mapped[int] = mapped_column(ForeignKey("market_ranked_topics.id", ondelete="CASCADE"), nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    reviewer: Mapped[str] = mapped_column(String(96), nullable=False, default="local_reviewer")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ExternalTrendBenchmark(Base):
    """Manual same-day comparison evidence from another public trend surface.

    We intentionally store the analyst-observed label and source URL rather
    than scrape another platform or pretend its view count is comparable to
    YouTube. Matching to a local topic is a later, auditable judgement.
    """

    __tablename__ = "external_trend_benchmarks"
    __table_args__ = (
        Index("ix_external_benchmark_day_source", "observed_on", "source"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_on: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    region: Mapped[str | None] = mapped_column(String(16))
    category: Mapped[str | None] = mapped_column(String(64))
    source_rank: Mapped[int | None] = mapped_column(Integer)
    source_url: Mapped[str | None] = mapped_column(String(1024))
    note: Mapped[str | None] = mapped_column(Text)
    matched_ranked_topic_id: Mapped[int | None] = mapped_column(ForeignKey("market_ranked_topics.id", ondelete="SET NULL"))
    match_status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    match_confidence: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
