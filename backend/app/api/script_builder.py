"""Script Builder API — AI-assisted authoring of Salesforce Agentforce Agent Scripts.

Eight endpoints (design §6): a tenant/user-isolated conversation subsystem that
streams Agent Script generation, scores scripts (Analyze), and materializes a
script into a runnable Evolver agent (Apply As Agent, design §7).
"""

import asyncio
import json
import logging
import re
import shutil
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.security import get_current_user
from app.database import async_session, get_db
from app.models.agent import Agent, AgentPermission
from app.models.llm import LLMModel
from app.models.participant import Participant
from app.models.script_builder import (
    AgentScriptVersion,
    ScriptConversation,
    ScriptMessage,
)
from app.models.skill import Skill
from app.models.tenant import Tenant
from app.models.tool import AgentTool, Tool
from app.schemas.script_builder import (
    ApplyAsAgentRequest,
    ScriptAnalyzeRequest,
    ScriptConversationCreate,
    ScriptConversationOut,
    ScriptMessageOut,
    ScriptMessageSend,
)
from app.services.agent_manager import agent_manager
from app.services.agent_script_prompt import (
    AGENT_SCRIPT_SYSTEM_PROMPT,
    ANALYZE_SYSTEM_PROMPT,
)
from app.services.llm.utils import (
    LLMMessage,
    create_llm_client,
    get_max_tokens,
    get_model_api_key,
)
from app.services.storage_runtime.facade import get_storage_backend

router = APIRouter(prefix="/script-builder", tags=["script-builder"])

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
async def _get_llm_model(db: AsyncSession, user) -> LLMModel:
    """Pick the tenant's enabled LLM model, falling back to a global one."""
    tenant_id = getattr(user, "tenant_id", None)
    if tenant_id is not None:
        result = await db.execute(
            select(LLMModel)
            .where(LLMModel.tenant_id == tenant_id, LLMModel.enabled.is_(True))
            .order_by(LLMModel.created_at.asc())
            .limit(1)
        )
        model = result.scalar_one_or_none()
        if model is not None:
            return model
    result = await db.execute(
        select(LLMModel)
        .where(LLMModel.tenant_id.is_(None), LLMModel.enabled.is_(True))
        .order_by(LLMModel.created_at.asc())
        .limit(1)
    )
    model = result.scalar_one_or_none()
    if model is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "No LLM model available")
    return model


async def _build_tools_skills_context(db: AsyncSession, tenant_id) -> str:
    """Build the authoritative capability registry appended to the system prompt.

    Per design §6.2 / Appendix B: lists the tenant's real tools & skills, the
    ABSOLUTE RULES, any names that exist in BOTH registries, and worked examples
    so the LLM cannot confuse ``tool://`` with ``skill://``. When the company has
    no tools/skills at all, returns a strong prohibition against fabricating any.
    """
    tools_result = await db.execute(
        select(Tool)
        .where(Tool.enabled.is_(True), or_(Tool.tenant_id == tenant_id, Tool.tenant_id.is_(None)))
        .order_by(Tool.category.asc(), Tool.name.asc())
    )
    tools = list(tools_result.scalars().all())

    skills_result = await db.execute(
        select(Skill)
        .where(or_(Skill.tenant_id == tenant_id, Skill.tenant_id.is_(None)))
        .order_by(Skill.category.asc(), Skill.folder_name.asc())
    )
    skills = list(skills_result.scalars().all())

    if not tools and not skills:
        return (
            "\n\n# Available Platform Tools & Skills (Authoritative Registry)\n"
            "IMPORTANT: This company currently has NO tools and NO skills installed. "
            "You MUST NOT generate any `tool://` or `skill://` references in the script. "
            "If the user needs a capability, tell them explicitly to install the relevant "
            "tool or skill in Settings → Tools / Skills before it can be referenced.\n"
        )

    tool_names = {t.name for t in tools}
    skill_names = {s.folder_name for s in skills}
    overlap = sorted(tool_names & skill_names)

    lines: list[str] = [
        "",
        "# Available Platform Tools & Skills (Authoritative Registry)",
        "These are TWO INDEPENDENT registries. A name in the Tools list is NOT a skill, "
        "and a name in the Skills list is NOT a tool. They are different namespaces.",
        "",
        "## ABSOLUTE RULES",
        "1. Every executable action's target MUST be either `tool://<name>` (from the "
        "Tools list) or `skill://<folder_name>` (from the Skills list).",
        "2. A name that appears in the Skills list MUST be referenced with `skill://`.",
        "3. A name that appears in the Tools list MUST be referenced with `tool://`.",
        "4. NEVER invent names outside these lists. If the user asks for a capability "
        "that is missing, say so explicitly and stop — do not fabricate a reference.",
        "5. `flow://`, `apex://`, and `generatePromptResponse://` are NOT supported. "
        "Do not emit them.",
        "",
        f"## Available Tools ({len(tools)} total) — use as `tool://<name>`",
    ]
    for t in tools:
        desc = (t.description or "").strip().replace("\n", " ")[:120]
        lines.append(f"- tool://{t.name} (category: {t.category}) — {desc}")

    lines.append("")
    lines.append(f"## Available Skills ({len(skills)} total) — use as `skill://<folder_name>`")
    for s in skills:
        desc = (s.description or "").strip().replace("\n", " ")[:120]
        lines.append(f"- skill://{s.folder_name} (category: {s.category}) — {desc}")

    if overlap:
        lines.append("")
        lines.append("## ⚠ Names that exist in BOTH registries")
        for name in overlap:
            lines.append(f"- {name} — exists as both tool://{name} AND skill://{name}")

    # Worked examples — skill-only first, then tool-only.
    skill_only = sorted(skill_names - tool_names)
    tool_only = sorted(tool_names - skill_names)
    lines.append("")
    lines.append("## Quick correctness check (worked examples)")
    for name in skill_only[:5]:
        lines.append(f"- {name} is a SKILL → `skill://{name}` ✅  (tool://{name} ❌)")
    for name in tool_only[:5]:
        lines.append(f"- {name} is a TOOL  → `tool://{name}` ✅  (skill://{name} ❌)")

    return "\n".join(lines) + "\n"


