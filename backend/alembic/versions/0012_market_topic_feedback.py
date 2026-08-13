"""Add immutable Market Topic review feedback."""

from alembic import op
import sqlalchemy as sa

revision = "0012_market_topic_feedback"
down_revision = "0011_market_topic_scoring"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "market_topic_feedback",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("market_topic_id", sa.Integer(), sa.ForeignKey("market_topics.id", ondelete="CASCADE"), nullable=False),
        sa.Column("reviewer", sa.String(96), nullable=False, server_default="local_reviewer"),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("feature_model", sa.String(64), nullable=True),
        sa.Column("snapshot_count_at_review", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_market_topic_feedback_topic_created", "market_topic_feedback", ["market_topic_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_market_topic_feedback_topic_created", table_name="market_topic_feedback")
    op.drop_table("market_topic_feedback")
