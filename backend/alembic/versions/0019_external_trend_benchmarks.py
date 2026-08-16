"""Add manual external trend benchmark evidence."""

from alembic import op
import sqlalchemy as sa


revision = "0019_external_trend_benchmarks"
down_revision = "0018_content_truth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "external_trend_benchmarks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("observed_on", sa.DateTime(timezone=True), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("region", sa.String(length=16)),
        sa.Column("category", sa.String(length=64)),
        sa.Column("source_rank", sa.Integer()),
        sa.Column("source_url", sa.String(length=1024)),
        sa.Column("note", sa.Text()),
        sa.Column("matched_ranked_topic_id", sa.Integer(), sa.ForeignKey("market_ranked_topics.id", ondelete="SET NULL")),
        sa.Column("match_status", sa.String(length=32), nullable=False, server_default="PENDING"),
        sa.Column("match_confidence", sa.Float()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_external_benchmark_day_source", "external_trend_benchmarks", ["observed_on", "source"])


def downgrade() -> None:
    op.drop_index("ix_external_benchmark_day_source", table_name="external_trend_benchmarks")
    op.drop_table("external_trend_benchmarks")
