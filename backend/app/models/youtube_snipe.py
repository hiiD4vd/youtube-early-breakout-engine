from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Float, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class YoutubeSnipe(Base):
    """A permanently retained Short that passed the breakout pipeline."""

    __tablename__ = "youtube_snipes"
    __table_args__ = (
        Index("ix_youtube_snipes_detected_at", "detected_at"),
        Index("ix_youtube_snipes_niche", "niche"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    video_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    channel_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    channel_title: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str | None] = mapped_column(Text)
    video_url: Mapped[str] = mapped_column(String(512), nullable=False)
    thumbnail_url: Mapped[str | None] = mapped_column(String(1024))

    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    initial_view_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    current_view_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    velocity_per_hour: Mapped[float] = mapped_column(Float, nullable=False)
    breakout_score: Mapped[float] = mapped_column(Float, nullable=False)

    peak_timestamp_seconds: Mapped[float | None] = mapped_column(Float)
    peak_frame_path: Mapped[str | None] = mapped_column(String(1024))
    transcript: Mapped[str | None] = mapped_column(Text)
    niche: Mapped[str | None] = mapped_column(String(128))
    visual_facts: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ai_analysis: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    raw_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    processing_status: Mapped[str] = mapped_column(String(32), nullable=False, default="signal_detected")
    media_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    enrichment_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    processing_reason: Mapped[str | None] = mapped_column(String(300))
    signal_tier: Mapped[str] = mapped_column(String(16), nullable=False, default="WATCH")
    signal_score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
