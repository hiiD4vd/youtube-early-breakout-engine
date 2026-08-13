"""Add auditable metadata burst candidates for Market Trends."""
from alembic import op
import sqlalchemy as sa

revision = "0013_market_metadata_bursts"
down_revision = "0012_market_topic_feedback"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table("market_metadata_trends", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("signal_key", sa.String(255), unique=True, nullable=False), sa.Column("label", sa.String(255), nullable=False), sa.Column("signal_type", sa.String(32), nullable=False), sa.Column("status", sa.String(32), nullable=False, server_default="WATCHING"), sa.Column("member_count", sa.Integer(), nullable=False, server_default="0"), sa.Column("channel_count", sa.Integer(), nullable=False, server_default="0"), sa.Column("region_count", sa.Integer(), nullable=False, server_default="0"), sa.Column("fresh_ratio", sa.Float(), nullable=False, server_default="0"), sa.Column("burst_score", sa.Float(), nullable=False, server_default="0"), sa.Column("last_observed_at", sa.DateTime(timezone=True)))
    op.create_index("ix_market_metadata_trends_status_score", "market_metadata_trends", ["status", "burst_score"])
    op.create_table("market_metadata_trend_memberships", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("market_metadata_trend_id", sa.Integer(), sa.ForeignKey("market_metadata_trends.id", ondelete="CASCADE"), nullable=False), sa.Column("market_video_id", sa.Integer(), sa.ForeignKey("market_videos.id", ondelete="CASCADE"), nullable=False), sa.Column("matched_term", sa.String(255), nullable=False), sa.UniqueConstraint("market_metadata_trend_id", "market_video_id", name="uq_market_metadata_trend_video"))
    op.create_table("market_metadata_trend_snapshots", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("market_metadata_trend_id", sa.Integer(), sa.ForeignKey("market_metadata_trends.id", ondelete="CASCADE"), nullable=False), sa.Column("observed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False), sa.Column("member_count", sa.Integer(), nullable=False, server_default="0"), sa.Column("channel_count", sa.Integer(), nullable=False, server_default="0"), sa.Column("region_count", sa.Integer(), nullable=False, server_default="0"), sa.Column("fresh_ratio", sa.Float(), nullable=False, server_default="0"), sa.Column("burst_score", sa.Float(), nullable=False, server_default="0"))
    op.create_index("ix_market_metadata_snapshots_trend_time", "market_metadata_trend_snapshots", ["market_metadata_trend_id", "observed_at"])

def downgrade() -> None:
    op.drop_index("ix_market_metadata_snapshots_trend_time", table_name="market_metadata_trend_snapshots"); op.drop_table("market_metadata_trend_snapshots"); op.drop_table("market_metadata_trend_memberships"); op.drop_index("ix_market_metadata_trends_status_score", table_name="market_metadata_trends"); op.drop_table("market_metadata_trends")
