"""Email change request tracking.

Additive only: one new table `email_change_requests` to support the double-confirm
email rebinding flow (0.0.3, §21). No existing behavior or data is migrated.
"""

from alembic import op
import sqlalchemy as sa

revision = "0018_v003_email_change"
down_revision = "0017_v003_reco"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_change_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("new_email", sa.String(320), nullable=False),
        sa.Column("token_digest", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("token_digest", name="uq_email_change_requests_token_digest"),
    )


def downgrade() -> None:
    op.drop_table("email_change_requests")
