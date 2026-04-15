"""merge api_key_length and lark_gws heads

Revision ID: 38585c65c87b
Revises: 25ec2857a4f3, increase_api_key_length
Create Date: 2026-04-15 17:10:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '38585c65c87b'
down_revision: Union[str, None] = ('25ec2857a4f3', 'increase_api_key_length')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
