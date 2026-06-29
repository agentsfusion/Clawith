"""Evolution Job Daemon — checks and runs due cron-scheduled evolution jobs."""

import asyncio
import logging
from datetime import datetime

from croniter import croniter
from sqlalchemy import select, and_, update

from app.database import async_session
from app.models.evolver import EvolutionJob
from app.models.agent import Agent

logger = logging.getLogger(__name__)


def get_next_run_at(cron_expr: str) -> datetime:
    try:
        cron = croniter(cron_expr, datetime.utcnow())
        return cron.get_next(datetime)
    except Exception:
        from datetime import timedelta
        return datetime.utcnow() + timedelta(days=1)


def is_valid_cron(cron_expr: str) -> bool:
    try:
        croniter(cron_expr)
        return True
    except Exception:
        return False


async def _run_evolution_job(job_id, agent_id, tenant_id, direction: str):
    logger.info(f"[EvolutionJobDaemon] Running job {job_id} for agent {agent_id} (direction: {direction})")

    try:
        from app.services.evolver_evolution import run_evolution
        result = await run_evolution(str(agent_id), tenant_id, direction)

        async with async_session() as db:
            job_result = await db.execute(select(EvolutionJob).where(EvolutionJob.id == job_id))
            job = job_result.scalar_one_or_none()
            if job:
                job.last_run_at = datetime.utcnow()
                if result.get("status") == "success":
                    job.last_run_status = "success"
                    job.last_run_error = ""
                else:
                    job.last_run_status = "error"
                    job.last_run_error = result.get("detail", "Unknown error")
                job.next_run_at = get_next_run_at(job.cron_schedule)
                await db.commit()

        logger.info(f"[EvolutionJobDaemon] Job {job_id} completed: {result.get('status')}")
    except Exception as e:
        error_msg = str(e)
        logger.error(f"[EvolutionJobDaemon] Job {job_id} failed: {error_msg}")
        async with async_session() as db:
            job_result = await db.execute(select(EvolutionJob).where(EvolutionJob.id == job_id))
            job = job_result.scalar_one_or_none()
            if job:
                job.last_run_at = datetime.utcnow()
                job.last_run_status = "error"
                job.last_run_error = error_msg
                job.next_run_at = get_next_run_at(job.cron_schedule)
                await db.commit()


async def check_and_run_due_jobs():
    now = datetime.utcnow()
    async with async_session() as db:
        result = await db.execute(
            select(EvolutionJob, Agent)
            .join(Agent, EvolutionJob.agent_id == Agent.id)
            .where(and_(
                EvolutionJob.active == True,
                EvolutionJob.next_run_at <= now,
                EvolutionJob.last_run_status != "running",
            ))
        )
        due_jobs = result.all()

        for job, agent in due_jobs:
            claimed = await db.execute(
                update(EvolutionJob)
                .where(
                    EvolutionJob.id == job.id,
                    EvolutionJob.last_run_status != "running",
                )
                .values(last_run_status="running", last_run_error="")
            )
            await db.commit()
            if claimed.rowcount > 0:
                asyncio.create_task(
                    _run_evolution_job(job.id, job.agent_id, agent.tenant_id, job.direction)
                )
