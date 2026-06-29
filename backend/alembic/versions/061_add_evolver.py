"""add_evolver

Revision ID: 061_add_evolver
Revises: 060_add_script_builder
Create Date: 2026-06-29 00:00:00.000000

Creates the Agent Evolver tables (agent_feedbacks, agent_health_checks,
evolution_jobs). The ``agent_script_versions`` table is NOT created here — it
was already created in revision 060_add_script_builder. Idempotent: each table
and index creation is guarded by an inspector check so re-runs are safe.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "061_add_evolver"
down_revision: Union[str, None] = "060_add_script_builder"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # agent_feedbacks ------------------------------------------------------- #
    if not inspector.has_table("agent_feedbacks"):
        op.create_table(
            "agent_feedbacks",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column(
                "agent_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("agents.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("category", sa.String(length=30), nullable=False, server_default="general"),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="open"),
            sa.Column(
                "created_by",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id"),
                nullable=True,
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    existing_indexes = (
        {idx["name"] for idx in inspector.get_indexes("agent_feedbacks")}
        if inspector.has_table("agent_feedbacks")
        else set()
    )
    if "ix_agent_feedbacks_agent_id" not in existing_indexes:
        op.create_index("ix_agent_feedbacks_agent_id", "agent_feedbacks", ["agent_id"])

    # agent_health_checks --------------------------------------------------- #
    if not inspector.has_table("agent_health_checks"):
        op.create_table(
            "agent_health_checks",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column(
                "agent_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("agents.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("overall_score", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("dimensions", sa.JSON(), nullable=True),
            sa.Column("strengths", sa.JSON(), nullable=True),
            sa.Column("suggestions", sa.JSON(), nullable=True),
            sa.Column("script_version", sa.String(length=20), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    existing_indexes = (
        {idx["name"] for idx in inspector.get_indexes("agent_health_checks")}
        if inspector.has_table("agent_health_checks")
        else set()
    )
    if "ix_agent_health_checks_agent_id" not in existing_indexes:
        op.create_index("ix_agent_health_checks_agent_id", "agent_health_checks", ["agent_id"])

    # evolution_jobs -------------------------------------------------------- #
    if not inspector.has_table("evolution_jobs"):
        op.create_table(
            "evolution_jobs",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column(
                "agent_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("agents.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "direction", sa.Text(), nullable=False, server_default="general improvement"
            ),
            sa.Column(
                "cron_schedule", sa.String(length=100), nullable=False, server_default="0 0 * * *"
            ),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_run_status", sa.String(length=20), nullable=True, server_default=""),
            sa.Column("last_run_error", sa.Text(), nullable=True, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    existing_indexes = (
        {idx["name"] for idx in inspector.get_indexes("evolution_jobs")}
        if inspector.has_table("evolution_jobs")
        else set()
    )
    if "ix_evolution_jobs_agent_id" not in existing_indexes:
        op.create_index("ix_evolution_jobs_agent_id", "evolution_jobs", ["agent_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # evolution_jobs
    if inspector.has_table("evolution_jobs"):
        indexes = {idx["name"] for idx in inspector.get_indexes("evolution_jobs")}
        if "ix_evolution_jobs_agent_id" in indexes:
            op.drop_index("ix_evolution_jobs_agent_id", table_name="evolution_jobs")
        op.drop_table("evolution_jobs")

    # agent_health_checks
    if inspector.has_table("agent_health_checks"):
        indexes = {idx["name"] for idx in inspector.get_indexes("agent_health_checks")}
        if "ix_agent_health_checks_agent_id" in indexes:
            op.drop_index("ix_agent_health_checks_agent_id", table_name="agent_health_checks")
        op.drop_table("agent_health_checks")

    # agent_feedbacks
    if inspector.has_table("agent_feedbacks"):
        indexes = {idx["name"] for idx in inspector.get_indexes("agent_feedbacks")}
        if "ix_agent_feedbacks_agent_id" in indexes:
            op.drop_index("ix_agent_feedbacks_agent_id", table_name="agent_feedbacks")
        op.drop_table("agent_feedbacks")
