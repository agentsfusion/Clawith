"""add_script_builder

Revision ID: 060_add_script_builder
Revises: add_title_to_agent_focus_items
Create Date: 2026-06-29 00:00:00.000000

Creates the Script Builder tables (script_conversations, script_messages) and
the AgentScriptVersion table (agent_script_versions). Idempotent: each table and
index creation is guarded by an inspector check so re-runs are safe.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "060_add_script_builder"
down_revision: Union[str, None] = "add_title_to_agent_focus_items"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # script_conversations -------------------------------------------------- #
    if not inspector.has_table("script_conversations"):
        op.create_table(
            "script_conversations",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column(
                "tenant_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("tenants.id"),
                nullable=False,
            ),
            sa.Column(
                "user_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id"),
                nullable=False,
            ),
            sa.Column("title", sa.String(length=200), nullable=False, server_default="New Session"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    existing_indexes = {idx["name"] for idx in inspector.get_indexes("script_conversations")} if inspector.has_table("script_conversations") else set()
    if "ix_script_conversations_tenant_id" not in existing_indexes:
        op.create_index("ix_script_conversations_tenant_id", "script_conversations", ["tenant_id"])
    if "ix_script_conversations_user_id" not in existing_indexes:
        op.create_index("ix_script_conversations_user_id", "script_conversations", ["user_id"])

    # script_messages ------------------------------------------------------- #
    if not inspector.has_table("script_messages"):
        op.create_table(
            "script_messages",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column(
                "conversation_id",
                sa.Integer(),
                sa.ForeignKey("script_conversations.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("role", sa.String(length=20), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    existing_indexes = {idx["name"] for idx in inspector.get_indexes("script_messages")} if inspector.has_table("script_messages") else set()
    if "ix_script_messages_conversation_id" not in existing_indexes:
        op.create_index("ix_script_messages_conversation_id", "script_messages", ["conversation_id"])

    # agent_script_versions ------------------------------------------------- #
    if not inspector.has_table("agent_script_versions"):
        op.create_table(
            "agent_script_versions",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column(
                "agent_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("agents.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("folder", sa.String(length=100), nullable=False, server_default="initial"),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("source", sa.String(length=50), nullable=False, server_default="script_builder"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    existing_indexes = {idx["name"] for idx in inspector.get_indexes("agent_script_versions")} if inspector.has_table("agent_script_versions") else set()
    if "ix_agent_script_versions_agent_id" not in existing_indexes:
        op.create_index("ix_agent_script_versions_agent_id", "agent_script_versions", ["agent_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # agent_script_versions
    if inspector.has_table("agent_script_versions"):
        indexes = {idx["name"] for idx in inspector.get_indexes("agent_script_versions")}
        if "ix_agent_script_versions_agent_id" in indexes:
            op.drop_index("ix_agent_script_versions_agent_id", table_name="agent_script_versions")
        op.drop_table("agent_script_versions")

    # script_messages (before conversations due to FK)
    if inspector.has_table("script_messages"):
        indexes = {idx["name"] for idx in inspector.get_indexes("script_messages")}
        if "ix_script_messages_conversation_id" in indexes:
            op.drop_index("ix_script_messages_conversation_id", table_name="script_messages")
        op.drop_table("script_messages")

    # script_conversations
    if inspector.has_table("script_conversations"):
        indexes = {idx["name"] for idx in inspector.get_indexes("script_conversations")}
        if "ix_script_conversations_tenant_id" in indexes:
            op.drop_index("ix_script_conversations_tenant_id", table_name="script_conversations")
        if "ix_script_conversations_user_id" in indexes:
            op.drop_index("ix_script_conversations_user_id", table_name="script_conversations")
        op.drop_table("script_conversations")
