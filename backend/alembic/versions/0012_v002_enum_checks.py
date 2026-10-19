"""Align persisted enum checks with beta15/16 and 0.0.2 notification types.

No records are deleted on downgrade. New values must be resolved before downgrade;
otherwise the database rejects the narrower constraint and keeps the transaction intact.
"""

from alembic import op

revision = "0012_v002_enum_checks"
down_revision = "0011_beta16_search"
branch_labels = None
depends_on = None

CHECKS = [
    (
        "domain_events",
        "event_type",
        "ck_domain_events_event_type",
        [
            "resource.version.submitted",
            "resource.version.approved",
            "resource.version.rejected",
            "resource.published",
            "resource.withdrawn",
            "resource.upload.received",
            "report.created",
            "report.resolved",
            "correction.created",
            "correction.resolved",
            "request.created",
            "request.fulfilled",
            "request.updated",
            "file.scan.finished",
            "file.derivative.ready",
            "order.paid",
            "refund.confirmed",
        ],
        [
            "resource.version.submitted",
            "resource.version.approved",
            "resource.version.rejected",
            "resource.published",
            "resource.withdrawn",
            "resource.upload.received",
            "report.created",
            "report.resolved",
            "correction.created",
            "correction.resolved",
            "request.created",
            "request.fulfilled",
            "request.updated",
            "file.scan.finished",
            "file.derivative.ready",
            "order.paid",
            "refund.confirmed",
            "resource.version.needs_info",
            "contributor.application.submitted",
            "contributor.application.decided",
            "assignment.changed",
            "case.created",
            "case.updated",
            "case.decided",
            "rights_notice.received",
            "rights_notice.counter",
            "rights_notice.decided",
            "agreement.accepted",
            "price.proposed",
            "price.confirmed",
            "price.rejected",
        ],
    ),
    (
        "notifications",
        "type",
        "ck_notifications_type",
        [
            "submission_received",
            "review_result",
            "version_published",
            "resource_withdrawn",
            "report_result",
            "correction_result",
            "request_updated",
        ],
        [
            "submission_received",
            "review_result",
            "version_published",
            "resource_withdrawn",
            "report_result",
            "correction_result",
            "request_updated",
            "review_needs_info",
            "contributor_application_result",
            "assignment_changed",
            "case_result",
            "rights_notice_result",
            "agreement_updated",
            "price_updated",
        ],
    ),
    (
        "moderation_decisions",
        "decision",
        "ck_moderation_decisions_decision_valid",
        ["approve", "reject"],
        ["approve", "needs_info", "reject"],
    ),
]


def _replace(use_new: bool) -> None:
    for table, column, name, old, new in CHECKS:
        values = new if use_new else old
        expression = column + " IN (" + ", ".join(repr(value) for value in values) + ")"
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(op.f(name), type_="check")
            batch.create_check_constraint(op.f(name), expression)


def upgrade() -> None:
    _replace(True)


def downgrade() -> None:
    _replace(False)
