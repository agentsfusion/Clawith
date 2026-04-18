"""Evolution service for Evolver agents — ported from ClawEvolver's evolution-daemon.ts."""

import json
import logging
import re

from sqlalchemy import select, desc, func, and_

from app.database import async_session
from app.models.evolver import AgentFeedback, AgentScriptVersion
from app.models.llm import LLMModel
from app.services.llm.client import create_llm_client, LLMMessage, get_max_tokens

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


_TOOL_REF_RE = re.compile(r'tool://([A-Za-z0-9_\-]+)')
_SKILL_REF_RE = re.compile(r'skill://([A-Za-z0-9_\-]+)')


def _extract_resource_refs(script: str) -> tuple[set[str], set[str]]:
    """Return (tool_names, skill_folders) referenced in the script."""
    return (
        set(_TOOL_REF_RE.findall(script or "")),
        set(_SKILL_REF_RE.findall(script or "")),
    )


def _validate_resource_refs(
    script: str,
    available_tools: list[dict],
    available_skills: list[dict],
) -> list[str]:
    """Return a list of human-readable problems. Empty list = valid."""
    tool_refs, skill_refs = _extract_resource_refs(script)
    avail_tool_names = {t["name"] for t in available_tools}
    avail_skill_folders = {s["folder"] for s in available_skills}

    problems: list[str] = []
    for ref in sorted(tool_refs - avail_tool_names):
        problems.append(
            f"`tool://{ref}` is not installed for this agent. "
            f"Either install the tool or use one of the available tools."
        )
    for ref in sorted(skill_refs - avail_skill_folders):
        problems.append(
            f"`skill://{ref}` is not installed for this agent. "
            f"Either install the skill or use a different action target."
        )
    return problems


def _build_available_resources_section(tools: list[dict], skills: list[dict]) -> str:
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
6. ONLY use tools and skills listed in "Available Tools & Skills" — never reference unlisted ones.
   The output is **strictly validated**: any `tool://X` or `skill://Y` reference that is not in the
   available lists will cause this evolution to be REJECTED and discarded.
7. If a previously-used `skill://X` reference points to a skill that is no longer in the available
   list, you MUST replace it with a valid `tool://` or `skill://` from the lists above (or remove
   the action entirely). Prefer `tool://` over `skill://` when both can perform the same job.
8. Explain your reasoning and what you changed

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

        # ── HARD CONSTRAINT: every tool://X / skill://Y in the evolved script
        #    must exist in this agent's available resources. If not, reject the
        #    evolution and persist a knowledge note so the next cycle learns.
        problems = _validate_resource_refs(evolved_script, available_tools, available_skills)
        if problems:
            problem_text = "\n".join(f"- {p}" for p in problems)
            logger.warning(
                f"[Evolution] Rejecting evolved script for agent {agent_id}: "
                f"invalid resource references:\n{problem_text}"
            )
            # Save a learning so the LLM sees why its previous attempt was rejected.
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
                content=(
                    "PREVIOUS EVOLUTION ATTEMPT REJECTED — invalid resource references.\n"
                    "The script generated in the prior cycle referenced tools or skills that\n"
                    "are NOT installed for this agent. Future evolutions MUST only reference\n"
                    "items present in the 'Available Tools & Skills' lists.\n\n"
                    "Specific problems:\n" + problem_text
                ),
                source=f"evolution-rejected-{direction[:50]}",
            ))
            await db.commit()
            return {
                "status": "rejected",
                "detail": "evolved script references unavailable tools/skills",
                "problems": problems,
            }

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

        # Invalidate cached parsed scripts so the new version takes effect immediately
        try:
            from app.services.evolver_runtime import invalidate_parse_cache
            invalidate_parse_cache()
        except Exception:
            pass

        logger.info(f"[Evolution] Agent {agent_id} evolved to v{next_evolved_version}")
        return {
            "status": "success",
            "version": next_evolved_version,
            "feedbacks_addressed": len(open_feedback_ids),
        }
