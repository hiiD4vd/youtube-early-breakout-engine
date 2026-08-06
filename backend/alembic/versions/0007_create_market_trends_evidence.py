"""create market trends evidence

Revision ID: 0007_market_trends_evidence
Revises: 0006_add_topic_cluster_feedback
Create Date: 2026-08-06
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0007_market_trends_evidence"
down_revision = "0006_add_topic_cluster_feedback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "market_videos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("video_id", sa.String(length=32), nullable=False, unique=True),
        sa.Column("channel_id", sa.String(length=64), nullable=True),
        sa.Column("channel_title", sa.String(length=255), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("video_url", sa.String(length=512), nullable=False),
        sa.Column("thumbnail_url", sa.String(length=1024), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("category_id", sa.String(length=16), nullable=True),
        sa.Column("duration_iso8601", sa.String(length=64), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("source_provenance", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_index("ix_market_videos_video_id", "market_videos", ["video_id"])
    op.create_index("ix_market_videos_published_at", "market_videos", ["published_at"])
    op.create_table(
        "market_video_observations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("market_video_id", sa.Integer(), sa.ForeignKey("market_videos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("source_lane", sa.String(length=48), nullable=False),
        sa.Column("region", sa.String(length=8), nullable=True),
        sa.Column("language", sa.String(length=16), nullable=True),
        sa.Column("category_id", sa.String(length=16), nullable=True),
        sa.Column("view_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("like_count", sa.BigInteger(), nullable=True),
        sa.Column("comment_count", sa.BigInteger(), nullable=True),
        sa.Column("source_rank", sa.Integer(), nullable=True),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.UniqueConstraint("market_video_id", "observed_at", "source_lane", "region", "category_id", name="uq_market_observation_source_time"),
    )
    op.create_index("ix_market_observations_observed_at", "market_video_observations", ["observed_at"])
    op.create_index("ix_market_observations_lane_region", "market_video_observations", ["source_lane", "region"])


def downgrade() -> None:
    op.drop_index("ix_market_observations_lane_region", table_name="market_video_observations")
    op.drop_index("ix_market_observations_observed_at", table_name="market_video_observations")
    op.drop_table("market_video_observations")
    op.drop_index("ix_market_videos_published_at", table_name="market_videos")
    op.drop_index("ix_market_videos_video_id", table_name="market_videos")
    op.drop_table("market_videos")
