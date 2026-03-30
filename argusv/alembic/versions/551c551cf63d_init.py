"""init

Revision ID: 551c551cf63d
Revises: f5457ad07408
Create Date: 2026-03-07 15:57:16.656619

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '551c551cf63d'
down_revision: Union[str, Sequence[str], None] = 'f5457ad07408'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
