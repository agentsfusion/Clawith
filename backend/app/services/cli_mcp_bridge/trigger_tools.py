async def clawith_trigger_set(agent, name: str, type: str, config: dict, reason: str, focus_ref: str | None = None) -> str:
    from app.services.agent_tools import _handle_set_trigger
    return await _handle_set_trigger(agent.id, {
        "name": name, "type": type, "config": config, "reason": reason, "focus_ref": focus_ref,
    })


async def clawith_trigger_update(agent, name: str, config: dict | None = None, reason: str | None = None) -> str:
    from app.services.agent_tools import _handle_update_trigger
    return await _handle_update_trigger(agent.id, {
        "name": name, "config": config or {}, "reason": reason or "",
    })


async def clawith_trigger_cancel(agent, name: str) -> str:
    from app.services.agent_tools import _handle_cancel_trigger
    return await _handle_cancel_trigger(agent.id, {"name": name})


async def clawith_trigger_list(agent) -> str:
    from app.services.agent_tools import _handle_list_triggers
    return await _handle_list_triggers(agent.id)
