"""Isolated raw evidence store for broad Market Trends coverage."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
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
