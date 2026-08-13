"""add market video features"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision = "0009_market_video_features"
down_revision = "0008_market_shorts_status"
branch_labels = None
depends_on = None
def upgrade() -> None:
    op.create_table("market_video_features", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("market_video_id", sa.Integer(), sa.ForeignKey("market_videos.id", ondelete="CASCADE"), unique=True, nullable=False), sa.Column("feature_model", sa.String(length=64), nullable=False), sa.Column("normalized_text", sa.Text(), nullable=False), sa.Column("sparse_vector", postgresql.JSONB(astext_type=sa.Text())), sa.Column("topic_hint", sa.String(length=255)), sa.Column("confidence", sa.Float(), nullable=False, server_default="0"), sa.Column("provenance", postgresql.JSONB(astext_type=sa.Text())), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False))
    op.create_index("ix_market_video_features_model", "market_video_features", ["feature_model"])
def downgrade() -> None:
    op.drop_index("ix_market_video_features_model", table_name="market_video_features"); op.drop_table("market_video_features")
