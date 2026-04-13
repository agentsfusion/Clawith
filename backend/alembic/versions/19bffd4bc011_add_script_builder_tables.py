"""add_script_builder_tables

Revision ID: 19bffd4bc011
Revises: add_lark_oauth_tokens_table
Create Date: 2026-04-13 05:14:40.610012
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '19bffd4bc011'
down_revision: Union[str, None] = 'add_lark_oauth_tokens_table'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('script_conversations',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('tenant_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_script_conversations_tenant_id'), 'script_conversations', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_script_conversations_user_id'), 'script_conversations', ['user_id'], unique=False)
    op.create_table('script_messages',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('conversation_id', sa.Integer(), nullable=False),
    sa.Column('role', sa.String(length=20), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['conversation_id'], ['script_conversations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_script_messages_conversation_id'), 'script_messages', ['conversation_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_script_messages_conversation_id'), table_name='script_messages')
    op.drop_table('script_messages')
    op.drop_index(op.f('ix_script_conversations_user_id'), table_name='script_conversations')
    op.drop_index(op.f('ix_script_conversations_tenant_id'), table_name='script_conversations')
    op.drop_table('script_conversations')
