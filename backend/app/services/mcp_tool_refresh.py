"""Background auto-refresh for imported MCP servers.

Smithery-hosted MCP servers (Gmail, GitHub, etc.) silently gain, rename, or
remove tools over time. On-demand refresh keeps a single server current, but
nobody remembers to run it. This daemon periodically re-runs the existing
`import_mcp_from_smithery(..., refresh=True)` reconcile for every imported
server so their tool lists stay accurate automatically.

It deliberately reuses the refresh path's non-destructive fail-safe: the
reconcile aborts without changes when it cannot fetch the live `tools/list`,
so a transient network/auth glitch never disables otherwise-valid tools.

Only servers that already have a stored Smithery connection
(`smithery_namespace` + `smithery_connection_id`) are processed — that lets the
reconcile reuse the existing connection instead of minting a new one or
triggering a fresh OAuth flow. Directly-imported servers (no Smithery
connection) are skipped.
"""

import asyncio
import uuid

from loguru import logger
from sqlalchemy import select

from app.config import get_settings
from app.database import async_session
from app.models.tool import Tool, AgentTool


async def _collect_refresh_targets() -> dict[tuple[uuid.UUID, str], str]:
    """Find one (agent_id, server_name) -> server_id entry per imported server.

    Returns a mapping keyed by (agent_id, mcp_server_name). The value is the
    Smithery server_id to pass to the reconcile. We prefer the persisted
    `smithery_server_id`; if a legacy install never stored it we fall back to
    the server's display name as a best-effort search query (the reconcile's
    live tools/list, driven by the stored connection, remains authoritative).
    """
    targets: dict[tuple[uuid.UUID, str], str] = {}
    async with async_session() as db:
        rows = await db.execute(
            select(AgentTool, Tool)
            .join(Tool, AgentTool.tool_id == Tool.id)
            .where(Tool.type == "mcp")
        )
        for at, tool in rows.all():
            server_name = tool.mcp_server_name
            if not server_name:
                continue
            cfg = at.config or {}
            # Only servers with a stored Smithery connection can refresh
            # without creating a new connection / OAuth flow.
            if not (cfg.get("smithery_namespace") and cfg.get("smithery_connection_id")):
                continue
            key = (at.agent_id, server_name)
            if key in targets:
                continue
            targets[key] = cfg.get("smithery_server_id") or server_name
    return targets


def _classify(message: str) -> str:
    """Classify a reconcile result message for the summary log."""
    if "Refresh aborted" in message:
        return "aborted"
    if any(marker in message for marker in ("Added ", "Updated ", "Disabled ")):
        return "changed"
    return "unchanged"


async def refresh_all_mcp_servers() -> dict:
    """Re-run the refresh reconcile for every imported Smithery MCP server.

    Returns a summary dict with counts and per-server outcomes. Each server is
    refreshed independently; one failure never aborts the rest.
    """
    from app.services.resource_discovery import import_mcp_from_smithery

    targets = await _collect_refresh_targets()
    summary = {
        "servers": len(targets),
        "changed": 0,
        "unchanged": 0,
        "aborted": 0,
        "errors": 0,
        "details": [],
    }

    if not targets:
        logger.info("[MCPRefresh] No imported Smithery MCP servers to refresh")
        return summary

    logger.info(f"[MCPRefresh] Refreshing {len(targets)} imported MCP server(s)...")

    for (agent_id, server_name), server_id in targets.items():
        try:
            message = await import_mcp_from_smithery(server_id, agent_id, refresh=True)
        except Exception as e:
            summary["errors"] += 1
            summary["details"].append({
                "agent_id": str(agent_id),
                "server": server_name,
                "outcome": "error",
                "error": str(e)[:200],
            })
            logger.warning(
                f"[MCPRefresh] Error refreshing '{server_name}' for agent {agent_id}: {e}"
            )
            continue

        outcome = _classify(message)
        summary[outcome] += 1
        summary["details"].append({
            "agent_id": str(agent_id),
            "server": server_name,
            "outcome": outcome,
        })
        if outcome == "changed":
            # The reconcile message already enumerates exactly what changed.
            logger.info(
                f"[MCPRefresh] '{server_name}' (agent {agent_id}) updated:\n{message}"
            )
        elif outcome == "aborted":
            logger.warning(
                f"[MCPRefresh] '{server_name}' (agent {agent_id}) refresh aborted "
                f"(live tools/list unavailable — no changes made)"
            )

    logger.info(
        f"[MCPRefresh] Done — {summary['servers']} server(s): "
        f"{summary['changed']} changed, {summary['unchanged']} unchanged, "
        f"{summary['aborted']} aborted, {summary['errors']} errors"
    )
    return summary


async def start_mcp_refresh_daemon() -> None:
    """Background loop that periodically refreshes imported MCP servers.

    Registered as a startup background task. Honors MCP_AUTO_REFRESH_* settings.
    """
    settings = get_settings()
    if not settings.MCP_AUTO_REFRESH_ENABLED:
        logger.info("[MCPRefresh] Auto-refresh disabled (MCP_AUTO_REFRESH_ENABLED=false)")
        return

    interval = max(1, settings.MCP_AUTO_REFRESH_INTERVAL_HOURS) * 3600
    startup_delay = max(0, settings.MCP_AUTO_REFRESH_STARTUP_DELAY_SECONDS)

    logger.info(
        f"🔄 MCP auto-refresh daemon started "
        f"(every {settings.MCP_AUTO_REFRESH_INTERVAL_HOURS}h, "
        f"first run in {startup_delay}s)"
    )
    await asyncio.sleep(startup_delay)

    while True:
        try:
            await refresh_all_mcp_servers()
        except Exception as e:
            logger.error(f"[MCPRefresh] daemon tick error: {e}")
            import traceback
            traceback.print_exc()
        await asyncio.sleep(interval)
