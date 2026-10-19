"""Multi-page image preview: record the page number on each derivative row.

Additive only: one nullable column plus an index. Historical single-page rows keep
NULL and remain readable through the legacy lookup path; no backfill is performed.
"""

from alembic import op
import sqlalchemy as sa

revision = "0015_v003_preview_pages"
down_revision = "0014_v003_defense"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("file_derivatives", sa.Column("page_no", sa.Integer(), nullable=True))
    op.create_index(
        "ix_file_derivatives_source_page",
        "file_derivatives",
        ["source_file_id", "page_no"],
    )


def downgrade() -> None:
    op.drop_index("ix_file_derivatives_source_page", table_name="file_derivatives")
    op.drop_column("file_derivatives", "page_no")
