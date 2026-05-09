async def clawith_discover(agent, query: str, max_results: int = 5) -> str:
    from app.services.agent_tools import _discover_resources
    return await _discover_resources({
        "query": query, "max_results": max_results,
    })


async def clawith_mcp_import(agent, server_url: str, tool_name: str | None = None, config: dict | None = None) -> str:
    from app.services.agent_tools import _discover_resources
    return await _discover_resources({
        "action": "import", "server_url": server_url,
        "tool_name": tool_name or "", "config": config or {},
    })


async def clawith_hub_search(agent, query: str) -> str:
    from app.services.agent_tools import _discover_resources
    return await _discover_resources({
        "action": "hub_search", "query": query,
    })


async def clawith_skill_install(agent, skill_id: str) -> str:
    from app.services.agent_tools import _discover_resources
    return await _discover_resources({
        "action": "skill_install", "skill_id": skill_id,
    })
