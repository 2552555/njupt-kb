"""Encrypted authenticator enrollment and recent session verification."""

from alembic import op
import sqlalchemy as sa

revision = "0013_v002_mfa"
down_revision = "0012_v002_enum_checks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column("mfa_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "user_mfa",
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("secret_ciphertext", sa.String(512), nullable=True),
        sa.Column("enabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("setup_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_counter", sa.Integer(), nullable=True),
        sa.Column("recovery_hashes", sa.JSON(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_user_mfa_created_at", "user_mfa", ["created_at"])


def downgrade() -> None:
    # MFA cannot be removed while enabled accounts still rely on it.
    if op.get_context().as_sql:
        op.execute(
            "DO $$ BEGIN IF EXISTS (SELECT 1 FROM user_mfa WHERE enabled_at IS NOT NULL) THEN RAISE EXCEPTION 'Disable MFA through authenticated workflows before downgrade'; END IF; END $$"
        )
    elif op.get_bind().scalar(
        sa.text("SELECT COUNT(*) FROM user_mfa WHERE enabled_at IS NOT NULL")
    ):
        raise RuntimeError(
            "Disable MFA through authenticated workflows before downgrade"
        )
    op.drop_table("user_mfa")
    op.drop_column("sessions", "mfa_verified_at")
