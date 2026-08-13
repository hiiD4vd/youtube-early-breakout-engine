"""Add semantic labels for metadata-burst clusters."""
from alembic import op
import sqlalchemy as sa

revision = "0014_market_metadata_labels"
down_revision = "0013_market_metadata_bursts"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column("market_metadata_trends", sa.Column("semantic_label", sa.String(255)))
    op.add_column("market_metadata_trends", sa.Column("semantic_summary", sa.Text()))
    op.add_column("market_metadata_trends", sa.Column("semantic_confidence", sa.Float()))
    op.add_column("market_metadata_trends", sa.Column("semantic_status", sa.String(32), nullable=False, server_default="AI_PENDING"))
    op.add_column("market_metadata_trends", sa.Column("followable", sa.Boolean(), nullable=False, server_default=sa.false()))

def downgrade() -> None:
    op.drop_column("market_metadata_trends", "followable"); op.drop_column("market_metadata_trends", "semantic_status"); op.drop_column("market_metadata_trends", "semantic_confidence"); op.drop_column("market_metadata_trends", "semantic_summary"); op.drop_column("market_metadata_trends", "semantic_label")
