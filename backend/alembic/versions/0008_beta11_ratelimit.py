"""Beta11 API 分级限流计数器（pachong.md §三、§四）。

新增 rate_limit_counters：固定窗口计数表，(action, subject_kind, subject, window_start)
唯一。subject 优先登录用户、其次会话、最后 IP，避免校园网同一公网 IP 互相挤占。

downgrade 删除整张表：只丢失限流计数窗口，不影响任何业务数据；
但降级后运行中的旧版本代码若仍注入限流依赖会因缺表报错，应先回滚代码再降级。
"""

from alembic import op
import sqlalchemy as sa

revision = "0008_beta11_ratelimit"
down_revision = "0007_beta10_interaction"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "rate_limit_counters",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("subject_kind", sa.String(length=16), nullable=False),
        sa.Column("subject", sa.String(length=64), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint(
            "subject_kind IN ('user', 'session', 'ip')",
            name="subject_kind",
        ),
        sa.CheckConstraint("count >= 0", name="count"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "action",
            "subject_kind",
            "subject",
            "window_start",
            name="uq_rate_limit_counters_window",
        ),
    )
    # 清理过期窗口按 window_start 范围删除；按动作排查按 action。
    op.create_index(
        "ix_rate_limit_counters_window_start",
        "rate_limit_counters",
        ["window_start"],
    )
    op.create_index(
        "ix_rate_limit_counters_action", "rate_limit_counters", ["action"]
    )


def downgrade():
    # 仅丢弃限流计数窗口，不影响业务数据；注意旧代码仍在跑时会因缺表报错。
    op.drop_index(
        "ix_rate_limit_counters_action", table_name="rate_limit_counters"
    )
    op.drop_index(
        "ix_rate_limit_counters_window_start", table_name="rate_limit_counters"
    )
    op.drop_table("rate_limit_counters")
