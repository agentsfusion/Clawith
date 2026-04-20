"""add config_source and used_client_id to gws_oauth_tokens

Revision ID: f8a3b2c1d4e5
Revises: 6daca74ddc46
Create Date: 2026-04-20

Adds audit columns to gws_oauth_tokens for tracking which OAuth client
configuration was used to obtain each token.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f8a3b2c1d4e5'
down_revision: Union[str, None] = '6daca74ddc46'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'gws_oauth_tokens',
        sa.Column('config_source', sa.String(20), nullable=False, server_default='tenant'),
    )
    op.add_column(
        'gws_oauth_tokens',
        sa.Column('used_client_id', sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('gws_oauth_tokens', 'used_client_id')
    op.drop_column('gws_oauth_tokens', 'config_source')
