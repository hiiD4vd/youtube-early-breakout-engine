"""Add auditable per-surface Market discovery cohorts."""

from alembic import op
import sqlalchemy as sa


revision = "0020_market_source_runs"
down_revision = "0019_external_trend_benchmarks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "market_source_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_lane", sa.String(length=48), nullable=False),
        sa.Column("region", sa.String(length=8)),
        sa.Column("language", sa.String(length=16)),
        sa.Column("cohort_key", sa.String(length=96)),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="RUNNING"),
        sa.Column("candidates_seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("accepted_shorts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unique_shorts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duplicate_shorts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fresh_0_24h", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fresh_24_72h", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rejected_not_shorts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_type", sa.String(length=128)),
        sa.Column("details", sa.dialects.postgresql.JSONB()),
    )
    op.create_index("ix_market_source_runs_started_lane_region", "market_source_runs", ["started_at", "source_lane", "region"])


def downgrade() -> None:
    op.drop_index("ix_market_source_runs_started_lane_region", table_name="market_source_runs")
    op.drop_table("market_source_runs")
