"""add market shorts status

Revision ID: 0008_market_shorts_status
Revises: 0007_market_trends_evidence
Create Date: 2026-08-06
"""

from alembic import op
import sqlalchemy as sa

revision = "0008_market_shorts_status"
down_revision = "0007_market_trends_evidence"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column("market_videos", sa.Column("shorts_status", sa.String(length=32), nullable=False, server_default="UNVERIFIED"))

def downgrade() -> None:
    op.drop_column("market_videos", "shorts_status")
