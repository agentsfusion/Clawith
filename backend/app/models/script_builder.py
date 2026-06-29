"""Script Builder models — standalone conversation & message tables for Agent Script generation.

These two tables back the Script Builder feature (docs/Script_Builder_Design.md §4):
a tenant/user-isolated chat subsystem that drives an LLM to produce Salesforce
Agentforce ``.ascript`` files. They are intentionally separate from the main
ChatSession/ChatMessage tables.

``AgentScriptVersion`` records the script snapshot applied when an agent is
materialized via the "Apply As Agent" flow (design §7.5).
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ScriptConversation(Base):
    """A Script Builder chat session, isolated by tenant + user."""

    __tablename__ = "script_conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="New Session")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    messages: Mapped[list["ScriptMessage"]] = relationship(
        "ScriptMessage",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="ScriptMessage.created_at",
    )


class ScriptMessage(Base):
    """A single message within a Script Builder conversation (role: user/assistant)."""

    __tablename__ = "script_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("script_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    conversation: Mapped["ScriptConversation"] = relationship(
        "ScriptConversation", back_populates="messages"
    )


class AgentScriptVersion(Base):
    """A snapshot of an Agent Script applied to an agent (Apply As Agent flow)."""

    __tablename__ = "agent_script_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    folder: Mapped[str] = mapped_column(String(100), default="initial")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(50), default="script_builder")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
