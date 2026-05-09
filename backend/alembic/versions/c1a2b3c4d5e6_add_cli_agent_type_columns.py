"""Add CLI agent type columns to agents table

Revision ID: c1a2b3c4d5e6
Revises: f8a3b2c1d4e5
Create Date: 2026-05-05

Adds cli_engine, cli_api_key_encrypted, and cli_config columns
to support the new 'cli' agent_type for CLI subprocess execution.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c1a2b3c4d5e6'
down_revision: Union[str, None] = 'f8a3b2c1d4e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('agents', sa.Column('cli_engine', sa.String(30), nullable=True))
    op.add_column('agents', sa.Column('cli_api_key_encrypted', sa.String(500), nullable=True))
    op.add_column('agents', sa.Column('cli_config', sa.JSON(), nullable=True, server_default='{}'))


def downgrade() -> None:
    op.drop_column('agents', 'cli_config')
    op.drop_column('agents', 'cli_api_key_encrypted')
    op.drop_column('agents', 'cli_engine')
