"""Evolver Agent API — Feedback CRUD, Health Check, Script Version, Evolution Job management."""

import json
import logging
import re
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select, desc, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.database import get_db, async_session
from app.models.agent import Agent
from app.models.evolver import AgentFeedback, AgentHealthCheck, AgentScriptVersion, EvolutionJob
from app.models.llm import LLMModel
from app.models.user import User
from app.services.llm_client import create_llm_client, LLMMessage, get_max_tokens
from app.services.agent_script_prompt import ANALYZE_SYSTEM_PROMPT

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/evolver", tags=["evolver"])


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

class HealthCheckOut(BaseModel):
    id: str
    agent_id: str
    overall_score: int
    dimensions: list | None = None
    strengths: list | None = None
    suggestions: list | None = None
    script_version: str | None = None
    created_at: datetime

class ScriptVersionOut(BaseModel):
    id: str
    agent_id: str
    version: int
    folder: str
    content: str
    source: str | None = None
    created_at: datetime


async def _verify_evolver_agent(db: AsyncSession, agent_id: str, current_user: User | None = None) -> Agent:
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if current_user and agent.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="Access denied")
    if agent.agent_type != "evolver":
        raise HTTPException(status_code=400, detail="Agent is not an evolver type")
    return agent


async def _get_llm_model(db: AsyncSession, tenant_id) -> LLMModel:
    result = await db.execute(
        select(LLMModel)
        .where(LLMModel.tenant_id == tenant_id, LLMModel.enabled == True)
        .order_by(LLMModel.created_at)
        .limit(1)
    )
    model = result.scalar_one_or_none()
    if not model:
        result2 = await db.execute(
            select(LLMModel)
            .where(LLMModel.tenant_id.is_(None), LLMModel.enabled == True)
            .order_by(LLMModel.created_at)
            .limit(1)
        )
        model = result2.scalar_one_or_none()
    if not model:
        raise HTTPException(status_code=503, detail="No LLM model available")
    return model


