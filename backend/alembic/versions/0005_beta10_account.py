"""Beta10 account deletion marker.

注销立即生效（不可登录、公开资料匿名化）；本列只记录注销时刻，
邮箱与审计/业务事实按保留期继续保留，供申诉与对账，期满由运维清理（§9）。
"""

from alembic import op
import sqlalchemy as sa

revision = "0005_beta10_account"
down_revision = "0004_beta9"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade():
    op.drop_column("users", "deleted_at")
