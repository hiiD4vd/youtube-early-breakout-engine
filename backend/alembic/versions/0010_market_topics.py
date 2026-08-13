from alembic import op
import sqlalchemy as sa
revision="0010_market_topics"; down_revision="0009_market_video_features"; branch_labels=None; depends_on=None
def upgrade():
 op.create_table("market_topics",sa.Column("id",sa.Integer(),primary_key=True),sa.Column("label",sa.String(255),nullable=False),sa.Column("status",sa.String(32),nullable=False),sa.Column("member_count",sa.Integer(),nullable=False,server_default="0"),sa.Column("channel_count",sa.Integer(),nullable=False,server_default="0"),sa.Column("observed_views",sa.BigInteger(),nullable=False,server_default="0"),sa.Column("created_at",sa.DateTime(timezone=True),server_default=sa.text("now()"),nullable=False))
 op.create_table("market_topic_memberships",sa.Column("id",sa.Integer(),primary_key=True),sa.Column("market_topic_id",sa.Integer(),sa.ForeignKey("market_topics.id",ondelete="CASCADE"),nullable=False),sa.Column("market_video_id",sa.Integer(),sa.ForeignKey("market_videos.id",ondelete="CASCADE"),nullable=False,unique=True),sa.Column("similarity_score",sa.Float(),nullable=False,server_default="0"))
def downgrade(): op.drop_table("market_topic_memberships");op.drop_table("market_topics")
