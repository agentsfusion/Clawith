"""Script Builder API — standalone Agent Script generation with SSE streaming."""

import asyncio
import json
import re
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db, async_session
from app.models.user import User
from app.models.llm import LLMModel
from app.models.agent import Agent, AgentPermission
from app.models.participant import Participant
from app.models.script_builder import ScriptConversation, ScriptMessage
from app.models.tool import Tool, AgentTool
from app.models.skill import Skill
from app.models.evolver import AgentScriptVersion
from app.api.auth import get_current_user
from app.schemas.schemas import (
    ScriptConversationCreate,
    ScriptConversationOut,
    ScriptMessageSend,
    ScriptMessageOut,
    ScriptAnalyzeRequest,
)
from app.services.agent_script_prompt import AGENT_SCRIPT_SYSTEM_PROMPT, ANALYZE_SYSTEM_PROMPT
from app.services.llm.client import create_llm_client, LLMMessage, get_max_tokens

router = APIRouter(prefix="/script-builder", tags=["script-builder"])


async def _build_tools_skills_context(db: AsyncSession, tenant_id) -> str:
    """Build a context string listing available tools and skills for the tenant."""
    tool_result = await db.execute(
        select(Tool).where(Tool.enabled == True).order_by(Tool.category, Tool.name)
    )
    tools = tool_result.scalars().all()

    skill_result = await db.execute(
        select(Skill).where(
            (Skill.tenant_id == tenant_id) | (Skill.tenant_id.is_(None))
        ).order_by(Skill.name)
    )
    skills = skill_result.scalars().all()

    if not tools and not skills:
        return ""

    lines = ["\n\n# Available Platform Tools & Skills",
             "When generating Agent Scripts, reference these REAL tools and skills that are installed in this company's platform.",
             "Use their exact names as action targets in the Agent Script.\n"]

    if tools:
        lines.append("## Available Tools")
        for t in tools:
            desc = f" — {t.description[:120]}" if t.description else ""
            lines.append(f"- **{t.name}** ({t.category}){desc}")
        lines.append("")

    if skills:
        lines.append("## Available Skills")
        for s in skills:
            desc = f" — {s.description[:120]}" if s.description else ""
            lines.append(f"- **{s.folder_name}** ({s.category}){desc}")
        lines.append("")

    lines.append("## Integration Guidelines")
    lines.append("- In `actions:` blocks, use `target: \"tool://<tool_name>\"` to reference platform tools")
    lines.append("- In `actions:` blocks, use `target: \"skill://<skill_folder_name>\"` to reference platform skills")
    lines.append("- Only reference tools/skills from the lists above — do not invent non-existent ones")
    lines.append("- If a user's requirement needs a tool that isn't available, mention it and suggest alternatives\n")

    return "\n".join(lines)


async def _get_llm_model(db: AsyncSession, user: User) -> LLMModel:
    result = await db.execute(
        select(LLMModel)
        .where(LLMModel.tenant_id == user.tenant_id, LLMModel.enabled == True)
        .order_by(LLMModel.created_at)
        .limit(1)
    )
    model = result.scalar_one_or_none()
    if not model:
        result2 = await db.execute(
            select(LLMModel)
            .where(LLMModel.tenant_id.is_(None), LLMModel.enabled == True)
            .order_by(LLMModel.created_at)
            .limit(1)
        )
        model = result2.scalar_one_or_none()
    if not model:
        raise HTTPException(status_code=503, detail="No LLM model available")
    return model


