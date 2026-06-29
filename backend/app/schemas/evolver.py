"""Pydantic request/response schemas for the Agent Evolver feature (design §5)."""

from datetime import datetime

from pydantic import BaseModel, Field


class FeedbackCreate(BaseModel):
    category: str = Field(default="general", max_length=30)
    content: str = Field(min_length=1, max_length=2000)


class FeedbackUpdate(BaseModel):
    status: str | None = None
    content: str | None = None


class FeedbackOut(BaseModel):
    id: str
    agent_id: str
    category: str
    content: str
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class HealthCheckOut(BaseModel):
    id: str
    agent_id: str
    overall_score: int
    dimensions: list | None = None
    strengths: list | None = None
    suggestions: list | None = None
    script_version: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ScriptVersionOut(BaseModel):
    id: str
    agent_id: str
    version: int
    folder: str
    content: str
    source: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class EvolutionJobCreate(BaseModel):
    direction: str = Field(min_length=1, max_length=500)
    cron_schedule: str = Field(default="0 0 * * *", min_length=1, max_length=100)


class EvolutionJobUpdate(BaseModel):
    direction: str | None = None
    cron_schedule: str | None = None
    active: bool | None = None


class EvolutionJobOut(BaseModel):
    id: str
    agent_id: str
    agent_name: str | None = None
    direction: str
    cron_schedule: str
    active: bool
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    last_run_status: str | None = None
    last_run_error: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
