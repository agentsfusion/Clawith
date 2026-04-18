"""Evolver runtime — orchestrates structured Agent Script execution.

For agents with `agent_type='evolver'`, this layer drives the full execution flow:
    parse Agent Script
        ↓
    enter start_agent topic
        ↓
    sequentially process topic instructions:
        - logic instructions (-)         → execute immediately
        - reasoning instructions (|)     → collect for LLM
        - action calls (run @actions.x)  → collect for LLM as tool calls
        - conditionals (if/elif/else)    → evaluate & branch
        - variable ops (let/set)         → mutate state
        ↓
    build system prompt with state + collected instructions/actions
        ↓
    LLM responds → parse [SET]/[TRANSITION]/[MEM] directives → persist state

State is persisted per (agent_id, session_id) so concurrent sessions don't collide.
Parsed scripts are cached by content hash to avoid re-parsing every turn.
"""

import hashlib
import logging
import uuid
from dataclasses import dataclass

from app.services.script_runtime import (
    ParsedScript,
    ScriptExecutionResult,
    ScriptState,
    build_execution_prompt,
    execute_script_logic,
    get_script_for_agent,
    init_state,
    load_state,
    parse_script,
    process_response_v2,
    save_state,
)

logger = logging.getLogger(__name__)


# ── Module-level caches ──────────────────────────────────────────────────
# Parsed scripts are deterministic from content; cache by sha1 of script text.
_PARSE_CACHE: dict[str, ParsedScript] = {}
_PARSE_CACHE_MAX = 64


def _cached_parse(script_text: str) -> ParsedScript:
    digest = hashlib.sha1(script_text.encode("utf-8", errors="replace")).hexdigest()
    cached = _PARSE_CACHE.get(digest)
    if cached is not None:
        return cached
    parsed = parse_script(script_text)
    if len(_PARSE_CACHE) >= _PARSE_CACHE_MAX:
        # Evict an arbitrary entry (FIFO-ish via dict insertion order)
        _PARSE_CACHE.pop(next(iter(_PARSE_CACHE)))
    _PARSE_CACHE[digest] = parsed
    return parsed


@dataclass
class EvolverTurnContext:
    """Held across a single LLM call so finalize can persist state."""
    agent_id: uuid.UUID
    session_id: str
    parsed: ParsedScript
    state: ScriptState
    exec_result: ScriptExecutionResult
    system_prompt: str


async def _is_evolver_agent(agent_id) -> bool:
    if not agent_id:
        return False
    try:
        from sqlalchemy import select

        from app.database import async_session
        from app.models.agent import Agent as AgentModel

        async with async_session() as db:
            row = await db.execute(select(AgentModel.agent_type).where(AgentModel.id == agent_id))
            agent_type = row.scalar_one_or_none()
            return agent_type == "evolver"
    except Exception as e:
        logger.warning(f"[EvolverRuntime] Failed to check agent_type for {agent_id}: {e}")
        return False


async def prepare_evolver_turn(
    agent_id,
    session_id: str = "",
    user_id=None,
) -> EvolverTurnContext | None:
    """Build an evolver execution context for one LLM turn.

    Returns None if the agent is not an evolver, has no Agent Script, or the
    script fails to parse — in which case the caller should fall back to the
    legacy `build_agent_context` path.
    """
    if not agent_id:
        return None
    if not await _is_evolver_agent(agent_id):
        return None

    script_text = await get_script_for_agent(agent_id)
    if not script_text or not script_text.strip():
        logger.debug(f"[EvolverRuntime] Agent {agent_id} has no script — fallback to legacy")
        return None

    try:
        parsed = _cached_parse(script_text)
    except Exception as e:
        logger.warning(f"[EvolverRuntime] Parse failed for agent {agent_id}: {e}")
        return None

    if not parsed.start_agent and not parsed.topics:
        logger.debug(f"[EvolverRuntime] Agent {agent_id} script has no topics — fallback to legacy")
        return None

    # Load or initialise per-session state
    aid = agent_id if isinstance(agent_id, uuid.UUID) else uuid.UUID(str(agent_id))
    state = await load_state(aid, session_id)
    if state is None:
        state = init_state(parsed)
    else:
        # If the script was edited (new variables, removed variables), reconcile
        for vname, var in parsed.variables.items():
            state.variables.setdefault(vname, var.default)
        # If state references a topic that no longer exists, reset to start
        if state.current_topic not in (
            "__start__", *parsed.topics.keys(), ""
        ):
            logger.info(
                f"[EvolverRuntime] State topic '{state.current_topic}' no longer exists — resetting"
            )
            state.current_topic = "__start__" if parsed.start_agent else (
                next(iter(parsed.topics)) if parsed.topics else ""
            )

    # Run procedural script logic (handles if/let/set/transition/run/|)
    try:
        _uid = user_id if isinstance(user_id, uuid.UUID) else (
            uuid.UUID(str(user_id)) if user_id else None
        )
        exec_result = await execute_script_logic(
            parsed, state,
            agent_id=aid, user_id=_uid, session_id=session_id or "",
        )
    except Exception as e:
        logger.exception(f"[EvolverRuntime] execute_script_logic failed for agent {agent_id}: {e}")
        return None

    try:
        system_prompt = build_execution_prompt(parsed, state, exec_result)
    except Exception as e:
        logger.exception(f"[EvolverRuntime] build_execution_prompt failed for agent {agent_id}: {e}")
        return None

    logger.info(
        f"[EvolverRuntime] Prepared turn for {agent_id} session={session_id or '-'} "
        f"topic={state.current_topic} steps={len(exec_result.steps)} "
        f"prompts={len(exec_result.llm_instructions)} actions={len(exec_result.actions_to_run)}"
    )

    return EvolverTurnContext(
        agent_id=aid,
        session_id=session_id or "",
        parsed=parsed,
        state=state,
        exec_result=exec_result,
        system_prompt=system_prompt,
    )


