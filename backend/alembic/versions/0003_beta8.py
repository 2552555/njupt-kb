"""Beta8 comment withdrawal and moderation; existing comments stay visible."""

from alembic import op
import sqlalchemy as sa

revision = "0003_beta8"
down_revision = "0002_beta5"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("comments", sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("comments", sa.Column("is_hidden", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("comments", sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))


def downgrade():
    # 降级会移除治理状态；应用应先停写并备份，避免旧版本重新展示被隐藏正文。
    op.drop_column("comments", "revision")
    op.drop_column("comments", "is_hidden")
    op.drop_column("comments", "withdrawn_at")
