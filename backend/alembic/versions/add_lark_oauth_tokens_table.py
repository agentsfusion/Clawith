"""Add lark_oauth_tokens table

Revision ID: add_lark_oauth_tokens_table
Revises: add_gws_and_settings_tables
Create Date: 2026-04-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = "add_lark_oauth_tokens_table"
down_revision: Union[str, None] = "add_gws_and_settings_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS lark_oauth_tokens (
            id UUID PRIMARY KEY,
            agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            tenant_id UUID REFERENCES tenants(id),
            lark_user_id TEXT NOT NULL,
            lark_user_name VARCHAR(255),
            lark_avatar_url TEXT,
            access_token TEXT NOT NULL,
            refresh_token TEXT NOT NULL,
            token_expiry TIMESTAMPTZ,
            scopes TEXT[],
            status VARCHAR(20) DEFAULT 'active',
            last_used_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now(),
            CONSTRAINT uq_lark_oauth_agent_user UNIQUE (agent_id, user_id)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_lark_oauth_agent_id ON lark_oauth_tokens(agent_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_lark_oauth_user_id ON lark_oauth_tokens(user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_lark_oauth_tenant_id ON lark_oauth_tokens(tenant_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS lark_oauth_tokens")
