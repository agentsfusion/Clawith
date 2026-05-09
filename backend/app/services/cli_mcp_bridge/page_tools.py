async def clawith_page_publish(agent, title: str, content: str, path: str | None = None) -> str:
    from app.services.agent_tools import _publish_page
    from pathlib import Path
    ws = Path("/tmp")
    return await _publish_page(agent.id, agent.creator_id, ws, {
        "title": title, "content": content, "path": path or "",
    })


async def clawith_page_list(agent) -> str:
    from app.services.agent_tools import _list_published_pages
    return await _list_published_pages(agent.id)
