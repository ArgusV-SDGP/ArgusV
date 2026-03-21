"""Add description column to segments table

Revision ID: 0003_segment_description
Revises: 0002_vlm_columns
Create Date: 2026-03-20

Adds a GPT-4o generated scene description per video chunk.
"""

from alembic import op
import sqlalchemy as sa

revision = "0003_segment_description"
down_revision = "0002_vlm_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    cols = {c["name"] for c in sa.inspect(conn).get_columns("segments")}
    if "description" not in cols:
        op.add_column("segments", sa.Column("description", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("segments", "description")
