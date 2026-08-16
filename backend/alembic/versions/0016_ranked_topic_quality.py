"""Add canonical semantic identity and quality audit fields for ranked topics."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0016_topic_quality"
down_revision = "0015_ranked_topics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("market_ranked_topics", sa.Column("entity_signature", sa.String(255)))
    op.add_column("market_ranked_topics", sa.Column("context_signature", sa.String(255)))
    op.add_column("market_ranked_topics", sa.Column("semantic_summary", sa.Text()))
    op.add_column("market_ranked_topics", sa.Column("source_mix", postgresql.JSONB(astext_type=sa.Text())))
    op.add_column("market_ranked_topics", sa.Column("quality_flags", postgresql.JSONB(astext_type=sa.Text())))
    op.create_index("ix_market_ranked_topics_entity_signature", "market_ranked_topics", ["entity_signature"])
    op.create_index("ix_market_ranked_topics_context_signature", "market_ranked_topics", ["context_signature"])
    op.add_column("market_ranked_topic_snapshots", sa.Column("new_member_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("market_ranked_topic_snapshots", sa.Column("source_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("market_ranked_topic_snapshots", sa.Column("history_ready", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        "market_ranked_topic_reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("market_ranked_topic_id", sa.Integer(), sa.ForeignKey("market_ranked_topics.id", ondelete="CASCADE"), nullable=False),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("reviewer", sa.String(96), nullable=False, server_default="local_reviewer"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_market_ranked_topic_reviews_topic_created", "market_ranked_topic_reviews", ["market_ranked_topic_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_market_ranked_topic_reviews_topic_created", table_name="market_ranked_topic_reviews")
    op.drop_table("market_ranked_topic_reviews")
    op.drop_column("market_ranked_topic_snapshots", "history_ready")
    op.drop_column("market_ranked_topic_snapshots", "source_count")
    op.drop_column("market_ranked_topic_snapshots", "new_member_count")
    op.drop_index("ix_market_ranked_topics_context_signature", table_name="market_ranked_topics")
    op.drop_index("ix_market_ranked_topics_entity_signature", table_name="market_ranked_topics")
    op.drop_column("market_ranked_topics", "quality_flags")
    op.drop_column("market_ranked_topics", "source_mix")
    op.drop_column("market_ranked_topics", "semantic_summary")
    op.drop_column("market_ranked_topics", "context_signature")
    op.drop_column("market_ranked_topics", "entity_signature")
