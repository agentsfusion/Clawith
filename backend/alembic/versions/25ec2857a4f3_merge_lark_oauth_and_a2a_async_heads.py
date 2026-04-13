"""merge lark_oauth and a2a_async heads

Revision ID: 25ec2857a4f3
Revises: f1a2b3c4d5e6, add_lark_oauth_tokens_table
Create Date: 2026-04-13 22:29:37.199367
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '25ec2857a4f3'
down_revision: Union[str, None] = ('f1a2b3c4d5e6', 'add_lark_oauth_tokens_table')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
