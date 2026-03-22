"""Add description_embedding and thumbnail_url to segments

Revision ID: 0004_segment_rag
Revises: 0003_segment_description
Create Date: 2026-03-20

Enables semantic RAG search over video chunks:
  - description_embedding vector(1536): embedded scene description for pgvector search
  - thumbnail_url text: path to mid-segment JPEG frame for UI preview
"""

from alembic import op
import sqlalchemy as sa

revision = "0004_segment_rag"
down_revision = "0003_segment_description"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    cols = {c["name"] for c in sa.inspect(conn).get_columns("segments")}

    if "description_embedding" not in cols:
        op.execute("ALTER TABLE segments ADD COLUMN description_embedding vector(1536)")

    if "thumbnail_url" not in cols:
        op.add_column("segments", sa.Column("thumbnail_url", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("segments", "thumbnail_url")
    op.drop_column("segments", "description_embedding")
