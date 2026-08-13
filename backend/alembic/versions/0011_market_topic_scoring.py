"""Add auditable observed momentum to Market Topics."""

from alembic import op
import sqlalchemy as sa


revision = "0011_market_topic_scoring"
down_revision = "0010_market_topics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("market_topics", sa.Column("observed_velocity_per_hour", sa.Float(), nullable=False, server_default="0"))
    op.add_column("market_topics", sa.Column("acceleration", sa.Float(), nullable=True))
    op.add_column("market_topics", sa.Column("trend_score", sa.Float(), nullable=False, server_default="0"))
    op.add_column("market_topics", sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_market_topics_status_score", "market_topics", ["status", "trend_score"])
    op.create_table(
        "market_topic_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("market_topic_id", sa.Integer(), sa.ForeignKey("market_topics.id", ondelete="CASCADE"), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("observed_views", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("observed_velocity_per_hour", sa.Float(), nullable=False, server_default="0"),
        sa.Column("acceleration", sa.Float(), nullable=True),
        sa.Column("member_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("channel_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("new_member_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("trend_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("scoring_version", sa.String(64), nullable=False, server_default="market-topic-v1"),
    )
    op.create_index("ix_market_topic_snapshots_topic_observed", "market_topic_snapshots", ["market_topic_id", "observed_at"])


def downgrade() -> None:
    op.drop_index("ix_market_topic_snapshots_topic_observed", table_name="market_topic_snapshots")
    op.drop_table("market_topic_snapshots")
    op.drop_index("ix_market_topics_status_score", table_name="market_topics")
    op.drop_column("market_topics", "last_observed_at")
    op.drop_column("market_topics", "trend_score")
    op.drop_column("market_topics", "acceleration")
    op.drop_column("market_topics", "observed_velocity_per_hour")
