"""Evolver Agent API — Feedback CRUD, Health Check, Script Version, Evolution Job management.

Ported from develop's ``app/api/evolver.py`` with one **security fix**: the LLM
API key for the health-check endpoint is now decrypted via
``app.services.llm.utils.get_model_api_key`` instead of passing ciphertext
``api_key_encrypted`` to ``create_llm_client``.

Router prefix ``/evolver``, all endpoints require ``get_current_user`` +
``get_db``. Each agent-scoped route is guarded by ``_verify_evolver_agent``
(404 missing → 403 tenant mismatch → 400 not an evolver).

The cron daemon helpers (``is_valid_cron`` / ``get_next_run_at`` /
``_run_evolution_job``) are imported lazily because their module is created in a
parallel porting task and may not be present at import time yet.
"""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user
from app.database import get_db
from app.models.agent import Agent
from app.models.evolver import AgentFeedback, AgentHealthCheck, EvolutionJob
from app.models.script_builder import AgentScriptVersion
from app.models.tool import Tool
from app.models.user import User
from app.schemas.evolver import (
    EvolutionJobCreate,
    EvolutionJobOut,
    EvolutionJobUpdate,
    FeedbackCreate,
    FeedbackOut,
    FeedbackUpdate,
    HealthCheckOut,
    ScriptVersionOut,
)
from app.services.agent_script_prompt import ANALYZE_SYSTEM_PROMPT
from app.services.evolver_evolution import _get_llm_model, _parse_analysis, run_evolution
from app.services.llm.client import LLMMessage, create_llm_client, get_max_tokens
from app.services.llm.utils import get_model_api_key
from app.services.script_runtime import parse_script
from app.services.storage_runtime.facade import get_storage_backend

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/evolver", tags=["evolver"])


async def _verify_evolver_agent(
    db: AsyncSession, agent_id: str, current_user: User
) -> Agent:
    """Load the agent and validate evolver access (404 → 403 → 400)."""
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="Access denied")
    if agent.agent_type != "evolver":
        raise HTTPException(status_code=400, detail="Agent is not an evolver type")
    return agent


def _job_to_out(job: EvolutionJob, agent_name: str | None = None) -> EvolutionJobOut:
    return EvolutionJobOut(
        id=str(job.id),
        agent_id=str(job.agent_id),
        agent_name=agent_name,
        direction=job.direction,
        cron_schedule=job.cron_schedule,
        active=job.active,
        last_run_at=job.last_run_at,
        next_run_at=job.next_run_at,
        last_run_status=job.last_run_status,
        last_run_error=job.last_run_error,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


# ──────────────────────────────────────────────────────────────────────
# 1-4. Feedback CRUD
# ──────────────────────────────────────────────────────────────────────


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
            id=str(f.id),
            agent_id=str(f.agent_id),
            category=f.category,
            content=f.content,
            status=f.status,
            created_at=f.created_at,
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
        id=str(fb.id),
        agent_id=str(fb.agent_id),
        category=fb.category,
        content=fb.content,
        status=fb.status,
        created_at=fb.created_at,
    )


@router.patch(
    "/agents/{agent_id}/feedbacks/{feedback_id}", response_model=FeedbackOut
)
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
        id=str(fb.id),
        agent_id=str(fb.agent_id),
        category=fb.category,
        content=fb.content,
        status=fb.status,
        created_at=fb.created_at,
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


# ──────────────────────────────────────────────────────────────────────
# 5-7. Health Checks
# ──────────────────────────────────────────────────────────────────────


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
            id=str(c.id),
            agent_id=str(c.agent_id),
            overall_score=c.overall_score,
            dimensions=c.dimensions,
            strengths=c.strengths,
            suggestions=c.suggestions,
            script_version=c.script_version,
            created_at=c.created_at,
        )
        for c in checks
    ]


@router.post(
    "/agents/{agent_id}/health-checks", response_model=HealthCheckOut, status_code=201
)
async def trigger_health_check(
    agent_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    agent = await _verify_evolver_agent(db, agent_id, current_user)

    # Latest script: evolved preferred, else initial.
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
    if not llm_model:
        raise HTTPException(status_code=503, detail="No LLM model available")

    # SECURITY FIX: decrypt the API key — never pass api_key_encrypted.
    api_key = get_model_api_key(llm_model)
    client = create_llm_client(
        llm_model.provider,
        api_key,
        llm_model.model,
        llm_model.base_url,
        float(llm_model.request_timeout or 120),
    )

    try:
        messages = [
            LLMMessage(role="system", content=ANALYZE_SYSTEM_PROMPT),
            LLMMessage(
                role="user",
                content=f"Please analyze this Agent Script:\n\n```ascript\n{script.content}\n```",
            ),
        ]
        max_tokens = llm_model.max_output_tokens or get_max_tokens(
            llm_model.provider, llm_model.model
        )
        response = await client.complete(
            messages=messages,
            temperature=0.3,
            max_tokens=max_tokens,
        )
    finally:
        await client.close()

    analysis = _parse_analysis(response.content)
    version_label = (
        f"v{script.version}" if script.folder == "evolved" else f"v{script.version}-initial"
    )

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
        id=str(check.id),
        agent_id=str(check.agent_id),
        overall_score=check.overall_score,
        dimensions=check.dimensions,
        strengths=check.strengths,
        suggestions=check.suggestions,
        script_version=check.script_version,
        created_at=check.created_at,
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


# ──────────────────────────────────────────────────────────────────────
# 8-9. Script Versions
# ──────────────────────────────────────────────────────────────────────


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
            id=str(v.id),
            agent_id=str(v.agent_id),
            version=v.version,
            folder=v.folder,
            content=v.content,
            source=v.source,
            created_at=v.created_at,
        )
        for v in versions
    ]


