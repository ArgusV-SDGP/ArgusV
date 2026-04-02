"""add camera_id to zones

Revision ID: 2c7f9f19b6a1
Revises: 0005_detection_config
Create Date: 2026-03-20 03:35:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "2c7f9f19b6a1"
down_revision: Union[str, Sequence[str], None] = "0005_detection_config"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("zones", sa.Column("camera_id", sa.String(), nullable=True))
    op.create_index("ix_zones_camera_id", "zones", ["camera_id"], unique=False)
    op.create_foreign_key(
        "fk_zones_camera_id_cameras",
        "zones",
        "cameras",
        ["camera_id"],
        ["camera_id"],
        ondelete="SET NULL",
    )

    # Backfill camera_id using existing cameras.zone_id linkage.
    op.execute(
        """
        UPDATE zones
        SET camera_id = cameras.camera_id
        FROM cameras
        WHERE cameras.zone_id = zones.zone_id
          AND zones.camera_id IS NULL
        """
    )


def downgrade() -> None:
    op.drop_constraint("fk_zones_camera_id_cameras", "zones", type_="foreignkey")
    op.drop_index("ix_zones_camera_id", table_name="zones")
    op.drop_column("zones", "camera_id")
