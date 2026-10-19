"""Active defense: honeytokens, risk signals, risk subjects and policy versions.

Only additive DDL: four new tables and their indexes. No existing column, constraint
or row is rewritten; historical audit/business records are untouched.
"""

from alembic import op
import sqlalchemy as sa

revision = "0014_v003_defense"
down_revision = "0013_v002_mfa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "honeytokens",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("token_digest", sa.String(64), nullable=False),
        sa.Column("label", sa.String(64), nullable=False),
        sa.Column("placement", sa.String(40), nullable=False),
        sa.Column("placement_ref", sa.String(200), nullable=True),
        sa.Column(
            "owner_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("expected_visitor", sa.String(200), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
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
        sa.UniqueConstraint("token_digest", name="uq_honeytokens_token_digest"),
        sa.CheckConstraint(
            "status IN ('active', 'retired')", name="honeytoken_status"
        ),
    )
    op.create_index("ix_honeytokens_created_at", "honeytokens", ["created_at"])
    op.create_index(
        "ix_honeytokens_status_expires_at", "honeytokens", ["status", "expires_at"]
    )

    op.create_table(
        "defense_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("signal", sa.String(60), nullable=False),
        sa.Column("weight", sa.Integer(), nullable=False),
        sa.Column("subject_kind", sa.String(10), nullable=False),
        sa.Column("subject_key", sa.String(80), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("method", sa.String(10), nullable=True),
        sa.Column("path_template", sa.String(200), nullable=True),
        sa.Column("decoy_label", sa.String(64), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.CheckConstraint(
            "kind IN ('honeypot_hit', 'honeytoken_hit', 'enumeration', "
            "'boundary_probe', 'param_sweep', 'deep_pagination', 'rate_pressure')",
            name="defense_event_kind",
        ),
    )
    op.create_index("ix_defense_events_created_at", "defense_events", ["created_at"])
    op.create_index("ix_defense_events_occurred_at", "defense_events", ["occurred_at"])
    op.create_index(
        "ix_defense_events_subject_occurred",
        "defense_events",
        ["subject_key", "occurred_at"],
    )
    op.create_index(
        "ix_defense_events_kind_occurred", "defense_events", ["kind", "occurred_at"]
    )

    op.create_table(
        "risk_subjects",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("subject_key", sa.String(80), nullable=False),
        sa.Column("subject_kind", sa.String(10), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("level", sa.String(20), nullable=False),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("action_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("action_reason", sa.String(200), nullable=True),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_signal_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_signal_kind", sa.String(32), nullable=True),
        sa.Column("signal_count", sa.Integer(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "revoked_by",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("revoke_reason", sa.String(200), nullable=True),
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
        sa.UniqueConstraint("subject_key", name="uq_risk_subjects_subject_key"),
        sa.CheckConstraint(
            "level IN ('normal', 'suspicious', 'high')", name="risk_subject_level"
        ),
        sa.CheckConstraint(
            "action IN ('observe', 'throttle', 'challenge', 'review')",
            name="risk_subject_action",
        ),
    )
    op.create_index("ix_risk_subjects_created_at", "risk_subjects", ["created_at"])
    op.create_index(
        "ix_risk_subjects_action_expires", "risk_subjects", ["action", "action_expires_at"]
    )

    op.create_table(
        "defense_policies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(10), nullable=False),
        sa.Column("suspicious_score", sa.Float(), nullable=False),
        sa.Column("high_score", sa.Float(), nullable=False),
        sa.Column("throttle_factor_suspicious", sa.Float(), nullable=False),
        sa.Column("throttle_factor_high", sa.Float(), nullable=False),
        sa.Column("window_seconds", sa.Integer(), nullable=False),
        sa.Column("decay_half_life_seconds", sa.Integer(), nullable=False),
        sa.Column("action_ttl_seconds", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_by",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("note", sa.Text(), nullable=True),
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
        sa.UniqueConstraint("version", name="uq_defense_policies_version"),
        sa.CheckConstraint(
            "mode IN ('observe', 'enforce')", name="defense_policy_mode"
        ),
    )
    op.create_index("ix_defense_policies_created_at", "defense_policies", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_defense_policies_created_at", table_name="defense_policies")
    op.drop_table("defense_policies")
    op.drop_index("ix_risk_subjects_action_expires", table_name="risk_subjects")
    op.drop_index("ix_risk_subjects_created_at", table_name="risk_subjects")
    op.drop_table("risk_subjects")
    op.drop_index("ix_defense_events_kind_occurred", table_name="defense_events")
    op.drop_index("ix_defense_events_subject_occurred", table_name="defense_events")
    op.drop_index("ix_defense_events_occurred_at", table_name="defense_events")
    op.drop_index("ix_defense_events_created_at", table_name="defense_events")
    op.drop_table("defense_events")
    op.drop_index("ix_honeytokens_status_expires_at", table_name="honeytokens")
    op.drop_index("ix_honeytokens_created_at", table_name="honeytokens")
    op.drop_table("honeytokens")
