"""merge_source_agent_id_and_skill_exec_heads

Revision ID: 6daca74ddc46
Revises: 8fe1ff010ab6, a8f1d9c4e2b7
Create Date: 2026-04-19 10:01:27.480672
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6daca74ddc46'
down_revision: Union[str, None] = ('8fe1ff010ab6', 'a8f1d9c4e2b7')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
