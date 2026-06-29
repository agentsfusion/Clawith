"""Evolver Agent models — Feedback, Health Checks, and Evolution Jobs.

These tables back the Agent Evolver feature (docs/Agent_Evolver_Tabs_Design.md
§3.1, §3.2, §3.4): user feedback collection, AI health-check scoring, and
cron-scheduled autonomous evolution jobs.

``AgentScriptVersion`` is intentionally NOT defined here — it already lives in
``app.models.script_builder`` and is imported where needed to avoid duplicating
the ``agent_script_versions`` table.
"""

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# Re-export so callers can import AgentScriptVersion from the evolver namespace
# without this module redefining the underlying agent_script_versions table.
from app.models.script_builder import AgentScriptVersion  # noqa: E402,F401


class AgentFeedback(Base):
    """User feedback on an evolver agent — drives evolution."""

    __tablename__ = "agent_feedbacks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    category: Mapped[str] = mapped_column(String(30), nullable=False, default="general")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentHealthCheck(Base):
    """AI quality scoring for an evolver agent's script."""

    __tablename__ = "agent_health_checks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    overall_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dimensions: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)
    strengths: Mapped[list | None] = mapped_column(JSON, nullable=True)
    suggestions: Mapped[list | None] = mapped_column(JSON, nullable=True)
    script_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvolutionJob(Base):
    """Cron-scheduled evolution job for an evolver agent."""

    __tablename__ = "evolution_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    direction: Mapped[str] = mapped_column(Text, nullable=False, default="general improvement")
    cron_schedule: Mapped[str] = mapped_column(String(100), nullable=False, default="0 0 * * *")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_run_status: Mapped[str | None] = mapped_column(String(20), nullable=True, default="")
    last_run_error: Mapped[str | None] = mapped_column(Text, nullable=True, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
