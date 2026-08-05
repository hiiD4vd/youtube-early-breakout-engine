"""add signal tier

Revision ID: 0003_signal_tier
Revises: 0002_snipe_status
Create Date: 2026-08-03
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_signal_tier"
down_revision = "0002_snipe_status"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column("youtube_snipes", sa.Column("signal_tier", sa.String(length=16), nullable=False, server_default="WATCH"))
    op.add_column("youtube_snipes", sa.Column("signal_score", sa.Float(), nullable=False, server_default="0"))

def downgrade() -> None:
    op.drop_column("youtube_snipes", "signal_score")
    op.drop_column("youtube_snipes", "signal_tier")
