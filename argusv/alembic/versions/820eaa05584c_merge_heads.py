"""merge heads

Revision ID: 820eaa05584c
Revises: 427b9e81c67b, d1add19ad75a
Create Date: 2026-03-08 19:53:36.806869

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '820eaa05584c'
down_revision: Union[str, Sequence[str], None] = ('427b9e81c67b', 'd1add19ad75a')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
