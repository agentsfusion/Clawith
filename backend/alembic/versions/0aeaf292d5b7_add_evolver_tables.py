"""add_evolver_tables

Revision ID: 0aeaf292d5b7
Revises: 19bffd4bc011
Create Date: 2026-04-13 07:00:30.501644
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision: str = '0aeaf292d5b7'
down_revision: Union[str, None] = '19bffd4bc011'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    return inspect(bind).has_table(table_name)


def _index_exists(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    return index_name in [idx['name'] for idx in inspect(bind).get_indexes(table_name)]


def upgrade() -> None:
    if not _table_exists('agent_feedbacks'):
        op.create_table('agent_feedbacks',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('agent_id', sa.UUID(), nullable=False),
        sa.Column('category', sa.String(length=30), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['agent_id'], ['agents.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
        if not _index_exists('agent_feedbacks', 'ix_agent_feedbacks_agent_id'):
            op.create_index(op.f('ix_agent_feedbacks_agent_id'), 'agent_feedbacks', ['agent_id'], unique=False)

    if not _table_exists('agent_health_checks'):
        op.create_table('agent_health_checks',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('agent_id', sa.UUID(), nullable=False),
        sa.Column('overall_score', sa.Integer(), nullable=False),
        sa.Column('dimensions', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('strengths', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('suggestions', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('script_version', sa.String(length=20), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['agent_id'], ['agents.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
        )
        if not _index_exists('agent_health_checks', 'ix_agent_health_checks_agent_id'):
            op.create_index(op.f('ix_agent_health_checks_agent_id'), 'agent_health_checks', ['agent_id'], unique=False)

    if not _table_exists('agent_script_versions'):
        op.create_table('agent_script_versions',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('agent_id', sa.UUID(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('folder', sa.String(length=30), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('source', sa.String(length=200), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['agent_id'], ['agents.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
        )
        if not _index_exists('agent_script_versions', 'ix_agent_script_versions_agent_id'):
            op.create_index(op.f('ix_agent_script_versions_agent_id'), 'agent_script_versions', ['agent_id'], unique=False)


def downgrade() -> None:
    if _table_exists('agent_script_versions'):
        if _index_exists('agent_script_versions', 'ix_agent_script_versions_agent_id'):
            op.drop_index(op.f('ix_agent_script_versions_agent_id'), table_name='agent_script_versions')
        op.drop_table('agent_script_versions')
    if _table_exists('agent_health_checks'):
        if _index_exists('agent_health_checks', 'ix_agent_health_checks_agent_id'):
            op.drop_index(op.f('ix_agent_health_checks_agent_id'), table_name='agent_health_checks')
        op.drop_table('agent_health_checks')
    if _table_exists('agent_feedbacks'):
        if _index_exists('agent_feedbacks', 'ix_agent_feedbacks_agent_id'):
            op.drop_index(op.f('ix_agent_feedbacks_agent_id'), table_name='agent_feedbacks')
        op.drop_table('agent_feedbacks')
