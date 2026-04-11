"""Lark Skill Seeder - Import Lark CLI skills from GitHub."""

import base64
import re
import uuid

import httpx
from loguru import logger
from sqlalchemy import select

from app.database import async_session
from app.models.skill import Skill, SkillFile


GITHUB_API = "https://api.github.com"
LARK_REPO_OWNER = "larksuite"
LARK_REPO_NAME = "cli"
LARK_SKILLS_PATH = "skills"
LARK_SKILL_PREFIX = "lark-"


def is_lark_skill(folder_name: str) -> bool:
    """Check if a skill folder belongs to the Lark ecosystem."""
    return folder_name.startswith(LARK_SKILL_PREFIX)


async def _get_github_token(tenant_id: str | None = None) -> str:
    """Resolve GitHub token from tenant settings DB."""
    if not tenant_id:
        return ""

    try:
        from app.models.tenant_setting import TenantSetting
        async with async_session() as db:
            result = await db.execute(
                select(TenantSetting).where(
                    TenantSetting.tenant_id == uuid.UUID(tenant_id),
                    TenantSetting.key == "github_token",
                )
            )
            setting = result.scalar_one_or_none()
            if setting and setting.value.get("token"):
                return setting.value["token"]
    except Exception:
        pass
    return ""


def _parse_skill_md_frontmatter(content: str) -> dict:
    """Extract YAML frontmatter from SKILL.md content."""
    import yaml
    match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return {}
    try:
        return yaml.safe_load(match.group(1)) or {}
    except Exception:
        return {}


async def _fetch_lark_skill_content(
    skill_name: str,
    token: str = "",
) -> str | None:
    """Fetch SKILL.md content for a specific Lark skill from GitHub."""
    url = f"{GITHUB_API}/repos/{LARK_REPO_OWNER}/{LARK_REPO_NAME}/contents/{LARK_SKILLS_PATH}/{skill_name}/SKILL.md"
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    async with httpx.AsyncClient(timeout=30, headers=headers) as client:
        try:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                content = base64.b64decode(data["content"]).decode("utf-8")
                return content
            elif resp.status_code == 404:
                logger.warning(f"[Lark Seeder] SKILL.md not found for {skill_name}")
                return None
            elif resp.status_code == 429:
                logger.warning(f"[Lark Seeder] GitHub rate limit hit while fetching {skill_name}")
                return None
            else:
                logger.error(f"[Lark Seeder] GitHub API error for {skill_name}: {resp.status_code}")
                return None
        except Exception as e:
            logger.error(f"[Lark Seeder] Failed to fetch {skill_name}: {e}")
            return None


def _filter_lark_skills(entries: list[dict]) -> list[str]:
    """
    Filter and sort Lark skill directory names.

    Includes all lark-* skills from the Lark CLI repo.

    Priority ordering:
    1. lark-shared (dependency for all other Lark skills)
    2. Remaining skills (alphabetical)
    """
    skill_names = []
    has_lark_shared = False

    for entry in entries:
        if entry.get("type") != "dir":
            continue
        name = entry.get("name", "")

        if name == "lark-shared":
            has_lark_shared = True
            continue

        if is_lark_skill(name):
            skill_names.append(name)

    skill_names.sort()

    if has_lark_shared:
        skill_names.insert(0, "lark-shared")

    return skill_names


async def _save_lark_skill_to_db(
    folder_name: str,
    skill_md_content: str,
    tenant_id: str | None = None,
) -> bool:
    """
    Save a Lark skill to the database.

    Returns:
        True if saved successfully, False if skipped (already exists or error)
    """
    # Parse frontmatter
    frontmatter = _parse_skill_md_frontmatter(skill_md_content)
    name = frontmatter.get("name", folder_name)
    description = frontmatter.get("description", "")

    async with async_session() as db:
        # Check for conflict (folder_name + tenant_id)
        conflict_q = select(Skill).where(Skill.folder_name == folder_name)
        if tenant_id:
            conflict_q = conflict_q.where(Skill.tenant_id == uuid.UUID(tenant_id))
        else:
            conflict_q = conflict_q.where(Skill.tenant_id.is_(None))

        existing = await db.execute(conflict_q)
        if existing.scalar_one_or_none():
            logger.info(f"[Lark Seeder] Skill {folder_name} already exists, skipping")
            return False

        # Create Skill
        skill = Skill(
            name=name,
            description=description,
            category="lark",
            icon="",
            folder_name=folder_name,
            is_builtin=True,
            tenant_id=uuid.UUID(tenant_id) if tenant_id else None,
        )
        db.add(skill)
        await db.flush()

        # Create SkillFile
        # PostgreSQL text columns cannot store null bytes
        clean_content = skill_md_content.replace("\x00", "")
        db.add(SkillFile(
            skill_id=skill.id,
            path="SKILL.md",
            content=clean_content,
        ))

        await db.commit()
        logger.info(f"[Lark Seeder] Imported Lark skill: {name}")
        return True


