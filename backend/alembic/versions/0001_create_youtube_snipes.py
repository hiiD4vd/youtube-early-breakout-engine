"""create youtube snipes table

Revision ID: 0001_youtube_snipes
Revises:
Create Date: 2026-08-03
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_youtube_snipes"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "youtube_snipes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("video_id", sa.String(length=32), nullable=False),
        sa.Column("channel_id", sa.String(length=64), nullable=False),
        sa.Column("channel_title", sa.String(length=255)),
        sa.Column("title", sa.Text()),
        sa.Column("video_url", sa.String(length=512), nullable=False),
        sa.Column("thumbnail_url", sa.String(length=1024)),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("initial_view_count", sa.BigInteger(), nullable=False),
        sa.Column("current_view_count", sa.BigInteger(), nullable=False),
        sa.Column("velocity_per_hour", sa.Float(), nullable=False),
        sa.Column("breakout_score", sa.Float(), nullable=False),
        sa.Column("peak_timestamp_seconds", sa.Float()),
        sa.Column("peak_frame_path", sa.String(length=1024)),
        sa.Column("transcript", sa.Text()),
        sa.Column("niche", sa.String(length=128)),
        sa.Column("visual_facts", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("ai_analysis", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("raw_metadata", postgresql.JSONB(astext_type=sa.Text())),
        sa.UniqueConstraint("video_id"),
    )
    op.create_index("ix_youtube_snipes_video_id", "youtube_snipes", ["video_id"])
    op.create_index("ix_youtube_snipes_channel_id", "youtube_snipes", ["channel_id"])
    op.create_index("ix_youtube_snipes_detected_at", "youtube_snipes", ["detected_at"])
    op.create_index("ix_youtube_snipes_niche", "youtube_snipes", ["niche"])


def downgrade() -> None:
    op.drop_index("ix_youtube_snipes_niche", table_name="youtube_snipes")
    op.drop_index("ix_youtube_snipes_detected_at", table_name="youtube_snipes")
    op.drop_index("ix_youtube_snipes_channel_id", table_name="youtube_snipes")
    op.drop_index("ix_youtube_snipes_video_id", table_name="youtube_snipes")
    op.drop_table("youtube_snipes")
