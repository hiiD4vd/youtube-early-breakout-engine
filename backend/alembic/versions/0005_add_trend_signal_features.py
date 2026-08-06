"""add auditable trend signal features

Revision ID: 0005_trend_features
Revises: 0004_topic_trends
Create Date: 2026-08-06
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005_trend_features"
down_revision = "0004_topic_trends"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trend_signal_features",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("youtube_snipe_id", sa.Integer(), sa.ForeignKey("youtube_snipes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("feature_model", sa.String(length=64), nullable=False, server_default="lexical-v1"),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("sparse_vector", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("source_provenance", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("youtube_snipe_id"),
    )
    op.create_index("ix_trend_signal_features_model", "trend_signal_features", ["feature_model"])


def downgrade() -> None:
    op.drop_index("ix_trend_signal_features_model", table_name="trend_signal_features")
    op.drop_table("trend_signal_features")