@router.get("/agents/{agent_id}/feedbacks", response_model=list[FeedbackOut])
async def list_feedbacks(
    agent_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _verify_evolver_agent(db, agent_id, current_user)
    result = await db.execute(
        select(AgentFeedback)
        .where(AgentFeedback.agent_id == agent_id)
        .order_by(desc(AgentFeedback.created_at))
    )
    feedbacks = result.scalars().all()
    return [
        FeedbackOut(
            id=str(f.id), agent_id=str(f.agent_id), category=f.category,
            content=f.content, status=f.status, created_at=f.created_at,
        )
        for f in feedbacks
    ]


@router.post("/agents/{agent_id}/feedbacks", response_model=FeedbackOut, status_code=201)
async def create_feedback(
    agent_id: str,
    body: FeedbackCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _verify_evolver_agent(db, agent_id, current_user)
    fb = AgentFeedback(
        agent_id=agent_id,
        category=body.category,
        content=body.content,
        status="open",
        created_by=current_user.id,
    )
    db.add(fb)
    await db.commit()
    await db.refresh(fb)
    return FeedbackOut(
        id=str(fb.id), agent_id=str(fb.agent_id), category=fb.category,
        content=fb.content, status=fb.status, created_at=fb.created_at,
    )


@router.patch("/agents/{agent_id}/feedbacks/{feedback_id}", response_model=FeedbackOut)
async def update_feedback(
    agent_id: str,
    feedback_id: str,
    body: FeedbackUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(AgentFeedback).where(
            AgentFeedback.id == uuid.UUID(feedback_id),
            AgentFeedback.agent_id == agent_id,
        )
    )
    fb = result.scalar_one_or_none()
    if not fb:
        raise HTTPException(status_code=404, detail="Feedback not found")
    if body.status and body.status in ("open", "addressed", "dismissed"):
        fb.status = body.status
    if body.content is not None:
        fb.content = body.content
    await db.commit()
    await db.refresh(fb)
    return FeedbackOut(
        id=str(fb.id), agent_id=str(fb.agent_id), category=fb.category,
        content=fb.content, status=fb.status, created_at=fb.created_at,
    )


@router.delete("/agents/{agent_id}/feedbacks/{feedback_id}", status_code=204)
async def delete_feedback(
    agent_id: str,
    feedback_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(AgentFeedback).where(
            AgentFeedback.id == uuid.UUID(feedback_id),
            AgentFeedback.agent_id == agent_id,
        )
    )
    fb = result.scalar_one_or_none()
    if not fb:
        raise HTTPException(status_code=404, detail="Feedback not found")
    await db.delete(fb)
    await db.commit()


@router.get("/agents/{agent_id}/health-checks", response_model=list[HealthCheckOut])
async def list_health_checks(
    agent_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _verify_evolver_agent(db, agent_id, current_user)
    result = await db.execute(
        select(AgentHealthCheck)
        .where(AgentHealthCheck.agent_id == agent_id)
        .order_by(desc(AgentHealthCheck.created_at))
        .limit(20)
    )
    checks = result.scalars().all()
    return [
        HealthCheckOut(
            id=str(c.id), agent_id=str(c.agent_id), overall_score=c.overall_score,
            dimensions=c.dimensions, strengths=c.strengths,
            suggestions=c.suggestions, script_version=c.script_version,
            created_at=c.created_at,
        )
        for c in checks
    ]


@router.post("/agents/{agent_id}/health-checks", response_model=HealthCheckOut, status_code=201)
async def trigger_health_check(
    agent_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    agent = await _verify_evolver_agent(db, agent_id, current_user)

    latest_script = await db.execute(
        select(AgentScriptVersion)
        .where(AgentScriptVersion.agent_id == agent_id)
        .where(AgentScriptVersion.folder.in_(["evolved", "initial"]))
        .order_by(
            desc(AgentScriptVersion.folder == "evolved"),
            desc(AgentScriptVersion.version),
        )
        .limit(1)
    )
    script = latest_script.scalar_one_or_none()
    if not script:
        raise HTTPException(status_code=400, detail="No script found for this agent")

    llm_model = await _get_llm_model(db, agent.tenant_id)
    client = create_llm_client(
        llm_model.provider, llm_model.api_key_encrypted,
        llm_model.model, llm_model.base_url,
        float(llm_model.request_timeout or 120),
    )

    try:
        messages = [
            LLMMessage(role="system", content=ANALYZE_SYSTEM_PROMPT),
            LLMMessage(role="user", content=f"Please analyze this Agent Script:\n\n```ascript\n{script.content}\n```"),
        ]
        max_tokens = llm_model.max_output_tokens or get_max_tokens(llm_model.provider, llm_model.model)
        response = await client.complete(
            messages=messages,
            temperature=0.3,
            max_tokens=max_tokens,
        )
    finally:
        await client.close()

    analysis = _parse_analysis(response.content)
    version_label = f"v{script.version}" if script.folder == "evolved" else f"v{script.version}-initial"

    check = AgentHealthCheck(
        agent_id=agent_id,
        overall_score=analysis.get("overall_score", 0),
        dimensions=analysis.get("dimensions", []),
        strengths=analysis.get("strengths", []),
        suggestions=analysis.get("suggestions", []),
        script_version=version_label,
    )
    db.add(check)
    await db.commit()
    await db.refresh(check)

    return HealthCheckOut(
        id=str(check.id), agent_id=str(check.agent_id),
        overall_score=check.overall_score, dimensions=check.dimensions,
        strengths=check.strengths, suggestions=check.suggestions,
        script_version=check.script_version, created_at=check.created_at,
    )


@router.delete("/agents/{agent_id}/health-checks/{check_id}", status_code=204)
async def delete_health_check(
    agent_id: str,
    check_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(AgentHealthCheck).where(
            AgentHealthCheck.id == uuid.UUID(check_id),
            AgentHealthCheck.agent_id == agent_id,
        )
    )
    check = result.scalar_one_or_none()
    if not check:
        raise HTTPException(status_code=404, detail="Health check not found")
    await db.delete(check)
    await db.commit()


def _parse_analysis(text: str) -> dict:
    json_match = re.search(r'```(?:json)?\s*\n([\s\S]*?)```', text)
    raw = None
    if json_match:
        try:
            raw = json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass
    if raw is None:
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            return {"overall_score": 0, "dimensions": [], "strengths": [], "suggestions": [text[:500]]}
    if "overallScore" in raw and "overall_score" not in raw:
        raw["overall_score"] = raw.pop("overallScore")
    return raw


@router.get("/agents/{agent_id}/script-versions", response_model=list[ScriptVersionOut])
async def list_script_versions(
    agent_id: str,
    folder: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _verify_evolver_agent(db, agent_id, current_user)
    q = select(AgentScriptVersion).where(AgentScriptVersion.agent_id == agent_id)
    if folder:
        q = q.where(AgentScriptVersion.folder == folder)
    q = q.order_by(AgentScriptVersion.folder, desc(AgentScriptVersion.version))
    result = await db.execute(q)
    versions = result.scalars().all()
    return [
        ScriptVersionOut(
            id=str(v.id), agent_id=str(v.agent_id), version=v.version,
            folder=v.folder, content=v.content, source=v.source,
            created_at=v.created_at,
        )
        for v in versions
    ]


@router.post("/agents/{agent_id}/script-versions", response_model=ScriptVersionOut, status_code=201)
async def create_script_version(
    agent_id: str,
    body: dict,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _verify_evolver_agent(db, agent_id, current_user)
    folder = body.get("folder", "initial")
    if folder not in ("initial", "evolved", "evolution_knowledge"):
        raise HTTPException(status_code=400, detail="Invalid folder")

    max_ver = await db.execute(
        select(func.coalesce(func.max(AgentScriptVersion.version), 0))
        .where(AgentScriptVersion.agent_id == agent_id, AgentScriptVersion.folder == folder)
    )
    next_version = max_ver.scalar() + 1

    content = body.get("content", "")
    sv = AgentScriptVersion(
        agent_id=agent_id,
        version=next_version,
        folder=folder,
        content=content,
        source=body.get("source", "manual"),
    )
    db.add(sv)
    await db.commit()
    await db.refresh(sv)

    if folder in ("initial", "evolved"):
        try:
            from app.services.storage.factory import get_storage
            storage = get_storage()
            await storage.write(f"{agent_id}/soul.md", content)
        except Exception as e:
            logger.warning(f"[Evolver] Failed to sync soul.md for {agent_id}: {e}")

    return ScriptVersionOut(
        id=str(sv.id), agent_id=str(sv.agent_id), version=sv.version,
        folder=sv.folder, content=sv.content, source=sv.source,
        created_at=sv.created_at,
    )


@router.post("/agents/{agent_id}/evolve", status_code=200)
async def trigger_evolution(
    agent_id: str,
    body: dict | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    agent = await _verify_evolver_agent(db, agent_id, current_user)
    direction = (body or {}).get("direction", "Improve overall quality and user experience")

    from app.services.evolver_evolution import run_evolution
    result = await run_evolution(agent_id, agent.tenant_id, direction)
    return result


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


def _job_to_out(job: EvolutionJob, agent_name: str | None = None) -> EvolutionJobOut:
    return EvolutionJobOut(
        id=str(job.id), agent_id=str(job.agent_id), agent_name=agent_name,
        direction=job.direction, cron_schedule=job.cron_schedule,
        active=job.active, last_run_at=job.last_run_at, next_run_at=job.next_run_at,
        last_run_status=job.last_run_status, last_run_error=job.last_run_error,
        created_at=job.created_at, updated_at=job.updated_at,
    )


@router.get("/agents/{agent_id}/jobs", response_model=list[EvolutionJobOut])
async def list_evolution_jobs(
    agent_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    agent = await _verify_evolver_agent(db, agent_id, current_user)
    result = await db.execute(
        select(EvolutionJob)
        .where(EvolutionJob.agent_id == agent_id)
        .order_by(desc(EvolutionJob.created_at))
    )
    jobs = result.scalars().all()
    return [_job_to_out(j, agent.name) for j in jobs]


@router.post("/agents/{agent_id}/jobs", response_model=EvolutionJobOut, status_code=201)
async def create_evolution_job(
    agent_id: str,
    body: EvolutionJobCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    agent = await _verify_evolver_agent(db, agent_id, current_user)

    from app.services.evolution_job_daemon import is_valid_cron, get_next_run_at
    if not is_valid_cron(body.cron_schedule):
        raise HTTPException(status_code=400, detail="Invalid cron schedule expression")

    job = EvolutionJob(
        agent_id=agent_id,
        direction=body.direction,
        cron_schedule=body.cron_schedule,
        next_run_at=get_next_run_at(body.cron_schedule),
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return _job_to_out(job, agent.name)


@router.patch("/agents/{agent_id}/jobs/{job_id}", response_model=EvolutionJobOut)
async def update_evolution_job(
    agent_id: str,
    job_id: str,
    body: EvolutionJobUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    agent = await _verify_evolver_agent(db, agent_id, current_user)
    try:
        parsed_job_id = uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job ID format")
    result = await db.execute(
        select(EvolutionJob).where(
            EvolutionJob.id == parsed_job_id,
            EvolutionJob.agent_id == agent_id,
        )
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if body.direction is not None:
        job.direction = body.direction
    if body.active is not None:
        job.active = body.active
    if body.cron_schedule is not None:
        from app.services.evolution_job_daemon import is_valid_cron, get_next_run_at
        if not is_valid_cron(body.cron_schedule):
            raise HTTPException(status_code=400, detail="Invalid cron schedule expression")
        job.cron_schedule = body.cron_schedule
        job.next_run_at = get_next_run_at(body.cron_schedule)

    await db.commit()
    await db.refresh(job)
    return _job_to_out(job, agent.name)


@router.delete("/agents/{agent_id}/jobs/{job_id}", status_code=204)
async def delete_evolution_job(
    agent_id: str,
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _verify_evolver_agent(db, agent_id, current_user)
    try:
        parsed_job_id = uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job ID format")
    result = await db.execute(
        select(EvolutionJob).where(
            EvolutionJob.id == parsed_job_id,
            EvolutionJob.agent_id == agent_id,
        )
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    await db.delete(job)
    await db.commit()


@router.post("/agents/{agent_id}/jobs/{job_id}/run", status_code=200)
async def trigger_evolution_job(
    agent_id: str,
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    agent = await _verify_evolver_agent(db, agent_id, current_user)
    try:
        parsed_job_id = uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job ID format")
    result = await db.execute(
        select(EvolutionJob).where(
            EvolutionJob.id == parsed_job_id,
            EvolutionJob.agent_id == agent_id,
        )
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    import asyncio
    from app.services.evolution_job_daemon import _run_evolution_job
    asyncio.create_task(_run_evolution_job(job.id, job.agent_id, agent.tenant_id, job.direction))

    return {"message": "Evolution job triggered", "job_id": str(job.id)}
