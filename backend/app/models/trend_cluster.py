"""Permanent, auditable entities for post-signal Topic Trends."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TrendCluster(Base):
    __tablename__ = "trend_clusters"
    __table_args__ = (
        Index("ix_trend_clusters_status_score", "status", "trend_score"),
        Index("ix_trend_clusters_last_observed", "last_observed_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    public_slug: Mapped[str] = mapped_column(String(96), unique=True, nullable=False, index=True)
    label: Mapped[str | None] = mapped_column(String(255))
    label_confidence: Mapped[float | None] = mapped_column(Float)
    niche: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PRIVATE_CANDIDATE")
    trend_score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    semantic_cohesion: Mapped[float | None] = mapped_column(Float)
    observed_views: Mapped[int] = mapped_column(nullable=False, default=0)
    observed_velocity_per_hour: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    acceleration: Mapped[float | None] = mapped_column(Float)
    member_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    channel_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_member_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cooling_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    region_mix: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    channel_context_mix: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    evidence_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    cluster_reason: Mapped[str | None] = mapped_column(Text)
    model_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class TrendMembership(Base):
    __tablename__ = "trend_memberships"
    __table_args__ = (
        UniqueConstraint("cluster_id", "youtube_snipe_id", name="uq_trend_membership_cluster_snipe"),
        Index("ix_trend_memberships_snipe", "youtube_snipe_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cluster_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("trend_clusters.id", ondelete="CASCADE"), nullable=False)
    youtube_snipe_id: Mapped[int] = mapped_column(ForeignKey("youtube_snipes.id", ondelete="CASCADE"), nullable=False)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    similarity_score: Mapped[float | None] = mapped_column(Float)
    membership_state: Mapped[str] = mapped_column(String(32), nullable=False, default="PROVISIONAL")
    is_reupload_suspect: Mapped[bool] = mapped_column(nullable=False, default=False)
    is_same_channel_duplicate: Mapped[bool] = mapped_column(nullable=False, default=False)
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1)
    feature_evidence: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class TrendSnapshot(Base):
    __tablename__ = "trend_snapshots"
    __table_args__ = (Index("ix_trend_snapshots_cluster_observed", "cluster_id", "observed_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cluster_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("trend_clusters.id", ondelete="CASCADE"), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    observed_views: Mapped[int] = mapped_column(nullable=False, default=0)
    observed_velocity_per_hour: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    median_velocity_per_hour: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    acceleration: Mapped[float | None] = mapped_column(Float)
    member_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    channel_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    new_member_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    new_channel_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    trend_score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    scoring_version: Mapped[str] = mapped_column(String(64), nullable=False, default="topic-v1")
    reason: Mapped[str | None] = mapped_column(String(300))
