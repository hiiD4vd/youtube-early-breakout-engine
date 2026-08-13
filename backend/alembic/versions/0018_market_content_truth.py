"""Add auditable title-to-content truth checks for Market Shorts."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0018_content_truth"
down_revision = "0017_organic_growth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "market_content_truth_audits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("market_video_id", sa.Integer(), sa.ForeignKey("market_videos.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="PENDING"),
        sa.Column("alignment_score", sa.Float()),
        sa.Column("confidence", sa.Float()),
        sa.Column("title_claim", sa.Text()),
        sa.Column("content_summary", sa.Text()),
        sa.Column("transcript_excerpt", sa.Text()),
        sa.Column("visual_summary", sa.Text()),
        sa.Column("mismatch_reason", sa.Text()),
        sa.Column("evidence", postgresql.JSONB()),
        sa.Column("auditor_model", sa.String(length=96)),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_market_content_truth_status_seen", "market_content_truth_audits", ["status", "reviewed_at"])


def downgrade() -> None:
    op.drop_index("ix_market_content_truth_status_seen", table_name="market_content_truth_audits")
    op.drop_table("market_content_truth_audits")
