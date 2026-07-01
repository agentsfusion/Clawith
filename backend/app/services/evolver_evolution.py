"""Evolution service for Evolver agents — self-improvement loop + health analysis.

Ported from develop's ``evolver_evolution.py`` (which itself was a port of
ClawEvolver's evolution-daemon.ts). Faithfully preserves names and the 9-step
flow described in ``docs/Agent_Evolver_Tabs_Design.md`` §7.1, with one
**security fix** vs develop: the LLM API key is decrypted via
``get_model_api_key`` instead of passing ciphertext ``api_key_encrypted`` to
``create_llm_client``.
"""

import json
import logging
import re

from sqlalchemy import desc, func, select

from app.database import async_session
from app.models.evolver import AgentFeedback, AgentHealthCheck, EvolutionJob
from app.models.llm import LLMModel
from app.models.script_builder import AgentScriptVersion
from app.services.llm.client import LLMMessage, create_llm_client, get_max_tokens
from app.services.llm.utils import get_model_api_key

logger = logging.getLogger(__name__)

# --- Resource reference extraction / validation ------------------------------

_TOOL_REF_RE = re.compile(r"tool://([A-Za-z0-9_\-]+)")
_SKILL_REF_RE = re.compile(r"skill://([A-Za-z0-9_\-]+)")


async def _get_agent_available_tools(agent_id: str) -> list[dict]:
    """Installed tools for an agent: ``Tool(enabled=True)`` global set joined with
    ``AgentTool`` assignments. Per-tool enabled flag = ``AgentTool.enabled`` if an
    assignment exists, else ``Tool.is_default``. Returns
    ``[{name, category, description(<=120 chars)}]`` for the enabled ones.
    """
    from app.models.tool import AgentTool, Tool

    async with async_session() as db:
        all_tools_r = await db.execute(select(Tool).where(Tool.enabled == True))  # noqa: E712
        all_tools = all_tools_r.scalars().all()

        agent_tools_r = await db.execute(select(AgentTool).where(AgentTool.agent_id == agent_id))
        assignments = {str(at.tool_id): at for at in agent_tools_r.scalars().all()}

        result = []
        for t in all_tools:
            tid = str(t.id)
            at = assignments.get(tid)
            enabled = at.enabled if at else t.is_default
            if enabled:
                result.append(
                    {
                        "name": t.name,
                        "category": t.category or "",
                        "description": (t.description or "")[:120],
                    }
                )
        return result


async def _get_agent_available_skills(agent_id: str) -> list[dict]:
    """Enumerate ``{agent_id}/skills`` via the async storage backend. Supports both
    folder skills (``skills/<folder>/SKILL.md``) and flat ``skills/<name>.md`` files.
    Parses frontmatter for name/description via ``agent_context._parse_skill_frontmatter``.
    Returns ``[{name, folder, description}]``.
    """
    from app.services.agent_context import _parse_skill_frontmatter
    from app.services.storage_runtime.facade import get_storage_backend

    storage = get_storage_backend()
    prefix = f"{agent_id}/"
    skills: list[dict] = []

    try:
        entries = await storage.list_dir(f"{prefix}skills")
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
                    content = (await storage.read_text(skill_key)).strip()
                    name, desc = _parse_skill_frontmatter(content, entry.name)
                    skills.append({"name": name, "folder": entry.name, "description": desc})
                except Exception:
                    skills.append({"name": entry.name, "folder": entry.name, "description": ""})
        elif entry.name.endswith(".md"):
            stem = entry.name[:-3]
            read_key = f"{prefix}skills/{entry.name}"
            try:
                content = (await storage.read_text(read_key)).strip()
                name, desc = _parse_skill_frontmatter(content, stem)
                skills.append({"name": name, "folder": stem, "description": desc})
            except Exception:
                skills.append({"name": stem, "folder": stem, "description": ""})
    return skills


