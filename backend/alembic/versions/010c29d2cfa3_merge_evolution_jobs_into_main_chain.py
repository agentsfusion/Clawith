"""merge evolution_jobs into main chain

Revision ID: 010c29d2cfa3
Revises: 38585c65c87b, 11c2664ca2b6
Create Date: 2026-04-16 23:38:02.933971
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '010c29d2cfa3'
down_revision: Union[str, None] = ('38585c65c87b', '11c2664ca2b6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
