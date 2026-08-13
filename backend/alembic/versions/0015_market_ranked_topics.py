"""Persist ranked, public-facing Market Topics."""
from alembic import op
import sqlalchemy as sa

revision = "0015_ranked_topics"
down_revision = "0014_market_metadata_labels"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "market_ranked_topics",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("topic_key", sa.String(255), nullable=False, unique=True),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("topic_type", sa.String(64), nullable=False, server_default="other"),
        sa.Column("status", sa.String(32), nullable=False, server_default="WATCHING"),
        sa.Column("scope", sa.String(32), nullable=False, server_default="official_chart"),
        sa.Column("category_key", sa.String(32)),
        sa.Column("semantic_confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("member_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("channel_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("region_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("observed_views", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("observed_velocity_per_hour", sa.Float(), nullable=False, server_default="0"),
        sa.Column("acceleration", sa.Float()),
        sa.Column("trend_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("last_observed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_market_ranked_topics_status_score", "market_ranked_topics", ["status", "trend_score"])
    op.create_index("ix_market_ranked_topics_scope_seen", "market_ranked_topics", ["scope", "last_observed_at"])
    op.create_table(
        "market_ranked_topic_memberships",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("market_ranked_topic_id", sa.Integer(), sa.ForeignKey("market_ranked_topics.id", ondelete="CASCADE"), nullable=False),
        sa.Column("market_video_id", sa.Integer(), sa.ForeignKey("market_videos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("evidence_role", sa.String(32), nullable=False, server_default="topic_evidence"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.UniqueConstraint("market_ranked_topic_id", "market_video_id", name="uq_market_ranked_topic_video"),
    )
    op.create_table(
        "market_ranked_topic_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("market_ranked_topic_id", sa.Integer(), sa.ForeignKey("market_ranked_topics.id", ondelete="CASCADE"), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("observed_views", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("observed_velocity_per_hour", sa.Float(), nullable=False, server_default="0"),
        sa.Column("acceleration", sa.Float()),
        sa.Column("member_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("channel_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("region_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("trend_score", sa.Float(), nullable=False, server_default="0"),
    )
    op.create_index("ix_market_ranked_snapshots_topic_time", "market_ranked_topic_snapshots", ["market_ranked_topic_id", "observed_at"])


def downgrade() -> None:
    op.drop_index("ix_market_ranked_snapshots_topic_time", table_name="market_ranked_topic_snapshots")
    op.drop_table("market_ranked_topic_snapshots")
    op.drop_table("market_ranked_topic_memberships")
    op.drop_index("ix_market_ranked_topics_scope_seen", table_name="market_ranked_topics")
    op.drop_index("ix_market_ranked_topics_status_score", table_name="market_ranked_topics")
    op.drop_table("market_ranked_topics")