_TOOL_REF_RE = re.compile(r'target:\s*["\']?tool://([^"\'}}\s]+)', re.IGNORECASE)
_SKILL_REF_RE = re.compile(r'target:\s*["\']?skill://([^"\'}}\s]+)', re.IGNORECASE)


def _extract_referenced_targets(script: str) -> tuple[set[str], set[str]]:
    """Extract referenced ``tool://`` and ``skill://`` names from a script (§7.3)."""
    tool_refs = set(_TOOL_REF_RE.findall(script or ""))
    skill_refs = set(_SKILL_REF_RE.findall(script or ""))
    return tool_refs, skill_refs


_NAME_RE = re.compile(r"^\s*agent(?:_name)?\s*:\s*[\"']?(.*?)[\"']?\s*$", re.IGNORECASE)
_DESC_RE = re.compile(r"^\s*description\s*:\s*[\"']?(.*?)[\"']?\s*$", re.IGNORECASE)


def _parse_script_metadata(script: str) -> tuple[str, str]:
    """Extract agent name + description from the script header (§7.1).

    Matches ``agent_name:`` / ``agent:`` (case-insensitive, quotes stripped) for the
    name and ``description:`` for the description. Defaults name to
    ``"Evolver Agent"`` and description to ``""``; truncates to 100 / 500 chars.
    """
    name = ""
    description = ""
    for line in (script or "").splitlines():
        if not name:
            m = _NAME_RE.match(line)
            if m and m.group(1).strip():
                name = m.group(1).strip()
        if not description:
            m = _DESC_RE.match(line)
            if m and m.group(1).strip():
                description = m.group(1).strip()
        if name and description:
            break
    if not name:
        name = "Evolver Agent"
    return name[:100], description[:500]


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@router.get("/context")
async def get_context(
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the tenant's available tools + skills for the context bubble."""
    tenant_id = current_user.tenant_id

    tools_result = await db.execute(
        select(Tool)
        .where(Tool.enabled.is_(True), or_(Tool.tenant_id == tenant_id, Tool.tenant_id.is_(None)))
        .order_by(Tool.category.asc(), Tool.name.asc())
    )
    tools = [
        {
            "name": t.name,
            "display_name": t.display_name,
            "category": t.category,
            "description": (t.description or "")[:200],
            "icon": t.icon,
        }
        for t in tools_result.scalars().all()
    ]

    skills_result = await db.execute(
        select(Skill)
        .where(or_(Skill.tenant_id == tenant_id, Skill.tenant_id.is_(None)))
        .order_by(Skill.name.asc())
    )
    skills = [
        {
            "name": s.name,
            "folder_name": s.folder_name,
            "category": s.category,
            "description": (s.description or "")[:200],
            "icon": s.icon,
        }
        for s in skills_result.scalars().all()
    ]

    return {"tools": tools, "skills": skills}


@router.get("/conversations", response_model=list[ScriptConversationOut])
async def list_conversations(
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List the current user's conversations, oldest first."""
    result = await db.execute(
        select(ScriptConversation)
        .where(
            ScriptConversation.user_id == current_user.id,
            ScriptConversation.tenant_id == current_user.tenant_id,
        )
        .order_by(ScriptConversation.created_at.asc())
    )
    return list(result.scalars().all())


@router.post(
    "/conversations",
    response_model=ScriptConversationOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_conversation(
    body: ScriptConversationCreate,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new Script Builder conversation for the current user."""
    conversation = ScriptConversation(
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        title=body.title,
    )
    db.add(conversation)
    await db.flush()
    await db.refresh(conversation)
    return conversation


@router.delete("/conversations/{conv_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conv_id: int,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a conversation owned by the current user (cascades messages)."""
    result = await db.execute(
        select(ScriptConversation).where(
            ScriptConversation.id == conv_id,
            ScriptConversation.user_id == current_user.id,
        )
    )
    conversation = result.scalar_one_or_none()
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    await db.delete(conversation)


@router.get(
    "/conversations/{conv_id}/messages",
    response_model=list[ScriptMessageOut],
)
async def list_messages(
    conv_id: int,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List a conversation's messages, oldest first (owner-gated)."""
    conv_result = await db.execute(
        select(ScriptConversation).where(
            ScriptConversation.id == conv_id,
            ScriptConversation.user_id == current_user.id,
        )
    )
    if conv_result.scalar_one_or_none() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")

    result = await db.execute(
        select(ScriptMessage)
        .where(ScriptMessage.conversation_id == conv_id)
        .order_by(ScriptMessage.created_at.asc())
    )
    return list(result.scalars().all())


@router.post("/conversations/{conv_id}/messages")
async def send_message(
    conv_id: int,
    body: ScriptMessageSend,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Send a user message and stream the LLM-generated Agent Script via SSE.

    The user message is persisted first; full history is reloaded as context.
    Streaming is decoupled via an ``asyncio.Queue`` so the assistant message can
    be persisted in a fresh session after the stream completes (design §6.3.6).
    """
    conv_result = await db.execute(
        select(ScriptConversation).where(
            ScriptConversation.id == conv_id,
            ScriptConversation.user_id == current_user.id,
        )
    )
    if conv_result.scalar_one_or_none() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")

    # Persist the user message and commit so it survives the streaming session.
    db.add(ScriptMessage(conversation_id=conv_id, role="user", content=body.content))
    await db.commit()

    # Reload full history (oldest first) for context.
    history_result = await db.execute(
        select(ScriptMessage)
        .where(ScriptMessage.conversation_id == conv_id)
        .order_by(ScriptMessage.created_at.asc())
    )
    history = list(history_result.scalars().all())

    model = await _get_llm_model(db, current_user)
    tools_skills_context = await _build_tools_skills_context(db, current_user.tenant_id)
    system_prompt = AGENT_SCRIPT_SYSTEM_PROMPT + "\n\n" + tools_skills_context

    chat_messages: list[LLMMessage] = [LLMMessage(role="system", content=system_prompt)]
    for m in history:
        chat_messages.append(LLMMessage(role=m.role, content=m.content))

    provider = model.provider
    api_key = get_model_api_key(model)
    model_name = model.model
    base_url = model.base_url
    timeout = float(model.request_timeout) if model.request_timeout else 120.0
    max_tokens = model.max_output_tokens or get_max_tokens(provider, model_name)
    temperature = model.temperature

    queue: asyncio.Queue = asyncio.Queue()
    full_response = {"text": ""}

    async def _run_stream() -> None:
        client = create_llm_client(provider, api_key, model_name, base_url=base_url, timeout=timeout)
        try:
            async def on_chunk(text: str) -> None:
                full_response["text"] += text
                await queue.put(json.dumps({"content": text}))

            await client.stream(
                chat_messages,
                temperature=temperature,
                max_tokens=max_tokens,
                on_chunk=on_chunk,
            )
        except Exception as e:  # noqa: BLE001 — surface any failure to the client.
            logger.warning(f"Script Builder stream failed for conversation {conv_id}: {e}")
            await queue.put(json.dumps({"error": str(e)}))
        finally:
            if hasattr(client, "close"):
                try:
                    await client.close()
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"LLM client close failed: {e}")
            # Persist the assistant message in a fresh session so it does not
            # conflict with the request-scoped session's lifecycle.
            if full_response["text"]:
                try:
                    async with async_session() as s2:
                        s2.add(
                            ScriptMessage(
                                conversation_id=conv_id,
                                role="assistant",
                                content=full_response["text"],
                            )
                        )
                        await s2.commit()
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"Failed to persist assistant message for {conv_id}: {e}")
            await queue.put(None)

    task = asyncio.create_task(_run_stream())

    async def generate():
        try:
            while True:
                data = await queue.get()
                if data is None:
                    yield 'data: {"done": true}\n\n'
                    break
                yield f"data: {data}\n\n"
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/analyze")
async def analyze_script(
    body: ScriptAnalyzeRequest,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Score a script across 6 dimensions via a non-streaming LLM call (§6.3.7)."""
    model = await _get_llm_model(db, current_user)
    client = create_llm_client(
        model.provider,
        get_model_api_key(model),
        model.model,
        base_url=model.base_url,
        timeout=float(model.request_timeout) if model.request_timeout else 120.0,
    )
    try:
        messages = [
            LLMMessage(role="system", content=ANALYZE_SYSTEM_PROMPT),
            LLMMessage(
                role="user",
                content=f"Please analyze this Agent Script:\n\n```ascript\n{body.script}\n```",
            ),
        ]
        response = await client.complete(
            messages,
            max_tokens=get_max_tokens(model.provider, model.model),
        )
        raw = response.content or ""
        match = re.search(r"\{[\s\S]*\}", raw)
        if not match:
            raise ValueError("No JSON object found in analysis response")
        return json.loads(match.group(0))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Script analysis failed: {e}")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Failed to analyze script")
    finally:
        if hasattr(client, "close"):
            try:
                await client.close()
            except Exception as e:  # noqa: BLE001
                logger.warning(f"LLM client close failed: {e}")


@router.post("/apply-as-agent")
async def apply_as_agent(
    body: ApplyAsAgentRequest,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Materialize a script into a runnable Evolver agent (design §7).

    Runs a strict pre-flight on every referenced ``tool://`` / ``skill://``,
    creates the Agent + Participant + permission + script version, installs the
    referenced tools and skills (plus default skills), writes the script to
    ``soul.md``, and rolls back the file workspace if the DB commit fails.
    """
    target_tenant_id = current_user.tenant_id
    model = await _get_llm_model(db, current_user)

    # --- Pre-flight validation (§7.3) ------------------------------------- #
    tool_refs, skill_refs = _extract_referenced_targets(body.script)

    existing_tool_names: set[str] = set()
    if tool_refs:
        res = await db.execute(
            select(Tool.name).where(
                Tool.enabled.is_(True),
                or_(Tool.tenant_id == target_tenant_id, Tool.tenant_id.is_(None)),
                Tool.name.in_(tool_refs),
            )
        )
        existing_tool_names = set(res.scalars().all())
    missing_tools = sorted(tool_refs - existing_tool_names)

    existing_skill_names: set[str] = set()
    if skill_refs:
        res = await db.execute(
            select(Skill.folder_name).where(
                or_(Skill.tenant_id == target_tenant_id, Skill.tenant_id.is_(None)),
                Skill.folder_name.in_(skill_refs),
            )
        )
        existing_skill_names = set(res.scalars().all())
    missing_skills = sorted(skill_refs - existing_skill_names)

    if missing_tools or missing_skills:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={
                "message": (
                    "Cannot create agent — the script references capabilities that do not "
                    "exist in this company's platform. "
                    + (f"Missing tools: {', '.join(missing_tools)}." if missing_tools else "")
                    + (" | " if missing_tools and missing_skills else "")
                    + (f"Missing skills: {', '.join(missing_skills)}." if missing_skills else "")
                    + " Install them in Settings → Tools / Skills, or edit the script to "
                    "remove the missing references, then try again."
                ),
                "missing_tools": missing_tools,
                "missing_skills": missing_skills,
            },
        )

    # --- Parse metadata + build name (§7.1, §7.2) ------------------------- #
    meta_name, meta_description = _parse_script_metadata(body.script)
    raw_name = body.name or meta_name
    parts = [p for p in re.split(r"[\s_\-]+", raw_name) if p]
    agent_name = "".join(p[:1].upper() + p[1:] for p in parts) if parts else "EvolverAgent"
    agent_name = agent_name[:100]
    agent_desc = meta_description

    # --- Tenant quota/heartbeat defaults (§7.4) --------------------------- #
    max_llm_calls_per_day = 100
    heartbeat_interval_minutes = 240
    tenant_result = await db.execute(select(Tenant).where(Tenant.id == target_tenant_id))
    tenant = tenant_result.scalar_one_or_none()
    if tenant is not None:
        max_llm_calls_per_day = getattr(tenant, "default_max_llm_calls_per_day", None) or 100
        heartbeat_floor = getattr(tenant, "min_heartbeat_interval_minutes", None) or 240
        heartbeat_interval_minutes = max(240, heartbeat_floor)

    ttl_hours = getattr(current_user, "quota_agent_ttl_hours", None) or 48
    expires_at = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)

    agent = Agent(
        name=agent_name,
        role_description=agent_desc,
        creator_id=current_user.id,
        tenant_id=target_tenant_id,
        agent_type="evolver",
        primary_model_id=model.id,
        status="idle",
        expires_at=expires_at,
        max_llm_calls_per_day=max_llm_calls_per_day,
        heartbeat_interval_minutes=heartbeat_interval_minutes,
    )
    db.add(agent)
    await db.flush()  # populate agent.id

    # --- Auxiliary records (§7.5) ----------------------------------------- #
    db.add(
        Participant(
            type="agent",
            ref_id=agent.id,
            display_name=agent.name,
            avatar_url=agent.avatar_url,
        )
    )
    db.add(AgentPermission(agent_id=agent.id, scope_type="company", access_level="use"))
    await db.flush()

    db.add(
        AgentScriptVersion(
            agent_id=agent.id,
            version=1,
            folder="initial",
            content=body.script,
            source="script_builder",
        )
    )

    # --- Install tools (§7.6) --------------------------------------------- #
    installed_tools: list[str] = []
    tool_names, _ = _extract_referenced_targets(body.script)
    if tool_names:
        res = await db.execute(
            select(Tool).where(
                Tool.enabled.is_(True),
                or_(Tool.tenant_id == target_tenant_id, Tool.tenant_id.is_(None)),
                Tool.name.in_(tool_names),
            )
        )
        for t in res.scalars().all():
            db.add(AgentTool(agent_id=agent.id, tool_id=t.id, enabled=True, source="system"))
            installed_tools.append(t.name)

    # --- Initialize agent workspace + write soul.md (§7.7) ---------------- #
    try:
        await agent_manager.initialize_agent_files(
            db, agent, personality=meta_description or agent_desc, boundaries=""
        )
        storage = get_storage_backend()
        await storage.write_text(f"{agent.id}/soul.md", body.script)

        # --- Install skills + default skills (§7.8) ----------------------- #
        installed_skills: list[str] = []
        _, skill_names = _extract_referenced_targets(body.script)
        installed_folders: set[str] = set()

        referenced_skills: list[Skill] = []
        if skill_names:
            res = await db.execute(
                select(Skill)
                .options(selectinload(Skill.files))
                .where(
                    or_(Skill.tenant_id == target_tenant_id, Skill.tenant_id.is_(None)),
                    Skill.folder_name.in_(skill_names),
                )
            )
            referenced_skills = list(res.scalars().all())

        default_skills: list[Skill] = []
        res = await db.execute(
            select(Skill)
            .options(selectinload(Skill.files))
            .where(
                Skill.is_default.is_(True),
                or_(Skill.tenant_id == target_tenant_id, Skill.tenant_id.is_(None)),
            )
        )
        default_skills = list(res.scalars().all())

        for skill in referenced_skills + default_skills:
            if skill.folder_name in installed_folders:
                continue
            installed_folders.add(skill.folder_name)
            for sf in skill.files:
                if ".." in (sf.path or ""):
                    continue  # path-traversal guard
                try:
                    await storage.write_text(
                        f"{agent.id}/skills/{skill.folder_name}/{sf.path}",
                        sf.content or "",
                    )
                except Exception as e:  # noqa: BLE001 — copy failure must not abort.
                    logger.warning(
                        f"Failed to copy skill file {skill.folder_name}/{sf.path}: {e}"
                    )
            installed_skills.append(skill.folder_name)

        await db.commit()
    except Exception:
        # Roll back the file workspace that initialize_agent_files created.
        try:
            shutil.rmtree(agent_manager._agent_dir(agent.id), ignore_errors=True)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Failed to clean agent dir for {agent.id}: {e}")
        raise

    await db.refresh(agent)
    return {
        "agent_id": str(agent.id),
        "agent_name": agent.name,
        "installed_tools": installed_tools,
        "installed_skills": installed_skills,
    }