def _build_available_resources_section(tools: list[dict], skills: list[dict]) -> str:
    """Markdown section enumerating installed tools/skills, emphasizing the script
    MUST ONLY reference items in these lists (the output is hard-validated)."""
    lines = ["\n## Available Tools & Skills (STRICT CONSTRAINT)"]
    lines.append("The evolved script MUST ONLY reference tools and skills from the lists below.")
    lines.append("Do NOT invent, assume, or reference any tool or skill not listed here.")
    lines.append(
        "If functionality requires a tool/skill that is not available, note it in your "
        "explanation but do NOT add it to the script.\n"
    )

    if tools:
        lines.append("### Installed Tools")
        for t in tools:
            desc = f" — {t['description']}" if t["description"] else ""
            lines.append(f"- `tool://{t['name']}`{desc}")
        lines.append("")

    if skills:
        lines.append("### Installed Skills")
        for s in skills:
            desc = f" — {s['description']}" if s["description"] else ""
            lines.append(f"- `skill://{s['folder']}`{desc}")
        lines.append("")

    if not tools:
        lines.append("### Installed Tools\n- (none)\n")
    if not skills:
        lines.append("### Installed Skills\n- (none)\n")

    return "\n".join(lines)


def _extract_resource_refs(script: str) -> tuple[set[str], set[str]]:
    """Return ``(tool_names, skill_folders)`` referenced in the script via
    ``tool://X`` / ``skill://Y`` schemes."""
    return (
        set(_TOOL_REF_RE.findall(script or "")),
        set(_SKILL_REF_RE.findall(script or "")),
    )


def _extract_ascript_block(text: str) -> tuple[str | None, str | None]:
    """Extract the evolved agent script from an LLM response.

    Tolerant of fence formatting so a correctly-generated script is not
    discarded over cosmetics:
      1. Prefer an ``ascript``-tagged fence, any casing / surrounding spaces.
      2. Otherwise fall back to the first fenced code block with any (or no)
         language tag.

    Returns ``(script_content, full_matched_block)``; ``(None, None)`` when no
    fenced block exists at all.
    """
    if not text:
        return None, None
    match = re.search(
        r"```[ \t]*ascript[ \t]*\r?\n([\s\S]*?)```", text, re.IGNORECASE
    )
    if not match:
        # No ascript-tagged fence. Fall back to a generic fenced block only
        # when there is exactly one — otherwise we cannot tell the script
        # apart from explanatory code blocks, so we'd rather fail loudly.
        generic = list(
            re.finditer(
                r"```[ \t]*[A-Za-z0-9_+.\-]*[ \t]*\r?\n([\s\S]*?)```", text
            )
        )
        if len(generic) == 1:
            match = generic[0]
    if not match:
        return None, None
    content = match.group(1).strip()
    if not content:
        return None, None
    return content, match.group(0)


def _validate_resource_refs(
    script: str,
    available_tools: list[dict],
    available_skills: list[dict],
) -> list[str]:
    """Return a list of human-readable problems (missing tool/skill references).
    Empty list = valid."""
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


def _parse_analysis(text: str) -> dict:
    """Parse an LLM health-analysis response into a structured dict.

    Per design §8.3:
    1. Extract JSON from a ```json (or bare ```) fenced block; fall back to whole-text
       ``json.loads``.
    2. Both fail → ``{"overall_score": 0, "dimensions": [], "strengths": [],
       "suggestions": [text[:500]]}`` (graceful degradation, no error).
    3. CamelCase compat: rename ``overallScore`` → ``overall_score`` if needed; other
       keys pass through unchanged.
    """
    data: dict | None = None
    block_match = re.search(r"```(?:json)?\s*\n([\s\S]*?)```", text or "")
    if block_match:
        try:
            data = json.loads(block_match.group(1).strip())
        except Exception:
            data = None
    if data is None:
        try:
            data = json.loads((text or "").strip())
        except Exception:
            data = None

    if not isinstance(data, dict):
        return {
            "overall_score": 0,
            "dimensions": [],
            "strengths": [],
            "suggestions": [(text or "")[:500]],
        }

    # camelCase compat
    if "overallScore" in data and "overall_score" not in data:
        data["overall_score"] = data.pop("overallScore")
    return data


