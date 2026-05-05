async def clawith_a2a_send(agent, agent_name: str, message: str, msg_type: str = "notify") -> str:
    from app.services.agent_tools import _send_message_to_agent
    return await _send_message_to_agent(
        from_agent_id=agent.id,
        arguments={"agent_name": agent_name, "message": message, "msg_type": msg_type},
    )


async def clawith_a2a_send_file(agent, agent_name: str, file_path: str, message: str = "") -> str:
    from app.services.agent_tools import _send_message_to_agent
    return await _send_message_to_agent(
        from_agent_id=agent.id,
        arguments={"agent_name": agent_name, "message": message, "file_path": file_path, "msg_type": "file"},
    )
