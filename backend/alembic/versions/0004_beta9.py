"""Beta9 request resolution, responses and notification event."""

from alembic import op
import sqlalchemy as sa

revision = "0004_beta9"
down_revision = "0003_beta8"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "resource_requests",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "resource_requests",
        sa.Column("handled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "resource_requests", sa.Column("handled_by", sa.Uuid(), nullable=True)
    )
    op.create_foreign_key(
        "fk_request_handler", "resource_requests", "users", ["handled_by"], ["id"]
    )
    op.create_table(
        "resource_request_responses",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "request_id",
            sa.Uuid(),
            sa.ForeignKey("resource_requests.id"),
            nullable=False,
        ),
        sa.Column(
            "resource_id", sa.Uuid(), sa.ForeignKey("resources.id"), nullable=False
        ),
        sa.Column("submitted_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "request_id", "resource_id", name="uq_request_response_resource"
        ),
    )
    op.create_index(
        "ix_resource_request_responses_request_id",
        "resource_request_responses",
        ["request_id"],
    )
    op.drop_constraint(
        op.f("ck_domain_events_event_type"), "domain_events", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_domain_events_event_type"),
        "domain_events",
        "event_type IN ('resource.version.submitted', 'resource.version.approved', 'resource.version.rejected', 'resource.published', 'resource.withdrawn', 'resource.upload.received', 'report.created', 'report.resolved', 'correction.created', 'correction.resolved', 'request.created', 'request.fulfilled', 'request.updated', 'file.scan.finished', 'file.derivative.ready', 'order.paid', 'refund.confirmed')",
    )


def downgrade():
    # 停止相关写入并备份后回退；新事件仍存在时拒绝回退，不能静默删除事件。
    op.drop_constraint(
        op.f("ck_domain_events_event_type"), "domain_events", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_domain_events_event_type"),
        "domain_events",
        "event_type IN ('resource.version.submitted', 'resource.version.approved', 'resource.version.rejected', 'resource.published', 'resource.withdrawn', 'resource.upload.received', 'report.created', 'report.resolved', 'correction.created', 'correction.resolved', 'request.created', 'request.fulfilled', 'file.scan.finished', 'file.derivative.ready', 'order.paid', 'refund.confirmed')",
    )
    op.drop_table("resource_request_responses")
    op.drop_constraint("fk_request_handler", "resource_requests", type_="foreignkey")
    op.drop_column("resource_requests", "handled_by")
    op.drop_column("resource_requests", "handled_at")
    op.drop_column("resource_requests", "revision")
