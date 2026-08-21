"""Add composite indexes used by scoped Topic Pool reads.

Revision ID: 0021_topic_pool_performance
Revises: 0020_market_source_runs
"""

from alembic import op


revision = "0021_topic_pool_performance"
down_revision = "0020_market_source_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_market_videos_shorts_published",
        "market_videos",
        ["shorts_status", "published_at"],
    )
    op.create_index(
        "ix_market_observations_video_time",
        "market_video_observations",
        ["market_video_id", "observed_at"],
    )
    op.create_index(
        "ix_market_topics_status_seen",
        "market_topics",
        ["status", "last_observed_at"],
    )
    op.create_index(
        "ix_market_memberships_topic_video",
        "market_topic_memberships",
        ["market_topic_id", "market_video_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_market_memberships_topic_video", table_name="market_topic_memberships")
    op.drop_index("ix_market_topics_status_seen", table_name="market_topics")
    op.drop_index("ix_market_observations_video_time", table_name="market_video_observations")
    op.drop_index("ix_market_videos_shorts_published", table_name="market_videos")
