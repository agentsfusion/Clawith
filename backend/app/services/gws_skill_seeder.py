"""GWS Skill Seeder - Import Google Workspace CLI skills from GitHub."""

import base64
import re
import uuid

import httpx
from loguru import logger
from sqlalchemy import select

from app.database import async_session
from app.models.skill import Skill, SkillFile


GITHUB_API = "https://api.github.com"
GWS_REPO_OWNER = "googleworkspace"
GWS_REPO_NAME = "cli"
GWS_SKILLS_PATH = "skills"


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


async def _fetch_gws_skill_content(
    skill_name: str,
    token: str = "",
) -> str | None:
    """Fetch SKILL.md content for a specific GWS skill from GitHub."""
    url = f"{GITHUB_API}/repos/{GWS_REPO_OWNER}/{GWS_REPO_NAME}/contents/{GWS_SKILLS_PATH}/{skill_name}/SKILL.md"
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    async with httpx.AsyncClient(timeout=30, headers=headers) as client:
        try:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                content = base64.b64decode(data["content"]).decode("utf-8")
                return content
            elif resp.status_code == 404:
                logger.warning(f"[GWS Seeder] SKILL.md not found for {skill_name}")
                return None
            elif resp.status_code == 429:
                logger.warning(f"[GWS Seeder] GitHub rate limit hit while fetching {skill_name}")
                return None
            else:
                logger.error(f"[GWS Seeder] GitHub API error for {skill_name}: {resp.status_code}")
                return None
        except Exception as e:
            logger.error(f"[GWS Seeder] Failed to fetch {skill_name}: {e}")
            return None


def _filter_gws_skills(entries: list[dict]) -> list[str]:
    """
    Filter and sort GWS skill directory names.
    
    Priority:
    1. gws-shared (dependency for all other GWS skills)
    2. gws-* core skills (alphabetical)
    
    Excluded: gws-workflow-*, persona-*, recipe-*
    """
    skill_names = []
    has_gws_shared = False
    
    for entry in entries:
        if entry.get("type") != "dir":
            continue
        name = entry.get("name", "")
        
        # Check for gws-shared
        if name == "gws-shared":
            has_gws_shared = True
            continue
        
        # Include gws-* core skills (exclude workflows/personas/recipes)
        if (name.startswith("gws-") and 
            not name.startswith("gws-workflow-") and
            not name.startswith("persona-") and
            not name.startswith("recipe-")):
            skill_names.append(name)
    
    # Sort alphabetically
    skill_names.sort()
    
    # Add gws-shared first if it exists
    if has_gws_shared:
        skill_names.insert(0, "gws-shared")
    
    return skill_names


async def _save_gws_skill_to_db(
    folder_name: str,
    skill_md_content: str,
    tenant_id: str | None = None,
) -> bool:
    """
    Save a GWS skill to the database.
    
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
            logger.info(f"[GWS Seeder] Skill {folder_name} already exists, skipping")
            return False
        
        # Create Skill
        skill = Skill(
            name=name,
            description=description,
            category="gws",
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
        logger.info(f"[GWS Seeder] Imported GWS skill: {name}")
        return True


async def import_gws_skills(tenant_id: str | None = None) -> int:
    """
    Import GWS skills from GitHub into the skill registry.
    
    Args:
        tenant_id: Optional tenant ID for tenant-scoped import.
                   If None, creates global builtin skills (visible to all tenants).
    
    Returns:
        Number of skills imported successfully.
    """
    logger.info(f"[GWS Seeder] Starting import (tenant_id={tenant_id})")
    
    # Get GitHub token for higher rate limits
    token = await _get_github_token(tenant_id)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    
    # Fetch directory listing
    url = f"{GITHUB_API}/repos/{GWS_REPO_OWNER}/{GWS_REPO_NAME}/contents/{GWS_SKILLS_PATH}"
    
    async with httpx.AsyncClient(timeout=30, headers=headers) as client:
        try:
            resp = await client.get(url)
            if resp.status_code == 404:
                logger.error(f"[GWS Seeder] GitHub repository path not found: {url}")
                return 0
            if resp.status_code == 429:
                logger.warning("[GWS Seeder] GitHub rate limit exceeded")
                return 0
            if resp.status_code != 200:
                logger.error(f"[GWS Seeder] GitHub API error: {resp.status_code}")
                return 0
            
            entries = resp.json()
            if isinstance(entries, dict):
                entries = [entries]
        except Exception as e:
            logger.error(f"[GWS Seeder] Failed to fetch directory: {e}")
            return 0
    
    # Filter and sort skills
    skill_names = _filter_gws_skills(entries)
    logger.info(f"[GWS Seeder] Found {len(skill_names)} GWS skills to import")
    
    # Import each skill
    imported_count = 0
    for skill_name in skill_names:
        # Fetch SKILL.md content
        content = await _fetch_gws_skill_content(skill_name, token)
        if not content:
            logger.warning(f"[GWS Seeder] Skipping {skill_name} (no SKILL.md)")
            continue
        
        # Save to database
        saved = await _save_gws_skill_to_db(skill_name, content, tenant_id)
        if saved:
            imported_count += 1
    
    logger.info(f"[GWS Seeder] Imported {imported_count}/{len(skill_names)} skills")
    return imported_count


async def ensure_gws_shared_for_agent(agent_id: str, tenant_id: str | None = None):
    """
    Ensure gws-shared skill is installed when any gws-* skill is assigned to an agent.
    
    This will be called during skill assignment flow (separate implementation).
    
    Args:
        agent_id: The agent UUID
        tenant_id: Optional tenant ID for scoping
    """
    # TODO: Implement in skill assignment flow
    # Check if agent has any gws-* skills
    # If yes, ensure gws-shared is also in the agent's skills list
    pass
