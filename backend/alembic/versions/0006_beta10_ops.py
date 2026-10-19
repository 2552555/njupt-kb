"""Beta10 admin catalog maintenance: teacher/semester status and course inactive state.

新增字段仅在需要软停用时才加：
- `teachers.status`、`semesters.status` 支持管理员软停用教师与学期；
- `courses.status` 的 CHECK 增加 `inactive`，让停用课程不再进入前台选择器与检索。

不新增任何 domain event 类型；不改动 `ck_domain_events_event_type`。
"""

from alembic import op
import sqlalchemy as sa

revision = "0006_beta10_ops"
down_revision = "0005_beta10_account"
branch_labels = None
depends_on = None

_CATALOG_STATUSES = "'active', 'inactive', 'merged'"
_COURSE_STATUSES = "'active', 'deprecated', 'merged', 'pending_classification', 'inactive'"


def upgrade():
    op.add_column(
        "teachers",
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
    )
    op.create_check_constraint(
        op.f("ck_teachers_status_valid"),
        "teachers",
        f"status IN ({_CATALOG_STATUSES})",
    )

    op.add_column(
        "semesters",
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
    )
    op.create_check_constraint(
        op.f("ck_semesters_status_valid"),
        "semesters",
        f"status IN ({_CATALOG_STATUSES})",
    )

    op.drop_constraint(op.f("ck_courses_status_valid"), "courses", type_="check")
    op.create_check_constraint(
        op.f("ck_courses_status_valid"),
        "courses",
        f"status IN ({_COURSE_STATUSES})",
    )


def downgrade():
    # 回退前须确认没有 inactive 课程，否则重建旧约束会失败；不静默改写课程状态。
    op.drop_constraint(op.f("ck_courses_status_valid"), "courses", type_="check")
    op.create_check_constraint(
        op.f("ck_courses_status_valid"),
        "courses",
        "status IN ('active', 'deprecated', 'merged', 'pending_classification')",
    )

    op.drop_constraint(op.f("ck_semesters_status_valid"), "semesters", type_="check")
    op.drop_column("semesters", "status")

    op.drop_constraint(op.f("ck_teachers_status_valid"), "teachers", type_="check")
    op.drop_column("teachers", "status")
