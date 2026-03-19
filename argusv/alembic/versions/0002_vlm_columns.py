"""Add vlm_summary + vlm_embedding(1536) to detections; add updated_at to zones

Revision ID: 0002_vlm_columns
Revises: 0001_baseline
Create Date: 2026-03-20

Handles three cases:
  1. Fresh DB (tables created by 0001_baseline) — columns already exist, IF NOT EXISTS is a no-op.
  2. DB bootstrapped via create_all() with current models — same, columns exist.
  3. DB bootstrapped via create_all() with OLD models, or 0001 baseline ran with
     pre-existing tables that lacked the columns — adds the missing columns.

vlm_embedding dimension fix: the old migration accidentally used Vector(384).
The correct model is text-embedding-3-small → 1536 dims.  If the column already
exists with the wrong type we drop and recreate it (data loss acceptable because
no embeddings could have been stored with the correct dimensions anyway).
"""

from alembic import op
import sqlalchemy as sa


revision = "0002_vlm_columns"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def _column_exists(conn, table: str, column: str) -> bool:
    cols = {c["name"] for c in sa.inspect(conn).get_columns(table)}
    return column in cols


def upgrade() -> None:
    conn = op.get_bind()

    # ── detections.vlm_summary ────────────────────────────────────────────────
    if not _column_exists(conn, "detections", "vlm_summary"):
        op.add_column("detections", sa.Column("vlm_summary", sa.Text(), nullable=True))

    # ── detections.vlm_embedding (must be vector(1536)) ───────────────────────
    # Drop if it exists with the wrong dimension (old migration used 384).
    if _column_exists(conn, "detections", "vlm_embedding"):
        # Check the actual dimension stored in pg_attribute / atttypmod.
        result = conn.execute(sa.text("""
            SELECT atttypmod
            FROM   pg_attribute
            WHERE  attrelid = 'detections'::regclass
            AND    attname  = 'vlm_embedding'
            AND    attnum   > 0
        """)).scalar()
        # atttypmod for vector(n) is stored as n+8 (Postgres internal).  A value
        # of -1 means "no modifier" (shouldn't happen but guard anyway).
        actual_dim = (result - 8) if result and result != -1 else None
        if actual_dim != 1536:
            op.drop_column("detections", "vlm_embedding")
            op.execute("ALTER TABLE detections ADD COLUMN vlm_embedding vector(1536)")
    else:
        op.execute("ALTER TABLE detections ADD COLUMN vlm_embedding vector(1536)")

    # ── zones.updated_at ──────────────────────────────────────────────────────
    if not _column_exists(conn, "zones", "updated_at"):
        op.add_column("zones", sa.Column("updated_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    if _column_exists(conn, "zones", "updated_at"):
        op.drop_column("zones", "updated_at")
    if _column_exists(conn, "detections", "vlm_embedding"):
        op.drop_column("detections", "vlm_embedding")
    if _column_exists(conn, "detections", "vlm_summary"):
        op.drop_column("detections", "vlm_summary")
