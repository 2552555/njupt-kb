"""Beta10 comment reports and appeals.

- reports 增加 comment_id（评论举报）与 revision（乐观并发）；
- reports 增加局部唯一索引：同一用户对同一评论至多一条未处理举报；
- 新增 comment_appeals 表：被隐藏评论的作者提交一次申诉。

downgrade 只能做结构回退：会丢弃评论举报的关联与申诉治理状态，
因此**不能据此宣称业务可安全回退**，生产回退需先备份并停止相关写入。
"""

from alembic import op
import sqlalchemy as sa

revision = "0007_beta10_interaction"
down_revision = "0006_beta10_ops"
branch_labels = None
depends_on = None

_ACTIVE_WHERE = (
    "comment_id IS NOT NULL AND status IN ('open', 'triaged', 'investigating')"
)


def upgrade():
    op.add_column("reports", sa.Column("comment_id", sa.Uuid(), nullable=True))
    op.add_column(
        "reports",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_foreign_key(
        "fk_reports_comment_id_comments",
        "reports",
        "comments",
        ["comment_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_reports_comment_id", "reports", ["comment_id"])
    op.create_index(
        "uq_reports_active_comment_reporter",
        "reports",
        ["reporter_id", "comment_id"],
        unique=True,
        postgresql_where=sa.text(_ACTIVE_WHERE),
    )

    op.create_table(
        "comment_appeals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "comment_id", sa.Uuid(), sa.ForeignKey("comments.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "resource_id", sa.Uuid(), sa.ForeignKey("resources.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("appellant_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("handled_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("handled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'rejected')",
            name="status_valid",
        ),
        sa.UniqueConstraint("comment_id", name="uq_comment_appeals_comment"),
    )
    op.create_index("ix_comment_appeals_comment_id", "comment_appeals", ["comment_id"])
    op.create_index("ix_comment_appeals_resource_id", "comment_appeals", ["resource_id"])
    op.create_index("ix_comment_appeals_appellant_id", "comment_appeals", ["appellant_id"])
    op.create_index("ix_comment_appeals_status", "comment_appeals", ["status"])
    op.create_index("ix_comment_appeals_created_at", "comment_appeals", ["created_at"])
    op.create_index(
        "ix_comment_appeals_status_created", "comment_appeals", ["status", "created_at"]
    )


def downgrade():
    op.drop_index("ix_comment_appeals_status_created", table_name="comment_appeals")
    op.drop_index("ix_comment_appeals_created_at", table_name="comment_appeals")
    op.drop_index("ix_comment_appeals_status", table_name="comment_appeals")
    op.drop_index("ix_comment_appeals_appellant_id", table_name="comment_appeals")
    op.drop_index("ix_comment_appeals_resource_id", table_name="comment_appeals")
    op.drop_index("ix_comment_appeals_comment_id", table_name="comment_appeals")
    op.drop_table("comment_appeals")

    op.drop_index("uq_reports_active_comment_reporter", table_name="reports")
    op.drop_index("ix_reports_comment_id", table_name="reports")
    op.drop_constraint("fk_reports_comment_id_comments", "reports", type_="foreignkey")
    op.drop_column("reports", "revision")
    op.drop_column("reports", "comment_id")
