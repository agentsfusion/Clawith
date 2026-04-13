"""
Auto-save Agent Script from Factory Agent chat responses.

Ported from ClawEvolver's autoSaveToWorkspace() logic:
- Extracts ```ascript``` blocks from LLM responses
- Finds or creates a workspace agent (Clawith Agent record)
- Saves versioned scripts to agent workspace storage
- Extracts evolution knowledge from non-script explanation text
"""

import re
import uuid
from loguru import logger

from app.database import async_session
from app.models.agent import Agent as AgentModel
from sqlalchemy import select

from app.services.storage.factory import get_storage


def extract_ascript(text: str | None) -> str | None:
    if not text:
        return None
    matches = re.findall(r"```ascript\s*\r?\n([\s\S]*?)```", text)
    return matches[-1].strip() if matches else None


def extract_knowledge(text: str) -> str | None:
    without_script = re.sub(r"```ascript[\s\S]*?```", "", text).strip()
    if len(without_script) < 20:
        return None
    return without_script


def extract_agent_name(script: str) -> str | None:
    for pattern in [
        r'agent_name:\s*"([^"]+)"',
        r"agent_name:\s*'([^']+)'",
        r'name:\s*"([^"]+)"',
        r"name:\s*'([^']+)'",
    ]:
        m = re.search(pattern, script)
        if m:
            return m.group(1).strip()
    return None


def _titleize(name: str) -> str:
    return name.replace("_", " ").title()


async def _count_versions(storage, prefix: str, ext: str = ".ascript") -> int:
    try:
        files = await storage.list(prefix)
        nums = []
        for f in files:
            m = re.search(rf"v(\d+){re.escape(ext)}$", f.name)
            if m:
                nums.append(int(m.group(1)))
        return max(nums, default=0)
    except Exception:
        return 0


def is_factory_agent(agent_name: str, role_description: str) -> bool:
    name_lower = (agent_name or "").lower()
    role_lower = (role_description or "").lower()
    return "agent factory" in name_lower or "agent factory" in role_lower


async def auto_save_ascript(
    factory_agent_id: uuid.UUID,
    tenant_id: str | None,
    user_id: uuid.UUID,
    assistant_response: str,
    conversation: list[dict],
    conv_id: str | None = None,
):
    try:
        script = extract_ascript(assistant_response)
        if not script:
            return None

        logger.info("[AScript AutoSave] Detected ascript block in Factory Agent response")

        has_prior_script = any(
            m.get("role") == "assistant" and extract_ascript(m.get("content", ""))
            for m in conversation[:-1]
        )

        script_agent_name = extract_agent_name(script)
        display_name = _titleize(script_agent_name) if script_agent_name else "Agent from Factory"

        storage = get_storage()

        async with async_session() as db:
            existing_agent = None

            if script_agent_name:
                result = await db.execute(
                    select(AgentModel).where(
                        AgentModel.tenant_id == tenant_id,
                        AgentModel.agent_type == "ascript",
                    )
                )
                ascript_agents = result.scalars().all()
                for a in ascript_agents:
                    ws_path = f"{a.id}/agent_script.ascript"
                    try:
                        existing_script = await storage.read(ws_path)
                        existing_name = extract_agent_name(existing_script)
                        if existing_name and existing_name.lower() == script_agent_name.lower():
                            existing_agent = a
                            break
                    except Exception:
                        continue

            if existing_agent:
                agent = existing_agent
                folder = "evolved"
                logger.info(f"[AScript AutoSave] Found existing agent: {agent.name} ({agent.id})")
            else:
                agent = AgentModel(
                    name=display_name,
                    tenant_id=tenant_id,
                    agent_type="ascript",
                    role_description="Agent Script agent created by Factory",
                    status="idle",
                    creator_id=user_id,
                )
                db.add(agent)
                await db.commit()
                await db.refresh(agent)
                folder = "initial"
                logger.info(f"[AScript AutoSave] Created new agent: {agent.name} ({agent.id})")

            agent_id_str = str(agent.id)

            await storage.write(f"{agent_id_str}/agent_script.ascript", script)
            logger.info(f"[AScript AutoSave] Saved agent_script.ascript for {agent.name}")

            versions_prefix = f"{agent_id_str}/script_versions/{folder}/"
            last_version = await _count_versions(storage, versions_prefix, ".ascript")
            version = last_version + 1

            version_path = f"{agent_id_str}/script_versions/{folder}/v{version}.ascript"
            await storage.write(version_path, script)
            logger.info(f"[AScript AutoSave] Saved version: {version_path}")

            if has_prior_script or existing_agent:
                knowledge = extract_knowledge(assistant_response)
                if knowledge:
                    k_prefix = f"{agent_id_str}/script_versions/evolution_knowledge/"
                    last_kv = await _count_versions(storage, k_prefix, ".md")
                    kv = last_kv + 1
                    k_path = f"{agent_id_str}/script_versions/evolution_knowledge/v{kv}.md"
                    await storage.write(k_path, knowledge)
                    logger.info(f"[AScript AutoSave] Saved evolution knowledge: {k_path}")

            meta = {
                "agent_id": agent_id_str,
                "agent_name": agent.name,
                "folder": folder,
                "version": version,
                "script_agent_name": script_agent_name,
                "has_prior_script": has_prior_script,
            }
            logger.info(f"[AScript AutoSave] Complete: {meta}")

            return meta

    except Exception as e:
        logger.error(f"[AScript AutoSave] Failed: {e}")
        import traceback
        traceback.print_exc()
        return None
