"""add_evolution_jobs_table

Revision ID: 11c2664ca2b6
Revises: 0aeaf292d5b7
Create Date: 2026-04-14 14:11:19.458862
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision: str = '11c2664ca2b6'
down_revision: Union[str, None] = '0aeaf292d5b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    return inspect(bind).has_table(table_name)


def _index_exists(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    return index_name in [idx['name'] for idx in inspect(bind).get_indexes(table_name)]


def upgrade() -> None:
    if not _table_exists('evolution_jobs'):
        op.create_table('evolution_jobs',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('agent_id', sa.UUID(), nullable=False),
        sa.Column('direction', sa.Text(), nullable=False),
        sa.Column('cron_schedule', sa.String(length=100), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('next_run_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_run_status', sa.String(length=20), nullable=True),
        sa.Column('last_run_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['agent_id'], ['agents.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
        )
        if not _index_exists('evolution_jobs', 'ix_evolution_jobs_agent_id'):
            op.create_index(op.f('ix_evolution_jobs_agent_id'), 'evolution_jobs', ['agent_id'], unique=False)


def downgrade() -> None:
    if _table_exists('evolution_jobs'):
        if _index_exists('evolution_jobs', 'ix_evolution_jobs_agent_id'):
            op.drop_index(op.f('ix_evolution_jobs_agent_id'), table_name='evolution_jobs')
        op.drop_table('evolution_jobs')
