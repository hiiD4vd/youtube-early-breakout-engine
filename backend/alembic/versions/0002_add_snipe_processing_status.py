"""add youtube snipe processing status

Revision ID: 0002_snipe_status
Revises: 0001_youtube_snipes
Create Date: 2026-08-03
"""

from alembic import op
import sqlalchemy as sa

revision = "0002_snipe_status"
down_revision = "0001_youtube_snipes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("youtube_snipes", sa.Column("processing_status", sa.String(length=32), nullable=False, server_default="signal_detected"))
    op.add_column("youtube_snipes", sa.Column("media_status", sa.String(length=32), nullable=False, server_default="pending"))
    op.add_column("youtube_snipes", sa.Column("enrichment_status", sa.String(length=32), nullable=False, server_default="pending"))
    op.add_column("youtube_snipes", sa.Column("processing_reason", sa.String(length=300), nullable=True))


def downgrade() -> None:
    op.drop_column("youtube_snipes", "processing_reason")
    op.drop_column("youtube_snipes", "enrichment_status")
    op.drop_column("youtube_snipes", "media_status")
    op.drop_column("youtube_snipes", "processing_status")