async def finalize_evolver_turn(
    ctx: EvolverTurnContext,
    response_text: str | None,
) -> str:
    """Process the LLM response, extract directives, persist state.

    Always persists state, even when the LLM returned no usable text — because
    `execute_script_logic` may have mutated `state.variables` / `state.current_topic`
    in `prepare_evolver_turn`, and those changes must survive across turns.

    Returns the cleaned response text with `[SET]/[TRANSITION]/[MEM]` stripped,
    or the original `response_text` (possibly empty) if no directives were found.
    """
    cleaned = response_text or ""
    if response_text:
        try:
            cleaned, changes = process_response_v2(response_text, ctx.state, ctx.parsed)
            if changes:
                logger.info(
                    f"[EvolverRuntime] Agent {ctx.agent_id} session={ctx.session_id or '-'} "
                    f"applied {len(changes)} state change(s): {'; '.join(changes[:5])}"
                )
        except Exception as e:
            logger.exception(f"[EvolverRuntime] process_response_v2 failed for agent {ctx.agent_id}: {e}")
            cleaned = response_text

    try:
        await save_state(ctx.agent_id, ctx.state, ctx.session_id)
    except Exception as e:
        logger.warning(f"[EvolverRuntime] save_state failed for agent {ctx.agent_id}: {e}")

    return cleaned


def collect_missing_tool_events(ctx: EvolverTurnContext) -> list[dict]:
    """Extract structured missing-tool events from the prepared turn.

    Returns one dict per action whose `tool://X` target was unresolved
    (tool not enabled for the agent, or unknown). Callers can forward
    these to the chat UI as "missing tool" chips so users notice the
    misconfiguration without relying on the LLM to verbalize it.
    """
    events: list[dict] = []
    if ctx is None or ctx.exec_result is None:
        return events
    for act in ctx.exec_result.actions_to_run or []:
        if act.get("missing_tool"):
            events.append({
                "tool_name": act.get("tool_name") or "",
                "action": act.get("name") or "",
                "agent_id": str(ctx.agent_id),
            })
    return events


def collect_skill_failure_events(ctx: EvolverTurnContext) -> list[dict]:
    """Extract structured skill-execution failure events from the prepared turn.

    Sibling of `collect_missing_tool_events`. Returns one dict per action
    whose `skill://X` target failed to execute, distinguishing:
      - kind='timeout' — no openclaw worker claimed the job in time;
      - kind='missing' — the SKILL.md file was not present in the
        agent workspace;
      - kind='failed'  — a worker ran the skill but reported failure.
    Callers can forward these to the chat UI so users see deterministic
    feedback without relying on LLM compliance with prompt instructions.
    """
    events: list[dict] = []
    if ctx is None or ctx.exec_result is None:
        return events
    for act in ctx.exec_result.actions_to_run or []:
        if act.get("skill_timeout"):
            kind = "timeout"
        elif act.get("missing_skill"):
            kind = "missing"
        elif act.get("skill_failed"):
            kind = "failed"
        else:
            continue
        events.append({
            "skill_name": act.get("skill_name") or "",
            "action": act.get("name") or "",
            "agent_id": str(ctx.agent_id),
            "kind": kind,
            "error": act.get("error") or "",
        })
    return events


def invalidate_parse_cache():
    """Clear the parsed-script cache (call after evolution writes a new script)."""
    _PARSE_CACHE.clear()
