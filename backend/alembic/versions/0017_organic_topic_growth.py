"""Separate organic topic growth from newly added evidence coverage."""

from alembic import op
import sqlalchemy as sa


revision = "0017_organic_growth"
down_revision = "0016_topic_quality"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("market_ranked_topics", sa.Column("organic_velocity_per_hour", sa.Float(), nullable=False, server_default="0"))
    op.add_column("market_ranked_topic_snapshots", sa.Column("organic_velocity_per_hour", sa.Float(), nullable=False, server_default="0"))
    op.add_column("market_ranked_topic_snapshots", sa.Column("organic_measurement_ready", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("market_ranked_topic_snapshots", "organic_measurement_ready")
    op.drop_column("market_ranked_topic_snapshots", "organic_velocity_per_hour")
    op.drop_column("market_ranked_topics", "organic_velocity_per_hour")
