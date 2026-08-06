"""create auditable topic trends tables

Revision ID: 0004_topic_trends
Revises: 0003_signal_tier
Create Date: 2026-08-06
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004_topic_trends"
down_revision = "0003_signal_tier"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trend_clusters",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("public_slug", sa.String(length=96), nullable=False),
        sa.Column("label", sa.String(length=255)),
        sa.Column("label_confidence", sa.Float()),
        sa.Column("niche", sa.String(length=128)),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="PRIVATE_CANDIDATE"),
        sa.Column("trend_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("semantic_cohesion", sa.Float()),
        sa.Column("observed_views", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("observed_velocity_per_hour", sa.Float(), nullable=False, server_default="0"),
        sa.Column("acceleration", sa.Float()),
        sa.Column("member_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("channel_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_detected_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("last_observed_at", sa.DateTime(timezone=True)),
        sa.Column("last_member_at", sa.DateTime(timezone=True)),
        sa.Column("cooling_at", sa.DateTime(timezone=True)),
        sa.Column("region_mix", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("channel_context_mix", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("evidence_summary", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("cluster_reason", sa.Text()),
        sa.Column("model_metadata", postgresql.JSONB(astext_type=sa.Text())),
        sa.UniqueConstraint("public_slug"),
    )
    op.create_index("ix_trend_clusters_status_score", "trend_clusters", ["status", "trend_score"])
    op.create_index("ix_trend_clusters_last_observed", "trend_clusters", ["last_observed_at"])

    op.create_table(
        "trend_memberships",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cluster_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("trend_clusters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("youtube_snipe_id", sa.Integer(), sa.ForeignKey("youtube_snipes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("similarity_score", sa.Float()),
        sa.Column("membership_state", sa.String(length=32), nullable=False, server_default="PROVISIONAL"),
        sa.Column("is_reupload_suspect", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_same_channel_duplicate", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("weight", sa.Float(), nullable=False, server_default="1"),
        sa.Column("feature_evidence", postgresql.JSONB(astext_type=sa.Text())),
        sa.UniqueConstraint("cluster_id", "youtube_snipe_id", name="uq_trend_membership_cluster_snipe"),
    )
    op.create_index("ix_trend_memberships_snipe", "trend_memberships", ["youtube_snipe_id"])

    op.create_table(
        "trend_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cluster_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("trend_clusters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("observed_views", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("observed_velocity_per_hour", sa.Float(), nullable=False, server_default="0"),
        sa.Column("median_velocity_per_hour", sa.Float(), nullable=False, server_default="0"),
        sa.Column("acceleration", sa.Float()),
        sa.Column("member_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("channel_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("new_member_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("new_channel_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("trend_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("scoring_version", sa.String(length=64), nullable=False, server_default="topic-v1"),
        sa.Column("reason", sa.String(length=300)),
    )
    op.create_index("ix_trend_snapshots_cluster_observed", "trend_snapshots", ["cluster_id", "observed_at"])


def downgrade() -> None:
    op.drop_index("ix_trend_snapshots_cluster_observed", table_name="trend_snapshots")
    op.drop_table("trend_snapshots")
    op.drop_index("ix_trend_memberships_snipe", table_name="trend_memberships")
    op.drop_table("trend_memberships")
    op.drop_index("ix_trend_clusters_last_observed", table_name="trend_clusters")
    op.drop_index("ix_trend_clusters_status_score", table_name="trend_clusters")
    op.drop_table("trend_clusters")
