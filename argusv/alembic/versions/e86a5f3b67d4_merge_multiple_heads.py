"""merge multiple heads

Revision ID: e86a5f3b67d4
Revises: 7a29e046a79a, e99301c897a2
Create Date: 2026-03-15 14:54:03.788685

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e86a5f3b67d4'
down_revision: Union[str, Sequence[str], None] = ('7a29e046a79a', 'e99301c897a2')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