@router.post(
    "/agents/{agent_id}/script-versions",
    response_model=ScriptVersionOut,
    status_code=201,
)
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
        select(func.coalesce(func.max(AgentScriptVersion.version), 0)).where(
            AgentScriptVersion.agent_id == agent_id,
            AgentScriptVersion.folder == folder,
        )
    )
    next_version = max_ver.scalar() + 1

    content = body.get("content", "")

    # Resource validation only for runnable folders with non-empty content.
    if folder in ("initial", "evolved") and content.strip():
        parsed = parse_script(content)
        problems: list[str] = []
        missing_skills: list[dict] = []
        missing_tools: list[dict] = []

        storage = get_storage_backend()

        for topic_name, topic in parsed.topics.items():
            for action_name, action in topic.actions.items():
                target = action.target or ""
                if target.startswith("tool://"):
                    tool_name = target[len("tool://") :].strip()
                    t = await db.execute(select(Tool).where(Tool.name == tool_name))
                    if not t.scalar_one_or_none():
                        problems.append(
                            f"Action '{action_name}' references tool://{tool_name} "
                            f"which does not exist in tools table"
                        )
                        missing_tools.append(
                            {"action": action_name, "tool_name": tool_name}
                        )
                elif target.startswith("skill://"):
                    skill_name = target[len("skill://") :].strip()
                    try:
                        skill_key = f"{agent_id}/skills/{skill_name}/SKILL.md"
                        skill_found = await storage.exists(skill_key)
                        if not skill_found:
                            skill_key_lower = (
                                f"{agent_id}/skills/{skill_name}/skill.md"
                            )
                            skill_found = await storage.exists(skill_key_lower)
                        if not skill_found:
                            problems.append(
                                f"Action '{action_name}' references skill://{skill_name} "
                                f"but no SKILL.md found in agent workspace"
                            )
                            missing_skills.append(
                                {"action": action_name, "folder_name": skill_name}
                            )
                    except Exception as e:  # noqa: BLE001
                        logger.warning(
                            f"[Evolver] Skill validation skipped for {skill_name}: {e}"
                        )

        if problems:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": f"Script references unavailable resources: {'; '.join(problems)}",
                    "missing_skills": missing_skills,
                    "missing_tools": missing_tools,
                },
            )

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
            storage = get_storage_backend()
            await storage.write_text(f"{agent_id}/soul.md", content)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[Evolver] Failed to sync soul.md for {agent_id}: {e}")

    return ScriptVersionOut(
        id=str(sv.id),
        agent_id=str(sv.agent_id),
        version=sv.version,
        folder=sv.folder,
        content=sv.content,
        source=sv.source,
        created_at=sv.created_at,
    )


# ──────────────────────────────────────────────────────────────────────
# 10. Trigger Evolution
# ──────────────────────────────────────────────────────────────────────


@router.post("/agents/{agent_id}/evolve", status_code=200)
async def trigger_evolution(
    agent_id: str,
    body: dict | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    agent = await _verify_evolver_agent(db, agent_id, current_user)
    direction = (body or {}).get(
        "direction", "Improve overall quality and user experience"
    )

    result = await run_evolution(agent_id, agent.tenant_id, direction)
    return result


# ──────────────────────────────────────────────────────────────────────
# 11-15. Evolution Jobs
# ──────────────────────────────────────────────────────────────────────


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
    from app.services.evolution_job_daemon import get_next_run_at, is_valid_cron

    agent = await _verify_evolver_agent(db, agent_id, current_user)

    if not is_valid_cron(body.cron_schedule):
        raise HTTPException(status_code=400, detail="Invalid cron schedule expression")

    job = EvolutionJob(
        agent_id=agent_id,
        direction=body.direction,
        cron_schedule=body.cron_schedule,
        active=True,
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
        from app.services.evolution_job_daemon import get_next_run_at, is_valid_cron

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
    import asyncio

    from app.services.evolution_job_daemon import _run_evolution_job

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

    # Fire-and-forget; return immediately.
    asyncio.create_task(
        _run_evolution_job(job.id, job.agent_id, agent.tenant_id, job.direction)
    )

    return {"message": "Evolution job triggered", "job_id": str(job.id)}
