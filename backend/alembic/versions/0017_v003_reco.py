"""Recommendation behavior, models and experiments.

Additive only: six new tables. No existing behavior data is migrated; recommendation
continues to work with the default weights until a model is trained and activated.
"""

from alembic import op
import sqlalchemy as sa

revision = "0017_v003_reco"
down_revision = "0016_v003_guides"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "behavior_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("session_key", sa.String(64), nullable=True),
        sa.Column("event_type", sa.String(20), nullable=False),
        sa.Column(
            "resource_id",
            sa.Uuid(),
            sa.ForeignKey("resources.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "course_id",
            sa.Uuid(),
            sa.ForeignKey("courses.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("surface", sa.String(20), nullable=True),
        sa.Column("position", sa.Integer(), nullable=True),
        sa.Column("dwell_ms", sa.Integer(), nullable=True),
        sa.Column("progress_pct", sa.Integer(), nullable=True),
        sa.Column("experiment_key", sa.String(40), nullable=True),
        sa.Column("variant", sa.String(20), nullable=True),
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
            "event_type IN ('impression', 'click', 'view', 'progress', 'complete', "
            "'search', 'favorite', 'download')",
            name="behavior_event_type",
        ),
    )
    op.create_index("ix_behavior_events_created_at", "behavior_events", ["created_at"])
    op.create_index("ix_behavior_events_occurred_at", "behavior_events", ["occurred_at"])
    op.create_index(
        "ix_behavior_events_user_occurred", "behavior_events", ["user_id", "occurred_at"]
    )
    op.create_index(
        "ix_behavior_events_resource_type",
        "behavior_events",
        ["resource_id", "event_type"],
    )

    op.create_table(
        "reading_progress",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "resource_id",
            sa.Uuid(),
            sa.ForeignKey("resources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("progress_pct", sa.Integer(), nullable=False),
        sa.Column("last_position", sa.Integer(), nullable=True),
        sa.Column("total_dwell_ms", sa.Integer(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint(
            "user_id", "resource_id", name="uq_reading_progress_user_resource"
        ),
    )
    op.create_index("ix_reading_progress_created_at", "reading_progress", ["created_at"])
    op.create_index("ix_reading_progress_user_id", "reading_progress", ["user_id"])
    op.create_index("ix_reading_progress_resource_id", "reading_progress", ["resource_id"])

    op.create_table(
        "behavior_consents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("version", sa.String(20), nullable=False),
        sa.Column("reason", sa.String(200), nullable=True),
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
        sa.UniqueConstraint("user_id", name="uq_behavior_consents_user_id"),
    )
    op.create_index("ix_behavior_consents_created_at", "behavior_consents", ["created_at"])

    op.create_table(
        "recommendation_models",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("params", sa.JSON(), nullable=True),
        sa.Column("metrics", sa.JSON(), nullable=True),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("trained_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_by",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
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
        sa.UniqueConstraint("version", name="uq_recommendation_models_version"),
        sa.CheckConstraint(
            "status IN ('trained', 'active', 'archived')",
            name="recommendation_model_status",
        ),
    )
    op.create_index(
        "ix_recommendation_models_created_at", "recommendation_models", ["created_at"]
    )

    op.create_table(
        "experiments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("key", sa.String(40), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("variants", sa.JSON(), nullable=True),
        sa.Column("metric", sa.String(40), nullable=False),
        sa.Column("traffic_bps", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint("key", name="uq_experiments_key"),
        sa.CheckConstraint(
            "status IN ('draft', 'running', 'stopped', 'archived')",
            name="experiment_status",
        ),
    )
    op.create_index("ix_experiments_created_at", "experiments", ["created_at"])

    op.create_table(
        "experiment_assignments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "experiment_id",
            sa.Uuid(),
            sa.ForeignKey("experiments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("subject_key", sa.String(80), nullable=False),
        sa.Column("variant", sa.String(20), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exposure_count", sa.Integer(), nullable=False),
        sa.Column("click_count", sa.Integer(), nullable=False),
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
        sa.UniqueConstraint(
            "experiment_id", "subject_key", name="uq_experiment_assignments_subject"
        ),
    )
    op.create_index(
        "ix_experiment_assignments_created_at", "experiment_assignments", ["created_at"]
    )
    op.create_index(
        "ix_experiment_assignments_variant",
        "experiment_assignments",
        ["experiment_id", "variant"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_experiment_assignments_variant", table_name="experiment_assignments"
    )
    op.drop_index(
        "ix_experiment_assignments_created_at", table_name="experiment_assignments"
    )
    op.drop_table("experiment_assignments")
    op.drop_index("ix_experiments_created_at", table_name="experiments")
    op.drop_table("experiments")
    op.drop_index(
        "ix_recommendation_models_created_at", table_name="recommendation_models"
    )
    op.drop_table("recommendation_models")
    op.drop_index("ix_behavior_consents_created_at", table_name="behavior_consents")
    op.drop_table("behavior_consents")
    op.drop_index("ix_reading_progress_resource_id", table_name="reading_progress")
    op.drop_index("ix_reading_progress_user_id", table_name="reading_progress")
    op.drop_index("ix_reading_progress_created_at", table_name="reading_progress")
    op.drop_table("reading_progress")
    op.drop_index("ix_behavior_events_resource_type", table_name="behavior_events")
    op.drop_index("ix_behavior_events_user_occurred", table_name="behavior_events")
    op.drop_index("ix_behavior_events_occurred_at", table_name="behavior_events")
    op.drop_index("ix_behavior_events_created_at", table_name="behavior_events")
    op.drop_table("behavior_events")
