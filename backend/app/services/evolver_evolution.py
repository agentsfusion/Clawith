"""Evolution service for Evolver agents — ported from ClawEvolver's evolution-daemon.ts."""

import json
import logging
import re

from sqlalchemy import select, desc, func, and_

from app.database import async_session
from app.models.evolver import AgentFeedback, AgentScriptVersion
from app.models.llm import LLMModel
from app.services.llm_client import create_llm_client, LLMMessage, get_max_tokens

logger = logging.getLogger(__name__)


def build_evolution_prompt(
    current_script: str,
    direction: str,
    past_knowledge: list[str],
    open_feedbacks: list[dict],
) -> str:
    prompt = f"""You are an expert Salesforce Agentforce Agent Script evolver. Your task is to evolve and improve an existing Agent Script based on a specific evolution direction.

## Evolution Direction
{direction}

## Current Agent Script
```ascript
{current_script}
```
"""

    if past_knowledge:
        prompt += "\n## Past Evolution Knowledge & Learnings\n"
        prompt += "The following are accumulated insights from previous evolution cycles. You MUST review all of them before making changes, to build on prior learnings and avoid repeating past mistakes:\n\n"
        for i, k in enumerate(past_knowledge):
            prompt += f"### Learning {i + 1}\n{k}\n\n"

    if open_feedbacks:
        prompt += "\n## User Feedback (Must Address)\n"
        prompt += "The following feedback items have been manually submitted and MUST be considered in this evolution. Address each one and explain how you incorporated it:\n\n"
        for i, f in enumerate(open_feedbacks):
            prompt += f"{i + 1}. [{f['category'].upper()}] {f['content']}\n"

    prompt += f"""
## Your Task
1. First, review all past evolution knowledge to understand what has been tried and learned
2. Review and address all user feedback items
3. Analyze the current script against the evolution direction: "{direction}"
4. Make targeted improvements that align with the evolution direction
5. Preserve all existing functionality unless it conflicts with the direction
6. Explain your reasoning and what you changed

## Output Format
First, explain what you analyzed from past knowledge and what improvements you're making and why.
Then output the complete improved script wrapped in:
```ascript
[complete improved script here]
```

Be specific about what changed and why. This explanation will be saved as evolution knowledge for future cycles."""

    return prompt


async def run_evolution(agent_id: str, tenant_id, direction: str) -> dict:
    async with async_session() as db:
        latest_evolved = await db.execute(
            select(AgentScriptVersion)
            .where(
                AgentScriptVersion.agent_id == agent_id,
                AgentScriptVersion.folder == "evolved",
            )
            .order_by(desc(AgentScriptVersion.version))
            .limit(1)
        )
        evolved = latest_evolved.scalar_one_or_none()

        if not evolved:
            latest_initial = await db.execute(
                select(AgentScriptVersion)
                .where(
                    AgentScriptVersion.agent_id == agent_id,
                    AgentScriptVersion.folder == "initial",
                )
                .order_by(desc(AgentScriptVersion.version))
                .limit(1)
            )
            evolved = latest_initial.scalar_one_or_none()

        if not evolved:
            return {"status": "error", "detail": "No script found to evolve"}

        current_script = evolved.content

        knowledge_result = await db.execute(
            select(AgentScriptVersion)
            .where(
                AgentScriptVersion.agent_id == agent_id,
                AgentScriptVersion.folder == "evolution_knowledge",
            )
            .order_by(AgentScriptVersion.version)
        )
        past_knowledge = [k.content for k in knowledge_result.scalars().all()]

        feedback_result = await db.execute(
            select(AgentFeedback)
            .where(
                AgentFeedback.agent_id == agent_id,
                AgentFeedback.status == "open",
            )
        )
        open_feedbacks = [
            {"category": f.category, "content": f.content}
            for f in feedback_result.scalars().all()
        ]
        open_feedback_ids = [f.id for f in feedback_result.scalars().all()]

        feedback_result2 = await db.execute(
            select(AgentFeedback)
            .where(
                AgentFeedback.agent_id == agent_id,
                AgentFeedback.status == "open",
            )
        )
        open_feedback_rows = feedback_result2.scalars().all()
        open_feedback_ids = [f.id for f in open_feedback_rows]

        system_prompt = build_evolution_prompt(current_script, direction, past_knowledge, open_feedbacks)

        llm_model_result = await db.execute(
            select(LLMModel)
            .where(LLMModel.tenant_id == tenant_id, LLMModel.enabled == True)
            .order_by(LLMModel.created_at)
            .limit(1)
        )
        llm_model = llm_model_result.scalar_one_or_none()
        if not llm_model:
            llm_model_result2 = await db.execute(
                select(LLMModel)
                .where(LLMModel.tenant_id.is_(None), LLMModel.enabled == True)
                .order_by(LLMModel.created_at)
                .limit(1)
            )
            llm_model = llm_model_result2.scalar_one_or_none()
        if not llm_model:
            return {"status": "error", "detail": "No LLM model available"}

        client = create_llm_client(
            llm_model.provider, llm_model.api_key_encrypted,
            llm_model.model, llm_model.base_url,
            float(llm_model.request_timeout or 120),
        )

        try:
            messages = [
                LLMMessage(role="system", content=system_prompt),
                LLMMessage(role="user", content=f"Please evolve this agent script with focus on: {direction}"),
            ]
            max_tokens = llm_model.max_output_tokens or get_max_tokens(llm_model.provider, llm_model.model)
            full_response = await client.complete(
                messages=messages,
                temperature=0.7,
                max_tokens=max_tokens,
            )
        finally:
            await client.close()

        script_match = re.search(r'```ascript\s*\r?\n([\s\S]*?)```', full_response)
        evolved_script = script_match.group(1).strip() if script_match else None

        if not evolved_script:
            return {"status": "error", "detail": "AI response did not contain a valid ascript block"}

        max_evolved_ver = await db.execute(
            select(func.coalesce(func.max(AgentScriptVersion.version), 0))
            .where(
                AgentScriptVersion.agent_id == agent_id,
                AgentScriptVersion.folder == "evolved",
            )
        )
        next_evolved_version = max_evolved_ver.scalar() + 1

        db.add(AgentScriptVersion(
            agent_id=agent_id,
            version=next_evolved_version,
            folder="evolved",
            content=evolved_script,
            source=f"evolution-{direction[:50]}",
        ))

        knowledge_text = re.sub(r'```ascript[\s\S]*?```', '', full_response).strip()
        if len(knowledge_text) > 20:
            max_knowledge_ver = await db.execute(
                select(func.coalesce(func.max(AgentScriptVersion.version), 0))
                .where(
                    AgentScriptVersion.agent_id == agent_id,
                    AgentScriptVersion.folder == "evolution_knowledge",
                )
            )
            next_knowledge_version = max_knowledge_ver.scalar() + 1
            db.add(AgentScriptVersion(
                agent_id=agent_id,
                version=next_knowledge_version,
                folder="evolution_knowledge",
                content=knowledge_text,
                source=f"evolution-{direction[:50]}",
            ))

        if open_feedback_ids:
            for fid in open_feedback_ids:
                fb_result = await db.execute(
                    select(AgentFeedback).where(AgentFeedback.id == fid)
                )
                fb = fb_result.scalar_one_or_none()
                if fb:
                    fb.status = "addressed"

        await db.commit()

        logger.info(f"[Evolution] Agent {agent_id} evolved to v{next_evolved_version}")
        return {
            "status": "success",
            "version": next_evolved_version,
            "feedbacks_addressed": len(open_feedback_ids),
        }
