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


async def _get_agent_available_tools(agent_id: str) -> list[dict]:
    from app.models.tool import Tool, AgentTool
    async with async_session() as db:
        all_tools_r = await db.execute(select(Tool).where(Tool.enabled == True))
        all_tools = all_tools_r.scalars().all()

        agent_tools_r = await db.execute(
            select(AgentTool).where(AgentTool.agent_id == agent_id)
        )
        assignments = {str(at.tool_id): at for at in agent_tools_r.scalars().all()}

        result = []
        for t in all_tools:
            tid = str(t.id)
            at = assignments.get(tid)
            enabled = at.enabled if at else t.is_default
            if enabled:
                result.append({"name": t.name, "category": t.category or "", "description": (t.description or "")[:120]})
        return result


async def _get_agent_available_skills(agent_id: str) -> list[dict]:
    from app.services.storage.factory import get_storage
    storage = get_storage()
    prefix = f"{agent_id}/"
    skills = []
    try:
        entries = await storage.list(f"{prefix}skills")
    except Exception:
        return []

    for entry in sorted(entries, key=lambda e: (not e.is_dir, e.name)):
        if entry.name.startswith("."):
            continue
        if entry.is_dir:
            skill_key = f"{prefix}skills/{entry.name}/SKILL.md"
            if not await storage.exists(skill_key):
                skill_key = f"{prefix}skills/{entry.name}/skill.md"
            if await storage.exists(skill_key):
                try:
                    content = (await storage.read(skill_key)).strip()
                    from app.services.agent_context import _parse_skill_frontmatter
                    name, desc = _parse_skill_frontmatter(content, entry.name)
                    skills.append({"name": name, "folder": entry.name, "description": desc})
                except Exception:
                    skills.append({"name": entry.name, "folder": entry.name, "description": ""})
        elif entry.name.endswith(".md"):
            stem = entry.name[:-3]
            read_key = f"{prefix}skills/{entry.name}"
            try:
                content = (await storage.read(read_key)).strip()
                from app.services.agent_context import _parse_skill_frontmatter
                name, desc = _parse_skill_frontmatter(content, stem)
                skills.append({"name": name, "folder": stem, "description": desc})
            except Exception:
                skills.append({"name": stem, "folder": stem, "description": ""})
    return skills


def _build_available_resources_section(tools: list[dict], skills: list[dict]) -> str:
    if not tools and not skills:
        return ""

    lines = ["\n## Available Tools & Skills (STRICT CONSTRAINT)"]
    lines.append("The evolved script MUST ONLY reference tools and skills from the lists below.")
    lines.append("Do NOT invent, assume, or reference any tool or skill not listed here.")
    lines.append("If functionality requires a tool/skill that is not available, note it in your explanation but do NOT add it to the script.\n")

    if tools:
        lines.append("### Installed Tools")
        for t in tools:
            desc = f" — {t['description']}" if t['description'] else ""
            lines.append(f"- `tool://{t['name']}`{desc}")
        lines.append("")

    if skills:
        lines.append("### Installed Skills")
        for s in skills:
            desc = f" — {s['description']}" if s['description'] else ""
            lines.append(f"- `skill://{s['folder']}`{desc}")
        lines.append("")

    if not tools:
        lines.append("### Installed Tools\n- (none)\n")
    if not skills:
        lines.append("### Installed Skills\n- (none)\n")

    return "\n".join(lines)


def build_evolution_prompt(
    current_script: str,
    direction: str,
    past_knowledge: list[str],
    open_feedbacks: list[dict],
    available_resources: str = "",
) -> str:
    prompt = f"""You are an expert Salesforce Agentforce Agent Script evolver. Your task is to evolve and improve an existing Agent Script based on a specific evolution direction.

## Evolution Direction
{direction}

## Current Agent Script
```ascript
{current_script}
```
"""

    prompt += available_resources

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
6. ONLY use tools and skills listed in "Available Tools & Skills" — never reference unlisted ones
7. Explain your reasoning and what you changed

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

        available_tools = await _get_agent_available_tools(agent_id)
        available_skills = await _get_agent_available_skills(agent_id)
        available_resources = _build_available_resources_section(available_tools, available_skills)

        system_prompt = build_evolution_prompt(current_script, direction, past_knowledge, open_feedbacks, available_resources)

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

        response_text = full_response.content if hasattr(full_response, 'content') else str(full_response)
        script_match = re.search(r'```ascript\s*\r?\n([\s\S]*?)```', response_text)
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

        knowledge_text = re.sub(r'```ascript[\s\S]*?```', '', response_text).strip()
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

        try:
            from app.services.storage.factory import get_storage
            storage = get_storage()
            await storage.write(f"{agent_id}/soul.md", evolved_script)
        except Exception as e:
            logger.warning(f"[Evolution] Failed to sync soul.md for {agent_id}: {e}")

        logger.info(f"[Evolution] Agent {agent_id} evolved to v{next_evolved_version}")
        return {
            "status": "success",
            "version": next_evolved_version,
            "feedbacks_addressed": len(open_feedback_ids),
        }
