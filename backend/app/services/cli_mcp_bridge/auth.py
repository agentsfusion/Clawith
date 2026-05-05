import hashlib

from fastapi import HTTPException, Request
from sqlalchemy import select

from app.database import async_session


async def verify_cli_agent(request: Request):
    agent_id = request.headers.get("X-Agent-Id")
    api_key = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not agent_id or not api_key:
        raise HTTPException(401, "Missing agent credentials")

    from app.models.agent import Agent
    async with async_session() as db:
        agent = await db.get(Agent, agent_id)
        if not agent or agent.agent_type != "cli":
            raise HTTPException(403, "Not a CLI agent")
        key_hash = hashlib.sha256(api_key.encode()).hexdigest()
        if key_hash != agent.api_key_hash:
            raise HTTPException(403, "Invalid API key")
        return agent


_TOOL_AUTONOMY_MAP = {
    "clawith_a2a_send": "send_feishu_message",
    "clawith_channel_send": "send_feishu_message",
    "clawith_trigger_set": "write_workspace_files",
    "clawith_plaza_post": "write_workspace_files",
    "clawith_email_send": "send_external_message",
}


async def check_autonomy(agent, bridge_tool: str) -> bool:
    action = _TOOL_AUTONOMY_MAP.get(bridge_tool)
    if not action:
        return True
    level = (agent.autonomy_policy or {}).get(action, "L2")
    return level != "L3"
