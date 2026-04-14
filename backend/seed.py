"""Seed data script — creates initial data and delegates to shared seeders.

Called by `setup.sh` for first-time database population. Also runs the
shared seeder functions that `main.py` uses on every startup, so both
pathways produce the same result.

Idempotent: safe to re-run. DB-level dedup + seeder state guards prevent
duplicate data on repeated execution.
"""

import asyncio
import sys
sys.path.insert(0, ".")

from app.config import get_settings
from app.core.security import hash_password
from app.database import Base, engine, async_session
# Import ALL models so Base.metadata.create_all can resolve all FKs
from app.models.tenant import Tenant  # noqa: F401 — must be before user
from app.models.user import User
from app.models.agent import AgentTemplate  # noqa: F401
from app.models.llm import LLMModel  # noqa: F401
from app.models.task import Task  # noqa: F401
from app.models.skill import Skill  # noqa: F401
from app.models.tool import Tool  # noqa: F401
from app.models.participant import Participant  # noqa: F401
from app.models.channel_config import ChannelConfig  # noqa: F401
from app.models.schedule import AgentSchedule  # noqa: F401
from app.models.audit import AuditLog  # noqa: F401
from app.models.plaza import PlazaPost, PlazaComment  # noqa: F401
from app.models.activity_log import AgentActivityLog  # noqa: F401
from app.models.org import OrgDepartment, OrgMember, AgentRelationship, AgentAgentRelationship  # noqa: F401
from app.models.system_settings import SystemSetting  # noqa: F401
from app.models.invitation_code import InvitationCode  # noqa: F401