async def _get_llm_model(db, tenant_id) -> LLMModel | None:
    """Pick an enabled ``LLMModel``: tenant-specific first (created_at asc), then
    fall back to a tenant-agnostic model (``tenant_id IS NULL``). Returns None if
    neither exists."""
    result = await db.execute(
        select(LLMModel)
        .where(LLMModel.tenant_id == tenant_id, LLMModel.enabled == True)  # noqa: E712
        .order_by(LLMModel.created_at)
        .limit(1)
    )
    model = result.scalar_one_or_none()
    if model:
        return model

    result2 = await db.execute(
        select(LLMModel)
        .where(LLMModel.tenant_id.is_(None), LLMModel.enabled == True)  # noqa: E712
        .order_by(LLMModel.created_at)
        .limit(1)
    )
    return result2.scalar_one_or_none()


def build_evolution_prompt(
    current_script: str,
    direction: str,
    past_knowledge: list[str],
    open_feedbacks: list[dict],
    available_resources: str = "",
) -> str:
    """Assemble the Evolution System Prompt (verbatim from design Appendix A)."""
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
        prompt += (
            "The following are accumulated insights from previous evolution cycles. "
            "You MUST review all of them before making changes, to build on prior "
            "learnings and avoid repeating past mistakes:\n\n"
        )
        for i, k in enumerate(past_knowledge):
            prompt += f"### Learning {i + 1}\n{k}\n\n"

    if open_feedbacks:
        prompt += "\n## User Feedback (Must Address)\n"
        prompt += (
            "The following feedback items have been manually submitted and MUST be "
            "considered in this evolution. Address each one and explain how you "
            "incorporated it:\n\n"
        )
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
    """The 9-step evolution flow (design §7.1).

    Returns one of:
      - ``{status:'success', version, feedbacks_addressed}``
      - ``{status:'rejected', detail, problems}``
      - ``{status:'error', detail}``
    """
    async with async_session() as db:
        # Step 1 — current script (evolved → initial)
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

        # Step 2 — past evolution knowledge
        knowledge_result = await db.execute(
            select(AgentScriptVersion)
            .where(
                AgentScriptVersion.agent_id == agent_id,
                AgentScriptVersion.folder == "evolution_knowledge",
            )
            .order_by(AgentScriptVersion.version)
        )
        past_knowledge = [k.content for k in knowledge_result.scalars().all()]

        # Step 3 — open feedbacks
        feedback_result = await db.execute(
            select(AgentFeedback).where(
                AgentFeedback.agent_id == agent_id,
                AgentFeedback.status == "open",
            )
        )
        open_feedback_rows = feedback_result.scalars().all()
        open_feedbacks = [{"category": f.category, "content": f.content} for f in open_feedback_rows]
        open_feedback_ids = [f.id for f in open_feedback_rows]

        # Step 4 — available tools & skills
        available_tools = await _get_agent_available_tools(agent_id)
        available_skills = await _get_agent_available_skills(agent_id)
        available_resources = _build_available_resources_section(available_tools, available_skills)

        system_prompt = build_evolution_prompt(
            current_script, direction, past_knowledge, open_feedbacks, available_resources
        )

        # Step 5 — pick LLM model
        llm_model = await _get_llm_model(db, tenant_id)
        if not llm_model:
            return {"status": "error", "detail": "No LLM model available"}

        # Step 6 — build & call LLM (temperature=0.7). SECURITY FIX: decrypt the key.
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
                LLMMessage(role="system", content=system_prompt),
                LLMMessage(
                    role="user",
                    content=f"Please evolve this agent script with focus on: {direction}",
                ),
            ]
            max_tokens = llm_model.max_output_tokens or get_max_tokens(
                llm_model.provider, llm_model.model
            )
            full_response = await client.complete(
                messages=messages,
                temperature=0.7,
                max_tokens=max_tokens,
            )
        finally:
            await client.close()

        response_text = full_response.content if hasattr(full_response, "content") else str(full_response)

        # Step 7 — extract ascript block (tolerant of fence formatting)
        evolved_script, matched_block = _extract_ascript_block(response_text)

        if not evolved_script:
            return {"status": "error", "detail": "AI response did not contain a valid ascript block"}

        # Step 8 — HARD resource-ref validation (rejection mechanism)
        problems = _validate_resource_refs(evolved_script, available_tools, available_skills)
        if problems:
            problem_text = "\n".join(f"- {p}" for p in problems)
            logger.warning(
                f"[Evolution] Rejecting evolved script for agent {agent_id}: "
                f"invalid resource references:\n{problem_text}"
            )
            max_knowledge_ver = await db.execute(
                select(func.coalesce(func.max(AgentScriptVersion.version), 0)).where(
                    AgentScriptVersion.agent_id == agent_id,
                    AgentScriptVersion.folder == "evolution_knowledge",
                )
            )
            next_knowledge_version = max_knowledge_ver.scalar() + 1
            db.add(
                AgentScriptVersion(
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
                    source=f"evolution-rejected-{direction}"[:50],
                )
            )
            await db.commit()
            # Structured breakdown so the UI can render the existing
            # "missing resources" dialog and offer one-click imports.
            tool_refs, skill_refs = _extract_resource_refs(evolved_script)
            available_tool_names = {t["name"] for t in available_tools}
            available_skill_folders = {s["folder"] for s in available_skills}
            missing_tools = [
                {"tool_name": name, "action": ""}
                for name in sorted(tool_refs - available_tool_names)
            ]
            missing_skills = [
                {"folder_name": folder, "action": ""}
                for folder in sorted(skill_refs - available_skill_folders)
            ]
            return {
                "status": "rejected",
                "detail": "evolved script references unavailable tools/skills",
                "problems": problems,
                "missing_tools": missing_tools,
                "missing_skills": missing_skills,
            }

        # Step 9 — persist success
        max_evolved_ver = await db.execute(
            select(func.coalesce(func.max(AgentScriptVersion.version), 0)).where(
                AgentScriptVersion.agent_id == agent_id,
                AgentScriptVersion.folder == "evolved",
            )
        )
        next_evolved_version = max_evolved_ver.scalar() + 1

        db.add(
            AgentScriptVersion(
                agent_id=agent_id,
                version=next_evolved_version,
                folder="evolved",
                content=evolved_script,
                source=f"evolution-{direction}"[:50],
            )
        )

        # Sediment leftover explanation text as knowledge (strip the exact
        # script block we extracted, whatever fence style it used).
        knowledge_text = (
            response_text.replace(matched_block, "", 1).strip()
            if matched_block
            else response_text.strip()
        )
        if len(knowledge_text) > 20:
            max_knowledge_ver = await db.execute(
                select(func.coalesce(func.max(AgentScriptVersion.version), 0)).where(
                    AgentScriptVersion.agent_id == agent_id,
                    AgentScriptVersion.folder == "evolution_knowledge",
                )
            )
            next_knowledge_version = max_knowledge_ver.scalar() + 1
            db.add(
                AgentScriptVersion(
                    agent_id=agent_id,
                    version=next_knowledge_version,
                    folder="evolution_knowledge",
                    content=knowledge_text,
                    source=f"evolution-{direction}"[:50],
                )
            )

        # Mark addressed feedbacks.
        if open_feedback_ids:
            for fid in open_feedback_ids:
                fb_result = await db.execute(select(AgentFeedback).where(AgentFeedback.id == fid))
                fb = fb_result.scalar_one_or_none()
                if fb:
                    fb.status = "addressed"

        await db.commit()

        # Sync object storage (best-effort).
        try:
            from app.services.storage_runtime.facade import get_storage_backend

            storage = get_storage_backend()
            await storage.write_text(f"{agent_id}/soul.md", evolved_script)
        except Exception as e:
            logger.warning(f"[Evolution] Failed to sync soul.md for {agent_id}: {e}")

        # Invalidate cached parsed scripts so the new version takes effect immediately.
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
