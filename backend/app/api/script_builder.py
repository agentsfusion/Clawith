"""Script Builder API — standalone Agent Script generation with SSE streaming."""

import asyncio
import json
import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db, async_session
from app.models.user import User
from app.models.llm import LLMModel
from app.models.script_builder import ScriptConversation, ScriptMessage
from app.api.auth import get_current_user
from app.services.agent_script_prompt import AGENT_SCRIPT_SYSTEM_PROMPT, ANALYZE_SYSTEM_PROMPT
from app.services.llm_client import create_llm_client, LLMMessage, get_max_tokens

router = APIRouter(prefix="/script-builder", tags=["script-builder"])


class CreateConversationBody(BaseModel):
    title: str = "New Session"


class SendMessageBody(BaseModel):
    content: str


class AnalyzeBody(BaseModel):
    script: str


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


@router.get("/conversations")
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
        {"id": c.id, "title": c.title, "createdAt": c.created_at.isoformat()}
        for c in convs
    ]


@router.post("/conversations", status_code=201)
async def create_conversation(
    body: CreateConversationBody,
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
    return {"id": conv.id, "title": conv.title, "createdAt": conv.created_at.isoformat()}


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


@router.get("/conversations/{conv_id}/messages")
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
        {"id": m.id, "role": m.role, "content": m.content, "createdAt": m.created_at.isoformat()}
        for m in msgs
    ]


@router.post("/conversations/{conv_id}/messages")
async def send_message(
    conv_id: int,
    body: SendMessageBody,
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

    chat_messages = [
        LLMMessage(role="system", content=AGENT_SCRIPT_SYSTEM_PROMPT),
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
    body: AnalyzeBody,
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
