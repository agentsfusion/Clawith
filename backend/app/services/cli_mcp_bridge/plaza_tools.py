async def clawith_plaza_list(agent) -> str:
    from app.services.agent_tools import _plaza_get_new_posts
    return await _plaza_get_new_posts(agent.id, {})


async def clawith_plaza_post(agent, content: str, tags: list[str] | None = None) -> str:
    from app.services.agent_tools import _plaza_create_post
    return await _plaza_create_post(agent.id, {
        "content": content, "tags": tags or [],
    })


async def clawith_plaza_comment(agent, post_id: str, content: str) -> str:
    from app.services.agent_tools import _plaza_add_comment
    return await _plaza_add_comment(agent.id, {
        "post_id": post_id, "content": content,
    })
