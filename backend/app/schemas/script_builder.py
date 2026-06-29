"""Pydantic request/response schemas for the Script Builder feature (design §5)."""

from datetime import datetime

from pydantic import BaseModel, Field


class ScriptConversationCreate(BaseModel):
    """Request body for creating a Script Builder conversation."""

    title: str = Field(default="New Session", min_length=1, max_length=200)


class ScriptConversationOut(BaseModel):
    """Serialized Script Builder conversation (camelCase to match the frontend)."""

    id: int
    title: str
    createdAt: datetime = Field(
        validation_alias="created_at", serialization_alias="createdAt"
    )

    model_config = {"from_attributes": True, "populate_by_name": True}


class ScriptMessageSend(BaseModel):
    """Request body for sending a user message to a conversation."""

    content: str = Field(min_length=1, max_length=50000)


class ScriptMessageOut(BaseModel):
    """Serialized Script Builder message (camelCase to match the frontend)."""

    id: int
    role: str
    content: str
    createdAt: datetime = Field(
        validation_alias="created_at", serialization_alias="createdAt"
    )

    model_config = {"from_attributes": True, "populate_by_name": True}


class ScriptAnalyzeRequest(BaseModel):
    """Request body for the Analyze endpoint."""

    script: str = Field(min_length=1, max_length=100000)


class ApplyAsAgentRequest(BaseModel):
    """Request body for the Apply As Agent endpoint."""

    script: str = Field(min_length=10)
    name: str | None = None
