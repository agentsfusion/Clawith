async def clawith_channel_send(agent, member_name: str, message: str, channel: str | None = None) -> str:
    from app.services.agent_tools import _send_channel_message
    return await _send_channel_message(agent.id, {
        "member_name": member_name, "message": message, "channel": channel or "",
    })


async def clawith_channel_send_file(agent, file_path: str, member_name: str, message: str = "") -> str:
    from pathlib import Path
    from app.services.agent_tools import _send_channel_file
    ws = Path("/tmp")  # placeholder; actual workspace resolved inside
    return await _send_channel_file(agent.id, ws, {
        "file_path": file_path, "member_name": member_name, "message": message,
    })


async def clawith_web_send(agent, username: str, message: str) -> str:
    from app.services.agent_tools import _send_channel_message
    return await _send_channel_message(agent.id, {
        "member_name": username, "message": message, "channel": "web",
    })


async def clawith_feishu_send(agent, member_name: str, user_id: str | None = None, message: str = "") -> str:
    from app.services.agent_tools import _send_channel_message
    args: dict = {"member_name": member_name, "message": message, "channel": "feishu"}
    if user_id:
        args["user_id"] = user_id
    return await _send_channel_message(agent.id, args)