@router.get("/context")
async def get_tools_skills_context(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return available tools and skills for the current tenant."""
    tool_result = await db.execute(
        select(Tool).where(Tool.enabled == True).order_by(Tool.category, Tool.name)
    )
    tools = tool_result.scalars().all()

    skill_result = await db.execute(
        select(Skill).where(
            (Skill.tenant_id == current_user.tenant_id) | (Skill.tenant_id.is_(None))
        ).order_by(Skill.name)
    )
    skills = skill_result.scalars().all()

    return {
        "tools": [
            {"name": t.name, "display_name": t.display_name, "category": t.category,
             "description": t.description[:200] if t.description else "", "icon": t.icon}
            for t in tools
        ],
        "skills": [
            {"name": s.name, "folder_name": s.folder_name, "category": s.category,
             "description": s.description[:200] if s.description else "", "icon": s.icon}
            for s in skills
        ],
    }


@router.get("/conversations", response_model=list[ScriptConversationOut])
async def list_conversations(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ScriptConversation)
        .where(
            ScriptConversation.user_id == current_user.id,
            ScriptConversation.tenant_id == current_user.tenant_id,
        )
        .order_by(ScriptConversation.created_at)
    )
    convs = result.scalars().all()
    return [
        ScriptConversationOut(id=c.id, title=c.title, createdAt=c.created_at)
        for c in convs
    ]


@router.post("/conversations", status_code=201, response_model=ScriptConversationOut)
async def create_conversation(
    body: ScriptConversationCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    conv = ScriptConversation(
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        title=body.title,
    )
    db.add(conv)
    await db.commit()
    await db.refresh(conv)
    return ScriptConversationOut(id=conv.id, title=conv.title, createdAt=conv.created_at)


@router.delete("/conversations/{conv_id}", status_code=204)
async def delete_conversation(
    conv_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ScriptConversation).where(
            ScriptConversation.id == conv_id,
            ScriptConversation.user_id == current_user.id,
        )
    )
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    await db.delete(conv)
    await db.commit()


@router.get("/conversations/{conv_id}/messages", response_model=list[ScriptMessageOut])
async def list_messages(
    conv_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ScriptConversation).where(
            ScriptConversation.id == conv_id,
            ScriptConversation.user_id == current_user.id,
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Conversation not found")

    msg_result = await db.execute(
        select(ScriptMessage)
        .where(ScriptMessage.conversation_id == conv_id)
        .order_by(ScriptMessage.created_at)
    )
    msgs = msg_result.scalars().all()
    return [
        ScriptMessageOut(id=m.id, role=m.role, content=m.content, createdAt=m.created_at)
        for m in msgs
    ]


@router.post("/conversations/{conv_id}/messages")
async def send_message(
    conv_id: int,
    body: ScriptMessageSend,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ScriptConversation).where(
            ScriptConversation.id == conv_id,
            ScriptConversation.user_id == current_user.id,
        )
    )
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    user_msg = ScriptMessage(conversation_id=conv_id, role="user", content=body.content)
    db.add(user_msg)
    await db.commit()

    msg_result = await db.execute(
        select(ScriptMessage)
        .where(ScriptMessage.conversation_id == conv_id)
        .order_by(ScriptMessage.created_at)
    )
    history = msg_result.scalars().all()

    llm_model = await _get_llm_model(db, current_user)

    tools_skills_ctx = await _build_tools_skills_context(db, current_user.tenant_id)
    system_prompt = AGENT_SCRIPT_SYSTEM_PROMPT + tools_skills_ctx

    chat_messages = [
        LLMMessage(role="system", content=system_prompt),
    ]
    for m in history:
        chat_messages.append(LLMMessage(role=m.role, content=m.content))

    provider = llm_model.provider
    api_key = llm_model.api_key_encrypted
    model_name = llm_model.model
    base_url = llm_model.base_url
    timeout = float(llm_model.request_timeout or 120)
    max_tokens = llm_model.max_output_tokens or get_max_tokens(provider, model_name)
    temperature = llm_model.temperature

    queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def _run_stream():
        full_response = ""
        client = create_llm_client(provider, api_key, model_name, base_url, timeout)
        try:
            async def on_chunk(text: str):
                nonlocal full_response
                full_response += text
                await queue.put(json.dumps({"content": text}))

            await client.stream(
                messages=chat_messages,
                temperature=temperature,
                max_tokens=max_tokens,
                on_chunk=on_chunk,
            )
        except Exception as e:
            logger.error(f"[ScriptBuilder] Stream error: {e}")
            await queue.put(json.dumps({"error": str(e)}))
        finally:
            await client.close()

        if full_response:
            try:
                async with async_session() as save_db:
                    assistant_msg = ScriptMessage(
                        conversation_id=conv_id, role="assistant", content=full_response
                    )
                    save_db.add(assistant_msg)
                    await save_db.commit()
            except Exception as e:
                logger.error(f"[ScriptBuilder] Save error: {e}")

        await queue.put(None)

    async def generate():
        task = asyncio.create_task(_run_stream())
        try:
            while True:
                data = await queue.get()
                if data is None:
                    yield f"data: {json.dumps({'done': True})}\n\n"
                    break
                yield f"data: {data}\n\n"
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.post("/analyze")
async def analyze_script(
    body: ScriptAnalyzeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    llm_model = await _get_llm_model(db, current_user)

    client = create_llm_client(
        provider=llm_model.provider,
        api_key=llm_model.api_key_encrypted,
        model=llm_model.model,
        base_url=llm_model.base_url,
        timeout=float(llm_model.request_timeout or 120),
    )
    try:
        messages = [
            LLMMessage(role="system", content=ANALYZE_SYSTEM_PROMPT),
            LLMMessage(role="user", content=f"Please analyze this Agent Script:\n\n```ascript\n{body.script}\n```"),
        ]
        max_tokens = llm_model.max_output_tokens or get_max_tokens(llm_model.provider, llm_model.model)
        response = await client.complete(
            messages=messages,
            max_tokens=max_tokens,
        )
        raw = response.content or "{}"
        json_match = re.search(r"\{[\s\S]*\}", raw)
        parsed = json.loads(json_match.group(0) if json_match else raw)
        return parsed
    except Exception as e:
        logger.error(f"[ScriptBuilder] Analyze error: {e}")
        raise HTTPException(status_code=500, detail="Failed to analyze script")
    finally:
        await client.close()


def _parse_script_metadata(script: str) -> dict:
    """Extract agent name and description from Agent Script header."""
    name = "Evolver Agent"
    desc = ""
    name_found = False
    desc_found = False
    for line in script.splitlines():
        stripped = line.strip()
        low = stripped.lower()
        if not name_found:
            for prefix in ("agent_name:", "agent:"):
                if low.startswith(prefix):
                    val = stripped.split(":", 1)[1].strip().strip('"').strip("'")
                    if val:
                        name = val
                        name_found = True
                    break
        if not desc_found and low.startswith("description:"):
            val = stripped.split(":", 1)[1].strip().strip('"').strip("'")
            if val:
                desc = val
                desc_found = True
        if name_found and desc_found:
            break
    return {"name": name[:100], "description": desc[:500]}


def _extract_referenced_targets(script: str) -> tuple[set[str], set[str]]:
    """Parse tool:// and skill:// targets referenced in the script."""
    tool_names: set[str] = set()
    skill_names: set[str] = set()
    for m in re.finditer(r'target:\s*["\']?tool://([^"\'}\s]+)', script):
        tool_names.add(m.group(1).strip())
    for m in re.finditer(r'target:\s*["\']?skill://([^"\'}\s]+)', script):
        skill_names.add(m.group(1).strip())
    return tool_names, skill_names


class ApplyAsAgentRequest(BaseModel):
    script: str = Field(min_length=10)
    name: str | None = None


@router.post("/apply-as-agent")
async def apply_as_agent(
    body: ApplyAsAgentRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    meta = _parse_script_metadata(body.script)
    raw_name = body.name or meta["name"]
    parts = re.split(r'[\s_\-]+', raw_name) if raw_name else []
    agent_name = "".join(p[0].upper() + p[1:] for p in parts if p) if parts else "EvolverAgent"
    agent_desc = meta["description"]

    llm_model = await _get_llm_model(db, current_user)

    from app.models.tenant import Tenant
    target_tenant_id = current_user.tenant_id
    max_llm_calls = 100
    default_heartbeat_interval = 240
    if target_tenant_id:
        t_res = await db.execute(select(Tenant).where(Tenant.id == target_tenant_id))
        tenant = t_res.scalar_one_or_none()
        if tenant:
            max_llm_calls = tenant.default_max_llm_calls_per_day or 100
            if tenant.min_heartbeat_interval_minutes and tenant.min_heartbeat_interval_minutes > default_heartbeat_interval:
                default_heartbeat_interval = tenant.min_heartbeat_interval_minutes

    expires_at = datetime.now(timezone.utc) + timedelta(hours=current_user.quota_agent_ttl_hours or 48)

    agent = Agent(
        name=agent_name,
        role_description=agent_desc,
        creator_id=current_user.id,
        tenant_id=target_tenant_id,
        agent_type="evolver",
        primary_model_id=llm_model.id,
        status="idle",
        expires_at=expires_at,
        max_llm_calls_per_day=max_llm_calls,
        heartbeat_interval_minutes=default_heartbeat_interval,
    )
    db.add(agent)
    await db.flush()

    db.add(Participant(
        type="agent", ref_id=agent.id,
        display_name=agent.name, avatar_url=agent.avatar_url,
    ))

    db.add(AgentPermission(
        agent_id=agent.id, scope_type="company", access_level="use"
    ))
    await db.flush()

    db.add(AgentScriptVersion(
        agent_id=agent.id,
        version=1,
        folder="initial",
        content=body.script,
        source="script_builder",
    ))

    tool_names, skill_names = _extract_referenced_targets(body.script)

    installed_tools = []
    if tool_names:
        t_result = await db.execute(
            select(Tool).where(
                Tool.enabled == True,
                Tool.name.in_(tool_names),
                (Tool.tenant_id == target_tenant_id) | (Tool.tenant_id.is_(None)),
            )
        )
        for tool in t_result.scalars().all():
            db.add(AgentTool(agent_id=agent.id, tool_id=tool.id, enabled=True, source="system"))
            installed_tools.append(tool.name)

    from app.services.agent_manager import agent_manager
    from app.services.storage.factory import get_storage
    meta = _parse_script_metadata(body.script)
    await agent_manager.initialize_agent_files(
        db, agent,
        personality=meta["description"] or agent_desc,
        boundaries="",
    )

    storage = get_storage()
    soul_key = f"{agent.id}/soul.md"
    await storage.write(soul_key, body.script)

    installed_skills = []
    if skill_names:
        s_result = await db.execute(
            select(Skill).where(
                Skill.folder_name.in_(skill_names),
                (Skill.tenant_id == target_tenant_id) | (Skill.tenant_id.is_(None)),
            ).options(selectinload(Skill.files))
        )
        for skill in s_result.scalars().all():
            installed_skills.append(skill.folder_name)
            try:
                for sf in skill.files:
                    if ".." in sf.path.split("/"):
                        continue
                    key = f"{agent.id}/skills/{skill.folder_name}/{sf.path}"
                    await storage.write(key, sf.content)
            except Exception as e:
                logger.warning(f"[ApplyAsAgent] Failed to copy skill {skill.folder_name}: {e}")

    default_result = await db.execute(
        select(Skill).where(
            Skill.is_default == True,
            (Skill.tenant_id == target_tenant_id) | (Skill.tenant_id.is_(None)),
        ).options(selectinload(Skill.files))
    )
    for skill in default_result.scalars().all():
        if skill.folder_name not in installed_skills:
            try:
                for sf in skill.files:
                    if ".." in sf.path.split("/"):
                        continue
                    key = f"{agent.id}/skills/{skill.folder_name}/{sf.path}"
                    await storage.write(key, sf.content)
            except Exception as e:
                logger.warning(f"[ApplyAsAgent] Failed to copy default skill {skill.folder_name}: {e}")

    try:
        await db.commit()
    except Exception:
        import shutil
        try:
            agent_dir = agent_manager._agent_dir(agent.id)
            if agent_dir.exists():
                shutil.rmtree(agent_dir)
        except Exception as cleanup_err:
            logger.warning(f"[ApplyAsAgent] Failed to cleanup agent files after DB error: {cleanup_err}")
        raise
    await db.refresh(agent)

    logger.info(f"[ApplyAsAgent] Created evolver agent {agent.id} from script builder")

    return {
        "agent_id": str(agent.id),
        "agent_name": agent.name,
        "installed_tools": installed_tools,
        "installed_skills": installed_skills,
    }
