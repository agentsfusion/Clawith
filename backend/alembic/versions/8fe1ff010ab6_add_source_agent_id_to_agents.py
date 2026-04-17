"""add_source_agent_id_to_agents

Revision ID: 8fe1ff010ab6
Revises: 010c29d2cfa3
Create Date: 2026-04-17 12:38:34.663444
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '8fe1ff010ab6'
down_revision: Union[str, None] = '010c29d2cfa3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('agents', sa.Column('source_agent_id', sa.UUID(), nullable=True))
    op.create_foreign_key('fk_agents_source_agent_id', 'agents', 'agents', ['source_agent_id'], ['id'])


def downgrade() -> None:
    op.drop_constraint('fk_agents_source_agent_id', 'agents', type_='foreignkey')
    op.drop_column('agents', 'source_agent_id')