async def seed():
    """Create tables and seed initial data."""
    settings = get_settings()

    # Create all tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("✅ Database tables created")

    from sqlalchemy import select, func
    from app.services.seeder_state import is_seeder_done, mark_seeder_done

    async with async_session() as db:
        # 1. Default company (tenant)
        if await is_seeder_done("seeder:tenant", 1):
            print("✅ Tenant already seeded")
        else:
            existing_tenant = await db.execute(select(Tenant).where(Tenant.slug == "default"))
            if not existing_tenant.scalar_one_or_none():
                db.add(Tenant(name="Default", slug="default", im_provider="web_only"))
                print("✅ Default company created")
            await mark_seeder_done("seeder:tenant", 1)

        # 2. Built-in templates (English)
        if await is_seeder_done("seeder:seed-py-templates", 1):
            print("✅ Seed-py templates already created")
        else:
            templates = [
                {
                    "name": "Research Assistant",
                    "description": "A digital employee focused on information gathering, competitive analysis, and industry research",
                    "icon": "🔬",
                    "category": "research",
                    "soul_template": "## Identity\nYou are a professional research assistant, skilled in information gathering and analysis.\n\n## Personality\n- Rigorous and detail-oriented\n- Data-driven\n- Objective and impartial\n\n## Boundaries\n- All sources must be cited\n- No subjective judgments without evidence",
                    "is_builtin": True,
                },
                {
                    "name": "Project Management Assistant",
                    "description": "A digital employee responsible for project progress tracking, task assignment, and follow-up reminders",
                    "icon": "📋",
                    "category": "management",
                    "soul_template": "## Identity\nYou are an efficient project management assistant.\n\n## Personality\n- Well-organized\n- Proactive in follow-ups\n- Deadline-focused\n\n## Boundaries\n- Do not modify project plans without approval\n- Major decisions require confirmation",
                    "is_builtin": True,
                },
                {
                    "name": "Customer Service Assistant",
                    "description": "A digital employee that handles customer inquiries, FAQ responses, and ticket management",
                    "icon": "💬",
                    "category": "support",
                    "soul_template": "## Identity\nYou are a friendly and professional customer service assistant.\n\n## Personality\n- Warm and welcoming\n- Patient and detail-oriented\n- Solution-oriented\n\n## Boundaries\n- Do not promise beyond your authority\n- Escalate sensitive issues to humans",
                    "is_builtin": True,
                },
                {
                    "name": "Data Analyst",
                    "description": "A digital employee for data queries, report generation, and trend analysis",
                    "icon": "📊",
                    "category": "analytics",
                    "soul_template": "## Identity\nYou are a data analysis expert.\n\n## Personality\n- Precise and rigorous\n- Skilled in visualization\n- Strong analytical insight\n\n## Boundaries\n- Data security comes first\n- Never leak raw data",
                    "is_builtin": True,
                },
                {
                    "name": "Content Creation Assistant",
                    "description": "A digital employee for copywriting, content review, and social media management",
                    "icon": "✍️",
                    "category": "content",
                    "soul_template": "## Identity\nYou are a creative content assistant.\n\n## Personality\n- Highly creative\n- Strong writing skills\n- Marketing-savvy\n\n## Boundaries\n- Follow brand voice guidelines\n- Content must be reviewed before publishing",
                    "is_builtin": True,
                },
            ]

            for tmpl in templates:
                existing = await db.execute(
                    select(AgentTemplate).where(AgentTemplate.name == tmpl["name"])
                )
                if not existing.scalar_one_or_none():
                    db.add(AgentTemplate(**tmpl))
                    print(f"✅ Template created: {tmpl['icon']} {tmpl['name']}")
            await mark_seeder_done("seeder:seed-py-templates", 1)

        # 3. Demo agents for platform admin (if admin has zero agents)
        if await is_seeder_done("seeder:agents", 1):
            print("✅ Default agents already seeded")
        else:
            from app.models.agent import Agent
            admin_result = await db.execute(select(User).where(User.role == "platform_admin"))
            admin_user = admin_result.scalar_one_or_none()
            if admin_user:
                agent_count_result = await db.execute(
                    select(func.count()).select_from(Agent).where(Agent.creator_id == admin_user.id)
                )
                agent_count = agent_count_result.scalar()
                if agent_count == 0:
                    demo_agents = [
                        {
                            "name": "Morty",
                            "role_description": "Research Assistant — focused on information gathering, competitive analysis, and industry research.",
                            "status": "idle",
                            "heartbeat_enabled": True,
                        },
                        {
                            "name": "Meeseeks",
                            "role_description": "Task Executor — focuses on completing specific tasks assigned by the user efficiently.",
                            "status": "idle",
                            "heartbeat_enabled": True,
                        },
                    ]
                    for agent_data in demo_agents:
                        agent = Agent(
                            creator_id=admin_user.id,
                            tenant_id=admin_user.tenant_id,
                            **agent_data,
                        )
                        db.add(agent)
                        await db.flush()

                        # Initialize workspace directories
                        from pathlib import Path
                        ws_root = Path(settings.AGENT_DATA_DIR) / str(agent.id)
                        try:
                            for sub in ["workspace", "memory", "skills"]:
                                (ws_root / sub).mkdir(parents=True, exist_ok=True)
                            soul_path = ws_root / "soul.md"
                            if not soul_path.exists():
                                soul_path.write_text(f"# {agent.name}\n\n{agent.role_description}\n", encoding="utf-8")
                            mem_path = ws_root / "memory" / "memory.md"
                            if not mem_path.exists():
                                mem_path.write_text("# Memory\n\n_Record important information and knowledge here._\n", encoding="utf-8")
                        except OSError:
                            pass  # AGENT_DATA_DIR may not be writable
                        print(f"✅ Demo agent created: {agent.name}")
            await mark_seeder_done("seeder:agents", 1)

        await db.commit()

    # 4. Run shared seeders (tools, English templates, skills, etc.)
    from app.services.tool_seeder import seed_builtin_tools, clean_orphaned_mcp_tools
    from app.services.template_seeder import seed_agent_templates
    from app.services.skill_seeder import seed_skills, push_default_skills_to_existing_agents

    await seed_builtin_tools()
    await clean_orphaned_mcp_tools()
    await seed_agent_templates()
    await seed_skills()
    await push_default_skills_to_existing_agents()

    print("\n🎉 Seed data complete!")


if __name__ == "__main__":
    asyncio.run(seed())
