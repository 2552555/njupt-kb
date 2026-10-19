"""Beta5 profiles and structured classification corrections."""
from alembic import op
import sqlalchemy as sa

revision = "0002_beta5"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("user_profiles", sa.Column("gender", sa.String(30), nullable=True))
    op.add_column("user_profiles", sa.Column("bio", sa.String(500), nullable=True))
    op.add_column("corrections", sa.Column("suggested_course_id", sa.Uuid(), nullable=True))
    op.add_column("corrections", sa.Column("suggested_resource_type", sa.String(30), nullable=True))
    op.create_foreign_key("fk_corrections_suggested_course", "corrections", "courses", ["suggested_course_id"], ["id"])


def downgrade():
    op.drop_constraint("fk_corrections_suggested_course", "corrections", type_="foreignkey")
    op.drop_column("corrections", "suggested_resource_type")
    op.drop_column("corrections", "suggested_course_id")
    op.drop_column("user_profiles", "bio")
    op.drop_column("user_profiles", "gender")
