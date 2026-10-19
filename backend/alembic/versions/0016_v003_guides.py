"""Guide articles: editable course guides with versioned review.

Additive only: two new tables. Guide resources uploaded as files remain valid and are
not migrated into articles automatically; the two content types coexist.
"""

from alembic import op
import sqlalchemy as sa

revision = "0016_v003_guides"
down_revision = "0015_v003_preview_pages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "guide_articles",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "course_id",
            sa.Uuid(),
            sa.ForeignKey("courses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "author_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("summary", sa.String(500), nullable=True),
        sa.Column("body_markdown", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("published_version_no", sa.Integer(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latest_version_no", sa.Integer(), nullable=False),
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
        sa.CheckConstraint(
            "status IN ('draft', 'pending', 'published', 'rejected', 'withdrawn')",
            name="guide_article_status",
        ),
    )
    op.create_index("ix_guide_articles_created_at", "guide_articles", ["created_at"])
    op.create_index(
        "ix_guide_articles_course_status", "guide_articles", ["course_id", "status"]
    )
    op.create_index(
        "ix_guide_articles_author_created", "guide_articles", ["author_id", "created_at"]
    )

    op.create_table(
        "guide_article_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "article_id",
            sa.Uuid(),
            sa.ForeignKey("guide_articles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("summary", sa.String(500), nullable=True),
        sa.Column("body_markdown", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "reviewer_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("review_note", sa.Text(), nullable=True),
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
            "article_id", "version_no", name="uq_guide_article_versions_no"
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'pending', 'published', 'rejected', 'withdrawn')",
            name="guide_article_version_status",
        ),
    )
    op.create_index(
        "ix_guide_article_versions_created_at", "guide_article_versions", ["created_at"]
    )
    op.create_index(
        "ix_guide_article_versions_status",
        "guide_article_versions",
        ["status", "submitted_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_guide_article_versions_status", table_name="guide_article_versions"
    )
    op.drop_index(
        "ix_guide_article_versions_created_at", table_name="guide_article_versions"
    )
    op.drop_table("guide_article_versions")
    op.drop_index("ix_guide_articles_author_created", table_name="guide_articles")
    op.drop_index("ix_guide_articles_course_status", table_name="guide_articles")
    op.drop_index("ix_guide_articles_created_at", table_name="guide_articles")
    op.drop_table("guide_articles")
