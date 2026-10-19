"""Beta13 账号保留期清理字段（zhanghao.md §9「注销账号」）。

为 users 增加 purged_at：保留期届满后运维任务将可识别 PII（邮箱）置为墓碑并打上本时间戳。
不删除用户行与业务记录，仅抹除个人可识别信息；审计日志与内容贡献继续保留。

downgrade 仅删除该列：丢失"已清理"标记，但用户数据与业务记录不受影响；
注意旧代码若按 purged_at 过滤会报错，应先回滚代码再降级。
"""

from alembic import op
import sqlalchemy as sa

revision = "0009_beta13_retention"
down_revision = "0008_beta11_ratelimit"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users",
        sa.Column(
            "purged_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index("ix_users_purged_at", "users", ["purged_at"])


def downgrade():
    op.drop_index("ix_users_purged_at", table_name="users")
    op.drop_column("users", "purged_at")
