"""Add detect_config to cameras + allowed_classes to zones

Revision ID: 0005_detection_config
Revises: 0004_segment_rag
Create Date: 2026-03-21

Changes:
  - cameras.detect_config JSONB — per-camera detection parameter overrides
  - zones.allowed_classes  JSONB — per-zone object class allow-list
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0005_detection_config"
down_revision = "0004_segment_rag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    cam_cols = {c["name"] for c in sa.inspect(conn).get_columns("cameras")}
    if "detect_config" not in cam_cols:
        op.add_column("cameras", sa.Column("detect_config", JSONB, nullable=True))

    zone_cols = {c["name"] for c in sa.inspect(conn).get_columns("zones")}
    if "allowed_classes" not in zone_cols:
        op.add_column("zones", sa.Column("allowed_classes", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("cameras", "detect_config")
    op.drop_column("zones", "allowed_classes")
