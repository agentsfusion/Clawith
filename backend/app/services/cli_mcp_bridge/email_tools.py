async def clawith_email_send(agent, to: str, subject: str, body: str) -> str:
    from app.services.agent_tools import _handle_email_tool
    from pathlib import Path
    ws = Path("/tmp")
    return await _handle_email_tool("send_email", agent.id, ws, {
        "to": to, "subject": subject, "body": body,
    })


async def clawith_email_read(agent, folder: str = "INBOX", max_results: int = 10) -> str:
    from app.services.agent_tools import _handle_email_tool
    from pathlib import Path
    ws = Path("/tmp")
    return await _handle_email_tool("read_emails", agent.id, ws, {
        "folder": folder, "max_results": max_results,
    })


async def clawith_email_reply(agent, message_id: str, body: str) -> str:
    from app.services.agent_tools import _handle_email_tool
    from pathlib import Path
    ws = Path("/tmp")
    return await _handle_email_tool("reply_email", agent.id, ws, {
        "message_id": message_id, "body": body,
    })
