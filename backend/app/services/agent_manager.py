from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import docker
from docker.errors import DockerException, NotFound
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.agent import Agent
from app.models.llm import LLMModel
from app.services.llm.utils import get_model_api_key
from app.services.storage.factory import get_storage

if TYPE_CHECKING:
    from app.models.user import User

settings = get_settings()


async def _collect_storage_keys(prefix: str) -> list[str]:
    """Recursively collect all file keys under a storage prefix."""
    storage = get_storage()
    keys: list[str] = []
    entries = await storage.list(prefix)
    for entry in entries:
        if entry.is_dir:
            keys.extend(await _collect_storage_keys(entry.path))
        else:
            keys.append(entry.path)
    return keys


class AgentManager:
    """Manage OpenClaw Gateway Docker containers for digital employees."""

    def __init__(self):
        try:
            self.docker_client = docker.from_env()
        except DockerException:
            logger.warning("Docker not available — agent containers will not be managed")
            self.docker_client = None

    def _agent_dir(self, agent_id: uuid.UUID) -> Path:
        return Path(settings.AGENT_DATA_DIR) / str(agent_id)

    def _template_dir(self) -> Path:
        return Path(settings.AGENT_TEMPLATE_DIR)

    async def initialize_agent_files(self, db: AsyncSession, agent: Agent,
                                      personality: str = "", boundaries: str = "") -> None:
        """Copy template files and customize for this agent."""
        storage = get_storage()
        aid = str(agent.id)
        agent_dir = self._agent_dir(agent.id)
        template_dir = self._template_dir()

        if agent_dir.exists():
            logger.warning(f"Agent dir already exists: {agent_dir}")
            return

        # --- Step 1: Copy template files to storage ---
        if template_dir.exists():
            for template_file in sorted(template_dir.rglob("*")):
                if template_file.is_file():
                    rel = template_file.relative_to(template_dir)
                    key = f"{aid}/{rel.as_posix()}"
                    content = template_file.read_text(encoding="utf-8")
                    await storage.write(key, content)
        else:
            # No template dir (local dev) — write minimal files via storage
            logger.info(f"Template dir not found ({template_dir}), creating minimal workspace")
            await storage.write(f"{aid}/tasks.json", "[]")

        # Create local directory structure for Docker volume mounts
        agent_dir.mkdir(parents=True, exist_ok=True)
        for subdir in ["workspace", "workspace/knowledge_base", "memory", "skills"]:
            (agent_dir / subdir).mkdir(exist_ok=True)

        # --- Step 2: Customize soul.md ---
        soul_key = f"{aid}/soul.md"
        # Get creator name
        from app.models.user import User
        result = await db.execute(select(User).where(User.id == agent.creator_id))
        creator = result.scalar_one_or_none()
        creator_name = creator.display_name if creator else "Unknown"

        soul_content = f"# Personality\n\nI'm {agent.name}, {agent.role_description or 'a digital assistant'}.\n"
        if await storage.exists(soul_key):
            template_content = await storage.read(soul_key)
            soul_content = template_content.replace("{{agent_name}}", agent.name)
            soul_content = soul_content.replace("{{role_description}}", agent.role_description or "General Assistant")
            soul_content = soul_content.replace("{{creator_name}}", creator_name)
            soul_content = soul_content.replace("{{created_at}}", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        elif agent.template_id:
            from app.models.template import AgentTemplate
            tmpl_result = await db.execute(select(AgentTemplate).where(AgentTemplate.id == agent.template_id))
            tmpl = tmpl_result.scalar_one_or_none()
            if tmpl and tmpl.soul_template:
                soul_content = tmpl.soul_template

        # Helper function to replace or append sections
        def replace_or_append_section(content: str, section_name: str, section_content: str) -> str:
            """Replace existing ## SectionName or append if not found."""
            if not section_content:
                return content

            # Pattern to match existing section (case-insensitive header)
            import re
            pattern = rf"^##\s+{re.escape(section_name)}\s*$"
            lines = content.split('\n')

            # Find the section header
            for i, line in enumerate(lines):
                if re.match(pattern, line.strip(), re.IGNORECASE):
                    # Found existing section - replace until next ## header or end
                    section_start = i
                    section_end = len(lines)
                    for j in range(i + 1, len(lines)):
                        if lines[j].strip().startswith('## '):
                            section_end = j
                            break

                    # Replace the section content (with trailing newline for proper spacing)
                    new_section = f"## {section_name}\n{section_content}\n"
                    lines = lines[:section_start] + [new_section] + lines[section_end:]
                    return '\n'.join(lines)

            # Section not found - append at the end
            return content + f"\n## {section_name}\n{section_content}\n"

        # Use the helper to replace or append Personality and Boundaries
        soul_content = replace_or_append_section(soul_content, "Personality", personality)
        soul_content = replace_or_append_section(soul_content, "Boundaries", boundaries)

        await storage.write(soul_key, soul_content)

        # --- Step 3: Ensure memory.md exists ---
        mem_key = f"{aid}/memory/memory.md"
        if not await storage.exists(mem_key):
            await storage.write(mem_key, "# Memory\n\n_Record important information and knowledge here._\n")

        # --- Step 4: Ensure reflections.md exists — copy from central template ---
        refl_key = f"{aid}/memory/reflections.md"
        if not await storage.exists(refl_key):
            refl_template = Path(__file__).parent.parent / "templates" / "reflections.md"
            refl_content = refl_template.read_text(encoding="utf-8") if refl_template.exists() else "# Reflections Journal\n"
            await storage.write(refl_key, refl_content)

        # --- Step 5: Ensure HEARTBEAT.md exists — copy from central template ---
        hb_key = f"{aid}/HEARTBEAT.md"
        if not await storage.exists(hb_key):
            hb_template = Path(__file__).parent.parent / "templates" / "HEARTBEAT.md"
            hb_content = hb_template.read_text(encoding="utf-8") if hb_template.exists() else "# Heartbeat Instructions\n"
            await storage.write(hb_key, hb_content)

        # --- Step 6: Customize state.json ---
        state_key = f"{aid}/state.json"
        if await storage.exists(state_key):
            state = json.loads(await storage.read(state_key))
            state["agent_id"] = str(agent.id)
            state["name"] = agent.name
            await storage.write(state_key, json.dumps(state, ensure_ascii=False, indent=2))

        logger.info(f"Initialized agent files at {agent_dir}")

    def _generate_openclaw_config(self, agent: Agent, model: LLMModel | None) -> dict:
        """Generate openclaw.json config for the agent container."""
        config = {
            "agent": {
                "model": f"{model.provider}/{model.model}" if model else "anthropic/claude-sonnet-4-5",
            },
            "agents": {
                "defaults": {
                    "workspace": "/home/node/.openclaw/workspace",
                },
            },
        }

        if model:
            config["env"] = {
                f"{model.provider.upper()}_API_KEY": get_model_api_key(model),
            }

        return config

    async def start_container(self, db: AsyncSession, agent: Agent) -> str | None:
        """Start an OpenClaw Gateway Docker container for the agent.

        Returns container_id or None if Docker not available.
        """
        if not self.docker_client:
            logger.info("Docker not available, skipping container start")
            agent.status = "idle"
            agent.last_active_at = datetime.now(timezone.utc)
            return None

        agent_dir = self._agent_dir(agent.id)

        # Get model config
        model = None
        if agent.primary_model_id:
            result = await db.execute(select(LLMModel).where(LLMModel.id == agent.primary_model_id))
            model = result.scalar_one_or_none()

        # Generate OpenClaw config
        config = self._generate_openclaw_config(agent, model)
        config_dir = agent_dir / ".openclaw"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "openclaw.json").write_text(json.dumps(config, indent=2), encoding="utf-8")

        # Create workspace symlink
        workspace_dir = config_dir / "workspace"
        if not workspace_dir.exists():
            workspace_dir.symlink_to(agent_dir / "workspace")

        # Assign a unique port
        container_port = 18789 + hash(str(agent.id)) % 10000

        try:
            container = self.docker_client.containers.run(
                settings.OPENCLAW_IMAGE,
                detach=True,
                name=f"clawith-agent-{str(agent.id)[:8]}",
                network=settings.DOCKER_NETWORK,
                ports={f"{settings.OPENCLAW_GATEWAY_PORT}/tcp": container_port},
                volumes={
                    str(agent_dir): {"bind": "/home/node/.openclaw", "mode": "rw"},
                },
                environment={
                    "OPENCLAW_GATEWAY_TOKEN": str(uuid.uuid4()),
                },
                restart_policy={"Name": "unless-stopped"},
                labels={
                    "clawith.agent_id": str(agent.id),
                    "clawith.agent_name": agent.name,
                },
            )

            agent.container_id = container.id
            agent.container_port = container_port
            agent.status = "running"
            agent.last_active_at = datetime.now(timezone.utc)

            logger.info(f"Started container {container.id[:12]} for agent {agent.name} on port {container_port}")
            return container.id

        except DockerException as e:
            logger.error(f"Failed to start container for agent {agent.name}: {e}")
            agent.status = "error"
            return None

    async def stop_container(self, agent: Agent) -> bool:
        """Stop the agent's Docker container."""
        if not self.docker_client or not agent.container_id:
            agent.status = "stopped"
            return True

        try:
            container = self.docker_client.containers.get(agent.container_id)
            container.stop(timeout=10)
            agent.status = "stopped"
            logger.info(f"Stopped container {agent.container_id[:12]} for agent {agent.name}")
            return True
        except NotFound:
            agent.status = "stopped"
            agent.container_id = None
            return True
        except DockerException as e:
            logger.error(f"Failed to stop container: {e}")
            return False

    async def remove_container(self, agent: Agent) -> bool:
        """Stop and remove the agent's Docker container."""
        if not self.docker_client or not agent.container_id:
            return True

        try:
            container = self.docker_client.containers.get(agent.container_id)
            container.stop(timeout=10)
            container.remove()
            agent.container_id = None
            agent.container_port = None
            logger.info(f"Removed container for agent {agent.name}")
            return True
        except NotFound:
            agent.container_id = None
            return True
        except DockerException as e:
            logger.error(f"Failed to remove container: {e}")
            return False

    async def archive_agent_files(self, agent_id: uuid.UUID) -> Path:
        """Archive agent files to a backup location and return the archive directory."""
        storage = get_storage()
        aid = str(agent_id)
        archive_dir = Path(settings.AGENT_DATA_DIR) / "_archived"
        archive_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        dest = archive_dir / f"{agent_id}_{timestamp}"

        # Collect all file keys from storage
        all_keys = await _collect_storage_keys(aid)

        if all_keys:
            dest.mkdir(parents=True, exist_ok=True)
            for key in all_keys:
                content = await storage.read(key)
                rel = key.removeprefix(aid + "/")
                local_file = dest / rel
                local_file.parent.mkdir(parents=True, exist_ok=True)
                local_file.write_text(content, encoding="utf-8")

            # Delete originals from storage
            await storage.delete_prefix(aid)
            logger.info(f"Archived agent files to {dest}")
        else:
            dest.mkdir(parents=True, exist_ok=True)
        return dest

    def get_container_status(self, agent: Agent) -> dict:
        """Get real-time container status."""
        if not self.docker_client or not agent.container_id:
            return {"running": False, "status": agent.status}

        try:
            container = self.docker_client.containers.get(agent.container_id)
            return {
                "running": container.status == "running",
                "status": container.status,
                "ports": container.ports,
                "created": container.attrs.get("Created", ""),
            }
        except NotFound:
            return {"running": False, "status": "not_found"}
        except DockerException:
            return {"running": False, "status": "error"}


    async def clone_agent(self, db: AsyncSession, source: Agent, cloner: "User", name: str, copy_files: list[str] | None = None) -> Agent:
        """Clone an existing agent's configuration into a new agent owned by cloner.

        Args:
            copy_files: List of file categories to copy. Options: soul.md, memory, skills, workspace, HEARTBEAT.md.
                        Defaults to ["soul.md", "memory", "skills", "HEARTBEAT.md"].
        """
        from fastapi import HTTPException

        if source.agent_type == "openclaw":
            raise HTTPException(status_code=400, detail="Cannot clone openclaw-type agents")

        _valid_categories = {"soul.md", "memory", "skills", "workspace", "HEARTBEAT.md"}
        if copy_files is None:
            copy_files = ["soul.md", "memory", "skills", "HEARTBEAT.md"]
        invalid = set(copy_files) - _valid_categories
        if invalid:
            raise HTTPException(status_code=422, detail=f"Invalid file categories: {', '.join(sorted(invalid))}")

        from app.services.quota_guard import check_agent_creation_quota, QuotaExceeded
        try:
            await check_agent_creation_quota(cloner.id)
        except QuotaExceeded as e:
            raise HTTPException(status_code=403, detail=e.message)

        from datetime import timedelta, timezone as tz
        expires_at = datetime.now(tz.utc) + timedelta(hours=cloner.quota_agent_ttl_hours or 48)

        target_tenant_id = cloner.tenant_id

        max_llm_calls = 100
        default_max_triggers = 20
        default_min_poll = 5
        default_webhook_rate = 5
        default_heartbeat_interval = 240
        if target_tenant_id:
            from app.models.tenant import Tenant
            tenant_result = await db.execute(select(Tenant).where(Tenant.id == target_tenant_id))
            tenant = tenant_result.scalar_one_or_none()
            if tenant:
                max_llm_calls = tenant.default_max_llm_calls_per_day or 100
                default_max_triggers = tenant.default_max_triggers or 20
                default_min_poll = tenant.min_poll_interval_floor or 5
                default_webhook_rate = tenant.max_webhook_rate_ceiling or 5
                if tenant.min_heartbeat_interval_minutes and tenant.min_heartbeat_interval_minutes > default_heartbeat_interval:
                    default_heartbeat_interval = tenant.min_heartbeat_interval_minutes

        cloned = Agent(
            name=name,
            role_description=source.role_description,
            bio=source.bio,
            avatar_url=source.avatar_url,
            welcome_message=source.welcome_message,
            creator_id=cloner.id,
            tenant_id=target_tenant_id,
            agent_type=source.agent_type,
            primary_model_id=source.primary_model_id,
            fallback_model_id=source.fallback_model_id,
            autonomy_policy=dict(source.autonomy_policy) if source.autonomy_policy else None,
            max_tokens_per_day=source.max_tokens_per_day,
            max_tokens_per_month=source.max_tokens_per_month,
            context_window_size=source.context_window_size,
            max_tool_rounds=source.max_tool_rounds,
            max_triggers=default_max_triggers,
            min_poll_interval_min=default_min_poll,
            webhook_rate_limit=default_webhook_rate,
            heartbeat_enabled=source.heartbeat_enabled,
            heartbeat_interval_minutes=default_heartbeat_interval,
            heartbeat_active_hours=source.heartbeat_active_hours,
            timezone=source.timezone,
            template_id=source.template_id,
            source_agent_id=source.id,
            status="creating",
            expires_at=expires_at,
            max_llm_calls_per_day=max_llm_calls,
        )
        db.add(cloned)
        await db.flush()

        from app.models.participant import Participant
        db.add(Participant(
            type="agent", ref_id=cloned.id,
            display_name=cloned.name, avatar_url=cloned.avatar_url,
        ))

        from app.models.agent import AgentPermission
        db.add(AgentPermission(agent_id=cloned.id, scope_type="company", access_level="use"))
        await db.flush()

        await self.initialize_agent_files(db, cloned, personality="", boundaries="")

        storage = get_storage()
        src_aid = str(source.id)
        dst_aid = str(cloned.id)

        # Copy soul.md
        if "soul.md" in copy_files:
            source_soul_key = f"{src_aid}/soul.md"
            if await storage.exists(source_soul_key):
                soul_content = await storage.read(source_soul_key)
                await storage.write(f"{dst_aid}/soul.md", soul_content)

        # Copy memory/ directory
        if "memory" in copy_files:
            source_mem_prefix = f"{src_aid}/memory/"
            source_mem_keys = await _collect_storage_keys(source_mem_prefix)
            cloned_mem_dir = self._agent_dir(cloned.id) / "memory"
            for key in source_mem_keys:
                content = await storage.read(key)
                target_key = f"{dst_aid}/{key[len(src_aid) + 1:]}"
                rel = key[len(source_mem_prefix):]
                local_path = cloned_mem_dir / rel
                local_path.parent.mkdir(parents=True, exist_ok=True)
                await storage.write(target_key, content)

        # Copy skills/ directory
        if "skills" in copy_files:
            source_skills_prefix = f"{src_aid}/skills/"
            source_skill_keys = await _collect_storage_keys(source_skills_prefix)
            cloned_skills_dir = self._agent_dir(cloned.id) / "skills"
            for key in source_skill_keys:
                content = await storage.read(key)
                target_key = f"{dst_aid}/{key[len(src_aid) + 1:]}"
                rel = key[len(source_skills_prefix):]
                local_path = cloned_skills_dir / rel
                local_path.parent.mkdir(parents=True, exist_ok=True)
                await storage.write(target_key, content)

        # Copy workspace/ directory
        if "workspace" in copy_files:
            source_ws_prefix = f"{src_aid}/workspace/"
            source_ws_keys = await _collect_storage_keys(source_ws_prefix)
            cloned_ws_dir = self._agent_dir(cloned.id) / "workspace"
            for key in source_ws_keys:
                content = await storage.read(key)
                target_key = f"{dst_aid}/{key[len(src_aid) + 1:]}"
                rel = key[len(source_ws_prefix):]
                local_path = cloned_ws_dir / rel
                local_path.parent.mkdir(parents=True, exist_ok=True)
                await storage.write(target_key, content)

        # Copy HEARTBEAT.md
        if "HEARTBEAT.md" in copy_files:
            source_hb_key = f"{src_aid}/HEARTBEAT.md"
            if await storage.exists(source_hb_key):
                hb_content = await storage.read(source_hb_key)
                await storage.write(f"{dst_aid}/HEARTBEAT.md", hb_content)

        from app.models.tool import AgentTool
        tool_result = await db.execute(select(AgentTool).where(AgentTool.agent_id == source.id))
        for at in tool_result.scalars().all():
            db.add(AgentTool(
                agent_id=cloned.id,
                tool_id=at.tool_id,
                enabled=at.enabled,
                config=at.config,
                source=at.source,
                installed_by_agent_id=None,
            ))
        await db.flush()

        await self.start_container(db, cloned)
        await db.flush()

        logger.info(f"Cloned agent {source.name} ({source.id}) -> {cloned.name} ({cloned.id})")
        return cloned


agent_manager = AgentManager()
