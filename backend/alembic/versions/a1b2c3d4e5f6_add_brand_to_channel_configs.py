"""add brand column to channel_configs

Revision ID: a1b2c3d4e5f6
Revises: df3da9cf3b27
Create Date: 2026-04-17 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'df3da9cf3b27'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'channel_configs',
        sa.Column('brand', sa.String(16), server_default='feishu', nullable=True),
    )


def downgrade() -> None:
    op.drop_column('channel_configs', 'brand')
