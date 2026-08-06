"""add topic cluster feedback

Revision ID: 0006_add_topic_cluster_feedback
Revises: 0005_trend_features
Create Date: 2026-08-06
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0006_add_topic_cluster_feedback"
down_revision = "0005_trend_features"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "topic_cluster_feedback",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cluster_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("trend_clusters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("reviewer", sa.String(length=96), nullable=False, server_default="local_reviewer"),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("feature_model", sa.String(length=64), nullable=True),
        sa.Column("snapshot_count_at_review", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_topic_cluster_feedback_cluster_created", "topic_cluster_feedback", ["cluster_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_topic_cluster_feedback_cluster_created", table_name="topic_cluster_feedback")
    op.drop_table("topic_cluster_feedback")
