"""add skill_exec kind to gateway_messages

Revision ID: a8f1d9c4e2b7
Revises: 010c29d2cfa3
Create Date: 2026-04-17

Adds support for broadcast skill execution jobs queued through the gateway
message bus. The `kind` column distinguishes 'chat' messages (existing,
addressed to a specific openclaw agent) from 'skill_exec' jobs (broadcast,
claimable by any openclaw worker). For skill_exec jobs, agent_id is NULL.
"""
from alembic import op
import sqlalchemy as sa


revision = "a8f1d9c4e2b7"
down_revision = "010c29d2cfa3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "gateway_messages",
        sa.Column("kind", sa.String(20), nullable=False, server_default="chat"),
    )
    op.alter_column("gateway_messages", "agent_id", nullable=True)
    op.create_index(
        "ix_gateway_messages_kind_status",
        "gateway_messages",
        ["kind", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_gateway_messages_kind_status", table_name="gateway_messages")
    op.alter_column("gateway_messages", "agent_id", nullable=False)
    op.drop_column("gateway_messages", "kind")