async def import_lark_skills(tenant_id: str | None = None) -> int:
    """
    Import Lark skills from GitHub into the skill registry.

    Args:
        tenant_id: Optional tenant ID for tenant-scoped import.
                   If None, creates global builtin skills (visible to all tenants).

    Returns:
        Number of skills imported successfully.
    """
    logger.info(f"[Lark Seeder] Starting import (tenant_id={tenant_id})")

    # Get GitHub token for higher rate limits
    token = await _get_github_token(tenant_id)
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    # Fetch directory listing
    url = f"{GITHUB_API}/repos/{LARK_REPO_OWNER}/{LARK_REPO_NAME}/contents/{LARK_SKILLS_PATH}"

    async with httpx.AsyncClient(timeout=30, headers=headers) as client:
        try:
            resp = await client.get(url)
            if resp.status_code == 404:
                logger.error(f"[Lark Seeder] GitHub repository path not found: {url}")
                return 0
            if resp.status_code == 429:
                logger.warning("[Lark Seeder] GitHub rate limit exceeded")
                return 0
            if resp.status_code != 200:
                logger.error(f"[Lark Seeder] GitHub API error: {resp.status_code}")
                return 0

            entries = resp.json()
            if isinstance(entries, dict):
                entries = [entries]
        except Exception as e:
            logger.error(f"[Lark Seeder] Failed to fetch directory: {e}")
            return 0

    # Filter and sort skills
    skill_names = _filter_lark_skills(entries)
    logger.info(f"[Lark Seeder] Found {len(skill_names)} Lark skills to import")

    # Import each skill
    imported_count = 0
    for skill_name in skill_names:
        # Fetch SKILL.md content
        content = await _fetch_lark_skill_content(skill_name, token)
        if not content:
            logger.warning(f"[Lark Seeder] Skipping {skill_name} (no SKILL.md)")
            continue

        # Save to database
        saved = await _save_lark_skill_to_db(skill_name, content, tenant_id)
        if saved:
            imported_count += 1

    logger.info(f"[Lark Seeder] Imported {imported_count}/{len(skill_names)} skills")
    return imported_count


async def ensure_lark_tool_for_agents_with_skills() -> int:
    """
    Startup task: scan all agents and enable 'lark' tool for any agent
    that has lark-* skill files in its workspace but lacks tool assignment.

    Returns:
        Number of agents that were updated.
    """
    from app.models.agent import Agent
    from app.services.storage.factory import get_storage

    storage = get_storage()

    async with async_session() as db:
        agents_r = await db.execute(select(Agent))
        agents = agents_r.scalars().all()

    count = 0
    for agent in agents:
        skills_prefix = f"{agent.id}/skills/"

        try:
            skills_list = await storage.list(skills_prefix)
            has_lark = any(
                info.is_dir and is_lark_skill(info.name)
                for info in skills_list
            )
        except Exception:
            has_lark = False

        if has_lark:
            enabled = await ensure_lark_tool_enabled_for_agent(agent.id)
            if enabled:
                count += 1

    if count > 0:
        logger.info(f"[Lark Seeder] Auto-enabled 'lark' tool for {count} agent(s) with Lark skills")
    return count


async def ensure_lark_tool_enabled_for_agent(agent_id: uuid.UUID) -> bool:
    """
    Ensure the 'lark' tool is enabled for an agent.

    When Lark skills are installed in an agent's workspace, the agent needs
    the 'lark' tool to be in its function-calling tool list so the LLM can
    actually execute Lark CLI commands.

    Returns:
        True if the tool was enabled (created or updated), False if already enabled or tool not found.
    """
    from app.models.tool import Tool, AgentTool

    async with async_session() as db:
        tool_r = await db.execute(select(Tool).where(Tool.name == "lark"))
        lark_tool = tool_r.scalar_one_or_none()
        if not lark_tool:
            logger.warning("[Lark Seeder] 'lark' tool not found in tools table, cannot auto-enable")
            return False

        at_r = await db.execute(
            select(AgentTool).where(
                AgentTool.agent_id == agent_id,
                AgentTool.tool_id == lark_tool.id,
            )
        )
        existing = at_r.scalar_one_or_none()

        if existing:
            if existing.enabled:
                return False
            existing.enabled = True
            await db.commit()
            logger.info(f"[Lark Seeder] Re-enabled 'lark' tool for agent {agent_id}")
            return True

        db.add(AgentTool(
            agent_id=agent_id,
            tool_id=lark_tool.id,
            enabled=True,
            source="system",
        ))
        await db.commit()
        logger.info(f"[Lark Seeder] Enabled 'lark' tool for agent {agent_id}")
        return True
