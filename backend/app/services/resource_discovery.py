"""Resource discovery — search Smithery & ModelScope registries and import MCP servers."""

import asyncio
import uuid
import httpx
from loguru import logger
from sqlalchemy import select
from app.database import async_session
from app.models.tool import Tool, AgentTool
from app.services.tool_config import decrypt_sensitive_fields, get_tenant_tool_config


# ── Smithery Registry Search ────────────────────────────────────

SMITHERY_API_BASE = "https://registry.smithery.ai"
MODELSCOPE_API_BASE = "https://modelscope.cn"


async def _get_smithery_api_key(agent_id: uuid.UUID | None = None) -> str:
    """Read Smithery API key.

    Priority: 1) per-agent AgentTool config, 2) system-level tool config.

    Sensitive fields in tool/AgentTool config are stored encrypted (see
    api.tools._encrypt_sensitive_fields). We must decrypt here before
    handing the value to httpx — otherwise Smithery rejects with 401.
    Falls back to raw value when decrypt fails (e.g. legacy plaintext keys).
    """
    def _maybe_decrypt(raw: str) -> str:
        if not raw:
            return ""
        return decrypt_sensitive_fields({"value": raw}, {"fields": [{"key": "value", "type": "password"}]}).get("value", raw)

    try:
        async with async_session() as db:
            agent_tenant_id = None
            if agent_id:
                from app.models.agent import Agent as AgentModel
                tenant_r = await db.execute(select(AgentModel.tenant_id).where(AgentModel.id == agent_id))
                agent_tenant_id = tenant_r.scalar_one_or_none()

            # 1) Per-agent: check AgentTool configs for any MCP tool with a smithery_api_key
            if agent_id:
                at_r = await db.execute(
                    select(AgentTool).where(AgentTool.agent_id == agent_id)
                )
                for at in at_r.scalars().all():
                    if at.config and at.config.get("smithery_api_key"):
                        return _maybe_decrypt(at.config["smithery_api_key"])
            # 2) Tenant/company fallback for builtin discovery tools
            for tool_name in ("discover_resources", "import_mcp_server"):
                r = await db.execute(select(Tool).where(Tool.name == tool_name))
                tool = r.scalar_one_or_none()
                if not tool:
                    continue
                tenant_config = await get_tenant_tool_config(db, agent_tenant_id, tool.name, tool.config_schema)
                if tenant_config.get("smithery_api_key"):
                    return tenant_config["smithery_api_key"]
                if tool.config and tool.config.get("smithery_api_key") and not agent_tenant_id:
                    return _maybe_decrypt(tool.config["smithery_api_key"])
    except Exception:
        pass
    return ""


async def _search_smithery_api(query: str, max_results: int, api_key: str) -> list[dict]:
    """Search Smithery registry, returns normalized results."""
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(
                f"{SMITHERY_API_BASE}/servers",
                params={"q": query, "pageSize": max_results},
                headers=headers,
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
        results = []
        for srv in data.get("servers", [])[:max_results]:
            results.append({
                "name": srv.get("qualifiedName", ""),
                "display_name": srv.get("displayName", ""),
                "description": srv.get("description", "")[:200],
                "remote": srv.get("remote", False),
                "verified": srv.get("verified", False),
                "use_count": srv.get("useCount", 0),
                "homepage": srv.get("homepage", ""),
                "source": "Smithery",
            })
        return results
    except Exception:
        return []


async def _get_modelscope_api_token(agent_id: uuid.UUID | None = None) -> str:
    """Read ModelScope API token from discover_resources tool config."""
    try:
        async with async_session() as db:
            agent_tenant_id = None
            if agent_id:
                from app.models.agent import Agent as AgentModel
                tenant_r = await db.execute(select(AgentModel.tenant_id).where(AgentModel.id == agent_id))
                agent_tenant_id = tenant_r.scalar_one_or_none()
            for tool_name in ("discover_resources", "import_mcp_server"):
                r = await db.execute(select(Tool).where(Tool.name == tool_name))
                tool = r.scalar_one_or_none()
                if not tool:
                    continue
                tenant_config = await get_tenant_tool_config(db, agent_tenant_id, tool.name, tool.config_schema)
                if tenant_config.get("modelscope_api_token"):
                    return tenant_config["modelscope_api_token"]
                if tool.config and tool.config.get("modelscope_api_token") and not agent_tenant_id:
                    return tool.config["modelscope_api_token"]
    except Exception:
        pass
    return ""


async def _search_modelscope_api(query: str, max_results: int, agent_id: uuid.UUID | None = None) -> list[dict]:
    """Search ModelScope MCP Hub via official OpenAPI (no WAF issues)."""
    api_token = await _get_modelscope_api_token(agent_id)
    if not api_token:
        return []  # Silently skip if no token configured

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_token}",
        "Cookie": f"m_session_id={api_token}",
        "User-Agent": "modelscope-mcp-server/1.0",
    }
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.put(
                f"{MODELSCOPE_API_BASE}/openapi/v1/mcp/servers",
                json={"page_size": max_results, "page_number": 1, "search": query, "filter": {}},
                headers=headers,
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            if not data.get("success"):
                return []

        servers_data = data.get("data", {}).get("mcp_server_list", [])
        if not servers_data:
            return []

        results = []
        for srv in servers_data[:max_results]:
            server_id = srv.get("id", "")
            results.append({
                "name": server_id,
                "display_name": srv.get("name", server_id),
                "description": srv.get("description", "")[:200],
                "remote": srv.get("is_hosted", False),
                "verified": True,
                "use_count": 0,
                "homepage": f"https://modelscope.cn/mcp/servers/{server_id}",
                "source": "ModelScope",
            })
        return results
    except Exception as e:
        logger.error(f"[ResourceDiscovery] ModelScope search failed: {e}")
        return []


async def search_registries(query: str, max_results: int = 5, agent_id: uuid.UUID | None = None) -> str:
    """Search both Smithery and ModelScope for MCP servers."""
    api_key = await _get_smithery_api_key(agent_id)

    # Search both registries in parallel
    import asyncio
    smithery_task = _search_smithery_api(query, max_results, api_key)
    modelscope_task = _search_modelscope_api(query, max_results, agent_id)
    smithery_results, modelscope_results = await asyncio.gather(smithery_task, modelscope_task)

    # Merge: Smithery first, then ModelScope (deduplicate by name)
    seen_names = set()
    all_results = []
    for r in smithery_results + modelscope_results:
        if r["name"] not in seen_names:
            seen_names.add(r["name"])
            all_results.append(r)

    if not all_results:
        return f'🔍 No MCP servers found for "{query}" on Smithery or ModelScope. Try different keywords.'

    results = []
    for i, srv in enumerate(all_results[:max_results], 1):
        verified = " ✅" if srv["verified"] else ""
        source_tag = f"[{srv['source']}]"
        if srv["remote"]:
            deploy_info = "🌐 Remote (no local install needed)"
        else:
            deploy_info = "💻 Local install required"
        use_info = f" · 👥 {srv['use_count']:,} users" if srv["use_count"] else ""
        hp = srv['homepage']

        results.append(
            f"**{i}. {srv['display_name']}**{verified} {source_tag}\n"
            f"   ID: `{srv['name']}`\n"
            f"   {srv['description']}\n"
            f"   {deploy_info}{use_info}\n"
            f"   {'🔗 ' + hp if hp else ''}"
        )

    header = f'🔍 Found {len(results)} MCP server(s) for "{query}":\n\n'
    footer = (
        "\n\n---\n"
        "💡 To import a remote server, use `import_mcp_server` with the server ID.\n"
        '   Example: import_mcp_server(server_id="gmail")'
    )
    return header + "\n\n".join(results) + footer


# Keep backward-compatible alias
async def search_smithery(query: str, max_results: int = 5, agent_id: uuid.UUID | None = None) -> str:
    return await search_registries(query, max_results, agent_id=agent_id)


# ── Import MCP Server ───────────────────────────────────────────

async def _ensure_smithery_connection(api_key: str, mcp_url: str, display_name: str) -> dict:
    """Create or reuse a Smithery Connect namespace + connection.

    Returns dict with keys: namespace, connection_id, auth_url (if OAuth needed).
    """
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            # Get or create namespace
            ns_resp = await client.get("https://api.smithery.ai/namespaces", headers=headers)
            namespaces = ns_resp.json().get("namespaces", []) if ns_resp.status_code == 200 else []
            if namespaces:
                namespace = namespaces[0]["name"]
            else:
                create_ns = await client.post(
                    "https://api.smithery.ai/namespaces",
                    json={"name": "clawith"},
                    headers=headers,
                )
                if create_ns.status_code not in (200, 201):
                    return {"error": f"Failed to create namespace: HTTP {create_ns.status_code}"}
                namespace = create_ns.json()["name"]

            # Create connection
            conn_id = display_name.lower().replace(" ", "-").replace(":", "")
            conn_resp = await client.post(
                f"https://api.smithery.ai/connect/{namespace}",
                json={"connectionId": conn_id, "mcpUrl": mcp_url, "name": display_name},
                headers=headers,
            )
            if conn_resp.status_code not in (200, 201):
                return {"error": f"Failed to create connection: HTTP {conn_resp.status_code} — {conn_resp.text[:200]}"}

            conn_data = conn_resp.json()
            result = {
                "namespace": namespace,
                "connection_id": conn_data.get("connectionId", conn_id),
            }
            status = conn_data.get("status", {})
            if isinstance(status, dict) and status.get("state") == "auth_required":
                result["auth_url"] = status.get("authorizationUrl", "")
            return result
    except Exception as e:
        return {"error": str(e)[:200]}


async def import_mcp_from_smithery(
    server_id: str,
    agent_id: uuid.UUID,
    config: dict | None = None,
    reauthorize: bool = False,
    refresh: bool = False,
) -> str:
    """Import an MCP server from Smithery into the platform.

    Uses the Smithery Registry detail API to get tool definitions,
    and stores the deploymentUrl for runtime execution via Smithery Connect.
    If config contains 'smithery_api_key', it's stored per-agent for future use.
    """
    config = dict(config) if config else {}  # mutable copy

    # Extract smithery_api_key from config (user-provided) or fallback to stored
    api_key = config.pop("smithery_api_key", None) or await _get_smithery_api_key(agent_id)
    if not api_key:
        return (
            "❌ Smithery API key is required to import MCP servers.\n\n"
            "请提供你的 Smithery API Key，你可以通过以下步骤获取：\n"
            "1. 注册/登录 https://smithery.ai\n"
            "2. 前往 https://smithery.ai/account/api-keys 创建 API Key\n"
            "3. 将 Key 提供给我，例如：\n"
            '   `import_mcp_server(server_id="github", config={"smithery_api_key": "your-key"})`'
        )

    # Write key back to discover_resources / import_mcp_server AgentTool configs
    # so it shows up in the Config dialog
    try:
        async with async_session() as db:
            for tool_name in ("discover_resources", "import_mcp_server"):
                r = await db.execute(select(Tool).where(Tool.name == tool_name))
                tool = r.scalar_one_or_none()
                if not tool:
                    continue
                at_r = await db.execute(
                    select(AgentTool).where(
                        AgentTool.agent_id == agent_id,
                        AgentTool.tool_id == tool.id,
                    )
                )
                at = at_r.scalar_one_or_none()
                if at:
                    at.config = {**(at.config or {}), "smithery_api_key": api_key}
                else:
                    db.add(AgentTool(
                        agent_id=agent_id, tool_id=tool.id, enabled=True,
                        source="system", config={"smithery_api_key": api_key},
                    ))
            await db.commit()
    except Exception:
        pass  # non-critical — key is still usable from MCP tool configs

    # ---- Early exit: check if this server's tools are already installed for this agent ----
    # Check by both tool name prefix AND mcp_server_name to catch different server_id variants
    # (e.g., "github" vs "@anthropic/github" both produce server_name "GitHub")
    clean_id_check = server_id.replace("/", "_").replace("@", "")
    try:
        async with async_session() as db:
            from sqlalchemy import or_
            existing_server_r = await db.execute(
                select(Tool).where(
                    Tool.type == "mcp",
                    or_(
                        Tool.name.like(f"mcp_{clean_id_check}%"),
                        Tool.name.like(f"mcp_{clean_id_check.split('_')[-1]}%"),
                    ),
                )
            )
            existing_server_tools = existing_server_r.scalars().all()
            if existing_server_tools and not config and not reauthorize and not refresh:
                # Check if this agent has assignments for these tools
                tool_ids = [t.id for t in existing_server_tools]
                agent_assignments_r = await db.execute(
                    select(AgentTool).where(
                        AgentTool.agent_id == agent_id,
                        AgentTool.tool_id.in_(tool_ids),
                    )
                )
                agent_assignments = agent_assignments_r.scalars().all()
                if len(agent_assignments) >= len(existing_server_tools):
                    tool_names = [t.display_name for t in existing_server_tools[:5]]
                    more = f" ... and {len(existing_server_tools) - 5} more" if len(existing_server_tools) > 5 else ""
                    return (
                        f"⏭️ You already have **{len(existing_server_tools)}** tools from this MCP server installed:\n"
                        + "\n".join(f"  • {n}" for n in tool_names) + more
                        + "\n\nNo action needed. These tools are ready to use."
                        + "\n\n💡 If tools stopped working (e.g. OAuth expired), use `import_mcp_server(server_id=\"....\", reauthorize=true)` to re-authorize."
                    )
    except Exception:
        pass  # non-critical — proceed to normal import flow

    # Step 1: Search for server by ID
    headers = {"Accept": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(
                f"{SMITHERY_API_BASE}/servers",
                params={"q": server_id.lstrip("@"), "pageSize": 5},
                headers=headers,
            )
            if resp.status_code != 200:
                return f"❌ Server '{server_id}' not found on Smithery (HTTP {resp.status_code})"
            data = resp.json()
            servers = data.get("servers", [])
            server_info = None
            clean_id = server_id.lstrip("@")
            for s in servers:
                if s.get("qualifiedName") == clean_id or s.get("qualifiedName") == server_id:
                    server_info = s
                    break
            if not server_info and servers:
                server_info = servers[0]
            if not server_info:
                return f"❌ Server '{server_id}' not found on Smithery."
    except Exception as e:
        return f"❌ Failed to fetch server info: {str(e)[:200]}"

    display_name = server_info.get("displayName", server_id.split("/")[-1])
    description = server_info.get("description", "")
    qualified_name = server_info.get("qualifiedName", server_id.lstrip("@"))

    # Check if server supports remote hosting
    if not server_info.get("remote"):
        return (
            f"⚠️ **{display_name}** (`{qualified_name}`) does not support remote hosting via Smithery Connect.\n"
            f"This server requires local installation and cannot be imported automatically.\n"
            f"🔗 {server_info.get('homepage', '')}"
        )

    # Step 2: Get full server details including tools from registry API
    tools_discovered = []
    deployment_url = None
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            detail_resp = await client.get(
                f"{SMITHERY_API_BASE}/servers/{qualified_name}",
                headers=headers,
            )
            if detail_resp.status_code == 200:
                detail = detail_resp.json()
                deployment_url = detail.get("deploymentUrl")
                raw_tools = detail.get("tools", [])
                tools_discovered = [
                    {
                        "name": t.get("name", ""),
                        "description": t.get("description", ""),
                        "inputSchema": t.get("inputSchema", {}),
                    }
                    for t in raw_tools if t.get("name")
                ]
                logger.info(f"[ResourceDiscovery] Got {len(tools_discovered)} tools from registry for {qualified_name}")
            else:
                logger.warning(f"[ResourceDiscovery] Could not fetch detail for {qualified_name}: HTTP {detail_resp.status_code}")
    except Exception as e:
        logger.error(f"[ResourceDiscovery] Could not fetch server detail: {e}")

    # Step 3: Determine the MCP server URL for runtime execution
    base_mcp_url = deployment_url or f"https://{qualified_name}.run.tools"

    # Step 3.5: Auto-create Smithery Connect namespace + connection
    smithery_config = {}  # will be merged into every AgentTool.config
    auth_message = ""

    # On refresh, reuse the existing Smithery connection rather than creating a
    # new one. Creating a connection can mint a fresh connection_id and force a
    # new OAuth authorization — refresh must never do that. The existing
    # connection is stored in the AgentTool config of this server's tools.
    stored_conn = None
    if refresh:
        try:
            async with async_session() as db:
                stored_r = await db.execute(
                    select(AgentTool)
                    .join(Tool, AgentTool.tool_id == Tool.id)
                    .where(
                        AgentTool.agent_id == agent_id,
                        Tool.type == "mcp",
                        Tool.mcp_server_name == display_name,
                    )
                )
                for at in stored_r.scalars().all():
                    c = at.config or {}
                    if c.get("smithery_namespace") and c.get("smithery_connection_id"):
                        stored_conn = {
                            "smithery_namespace": c["smithery_namespace"],
                            "smithery_connection_id": c["smithery_connection_id"],
                        }
                        break
        except Exception:
            stored_conn = None

    if stored_conn:
        smithery_config = stored_conn
    else:
        conn_result = await _ensure_smithery_connection(api_key, base_mcp_url, display_name)
        if "error" in conn_result:
            auth_message = f"\n\n⚠️ Could not auto-create Smithery connection: {conn_result['error']}"
        else:
            smithery_config = {
                "smithery_namespace": conn_result["namespace"],
                "smithery_connection_id": conn_result["connection_id"],
            }
            if conn_result.get("auth_url"):
                auth_message = (
                    f"\n\n🔐 **OAuth 授权需要**: 请在浏览器中访问以下链接完成授权：\n"
                    f"{conn_result['auth_url']}\n"
                    f"授权完成后，工具即可使用。"
                )

    # Step 3.6: Override registry-advertised schema with the runtime server's
    # actual tools/list. Smithery's registry detail can drift behind the live
    # server (we hit this with shibui/finance: registry said `sql`, server
    # required `user_prompt` + `query`). The truth is whatever tools/list
    # returns at call time, so prefer it whenever available.
    live_tools_ok = False
    deferred_live_resolution = False
    if smithery_config:
        ns_ = smithery_config["smithery_namespace"]
        conn_ = smithery_config["smithery_connection_id"]
        import json as _json
        # The live tools/list is the authoritative source for tool NAMES, so a
        # transient SSE/parse/timeout/5xx hiccup must not silently fall back to
        # the drift-prone registry names (Composio-style UPPERCASE that the
        # runtime server may not recognize). Retry a few times before giving up.
        max_live_attempts = 3
        for attempt in range(1, max_live_attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    live_resp = await client.post(
                        f"https://api.smithery.ai/connect/{ns_}/{conn_}/mcp",
                        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json",
                            "Accept": "application/json, text/event-stream",
                        },
                    )
                if live_resp.status_code == 200:
                    live_data = None
                    # Smithery Connect returns SSE; parse the first data: line.
                    for line in live_resp.text.split("\n"):
                        line = line.strip()
                        if line.startswith("data: "):
                            try:
                                live_data = _json.loads(line[6:])
                                break
                            except _json.JSONDecodeError:
                                pass
                    if live_data is None:
                        try:
                            live_data = _json.loads(live_resp.text)
                        except _json.JSONDecodeError:
                            live_data = None
                    live_tools = (live_data or {}).get("result", {}).get("tools", []) if live_data else []
                    # MCP servers also return prompts here; only treat actual tools.
                    live_tools_normalized = [
                        {
                            "name": t.get("name", ""),
                            "description": t.get("description", ""),
                            "inputSchema": t.get("inputSchema", {}),
                        }
                        for t in live_tools
                        if t.get("name") and isinstance(t.get("inputSchema"), dict)
                    ]
                    if live_tools_normalized:
                        logger.info(
                            f"[ResourceDiscovery] Using live tools/list for {qualified_name}: "
                            f"{len(live_tools_normalized)} tool(s) override registry's "
                            f"{len(tools_discovered)} (attempt {attempt})"
                        )
                        tools_discovered = live_tools_normalized
                        live_tools_ok = True
                        break
                    # HTTP 200 with no tools almost always means the connection
                    # is not authorized yet (OAuth pending). Retrying inside this
                    # import won't change that, so stop early and fail closed.
                    logger.warning(
                        f"[ResourceDiscovery] Live tools/list for {qualified_name} "
                        f"returned no tools (attempt {attempt}) — likely pending OAuth"
                    )
                    break
                else:
                    logger.warning(
                        f"[ResourceDiscovery] Live tools/list HTTP {live_resp.status_code} "
                        f"for {qualified_name} (attempt {attempt}/{max_live_attempts})"
                    )
            except Exception as e:
                logger.warning(
                    f"[ResourceDiscovery] Live tools/list failed for {qualified_name} "
                    f"(attempt {attempt}/{max_live_attempts}): {e}"
                )
            if attempt < max_live_attempts:
                await asyncio.sleep(1.5 * attempt)

    # The live tools/list is the SINGLE authoritative source for tool names on
    # Smithery-hosted servers. When it is unavailable we must NOT persist the
    # registry's drift-prone names, or calls fail later with
    # `-32602 Tool ... not found`.
    if not live_tools_ok:
        # Refresh must be non-destructive: if we could not fetch the authoritative
        # live tools/list, do NOT reconcile against possibly-stale registry data
        # (that could wrongly disable valid tools). Abort with no changes.
        if refresh:
            return (
                f"⚠️ Refresh aborted for **{display_name}**: could not fetch the live tool "
                f"list from the server right now, so no changes were made (to avoid "
                f"disabling valid tools).\n\n"
                f"Please try again in a moment. If the connection's authorization has "
                f"expired, use `import_mcp_server(server_id=\"{server_id}\", reauthorize=true)`."
            )
        # Fresh import: never persist the registry's drift-prone names.
        if not smithery_config:
            # No Smithery connection could be established, so the background
            # refresh daemon (which keys off smithery_namespace +
            # connection_id) can never discover or re-resolve this server.
            # Fail explicitly rather than leaving an orphan placeholder that
            # silently never gains its tools.
            return (
                f"⚠️ Import incomplete for **{display_name}** (`{server_id}`): "
                f"could not establish a Smithery connection, so tool names could "
                f"not be verified against the live server. No tools were created "
                f"(to avoid registering names the server won't recognize).\n\n"
                f"Please try again in a moment, or use "
                f"`import_mcp_server(server_id=\"{server_id}\", reauthorize=true)`."
                + (auth_message or "")
            )
        # Connection exists but the live tools/list isn't available yet
        # (typically OAuth still pending). Drop the registry tools and create
        # only a discoverable placeholder below — it carries the Smithery
        # connection, so the background auto-refresh daemon materializes the
        # real, live-named tools once the connection is authorized / reachable.
        if tools_discovered:
            logger.warning(
                f"[ResourceDiscovery] Fresh import of {qualified_name}: live "
                f"tools/list unavailable; NOT persisting {len(tools_discovered)} "
                f"registry-named tool(s). Deferring to placeholder + auto-refresh."
            )
        tools_discovered = []
        deferred_live_resolution = True

    # Persist the originating Smithery server_id so the background auto-refresh
    # job can re-resolve this exact server later without guessing from the
    # tool-name prefix (which loses the '/' in qualified names like
    # "@anthropic/github" -> "anthropic_github").
    if smithery_config:
        smithery_config["smithery_server_id"] = server_id

    # Merge smithery_config + user config for AgentTool
    agent_tool_config = {**smithery_config, **config}

    async with async_session() as db:
        imported_tools = []
        refresh_added: list[str] = []
        refresh_updated: list[str] = []
        refresh_removed: list[str] = []
        refresh_unchanged: list[str] = []

        # Helper: ensure AgentTool link exists and save config.
        # `enabled=None` preserves an existing link's enabled flag (and defaults
        # new links to enabled); pass enabled=False to keep a link present but
        # hidden from the LLM (used for the deferred pending-auth placeholder so
        # it remains discoverable by the auto-refresh daemon without being a
        # callable, broken tool). `extra_config` merges extra keys on top.
        async def _ensure_agent_tool(
            tool_id: uuid.UUID,
            *,
            enabled: bool | None = None,
            extra_config: dict | None = None,
        ):
            link_config = {**agent_tool_config, **(extra_config or {})}
            agent_check = await db.execute(
                select(AgentTool).where(
                    AgentTool.agent_id == agent_id,
                    AgentTool.tool_id == tool_id,
                )
            )
            at = agent_check.scalar_one_or_none()
            if at:
                at.config = {**(at.config or {}), **link_config}
                if enabled is not None:
                    at.enabled = enabled
            else:
                db.add(AgentTool(
                    agent_id=agent_id, tool_id=tool_id,
                    enabled=True if enabled is None else enabled,
                    source="user_installed", installed_by_agent_id=agent_id,
                    config=link_config,
                ))

        # On re-import/reauthorize: update ALL existing tools for this server
        if config or reauthorize:
            existing_server_tools_r = await db.execute(
                select(Tool).where(Tool.mcp_server_name == display_name, Tool.type == "mcp")
            )
            for et in existing_server_tools_r.scalars().all():
                et.mcp_server_url = base_mcp_url
                await _ensure_agent_tool(et.id)

        if tools_discovered:
            # Clean up the generic/placeholder entry now that individual
            # live-named tools exist, so a callable-but-nameless `mcp_<server>`
            # tool can never linger and trigger `-32602 Tool mcp_<server> not
            # found`. The placeholder Tool row is GLOBAL and may be shared by
            # other agents that are still pending auth, so only unlink THIS agent
            # and delete the Tool row when no agent references it anymore.
            generic_name = f"mcp_{server_id.replace('/', '_').replace('@', '')}"
            old_generic_r = await db.execute(
                select(Tool).where(Tool.name == generic_name, Tool.type == "mcp")
            )
            old_generic = old_generic_r.scalar_one_or_none()
            if old_generic:
                await db.execute(
                    AgentTool.__table__.delete().where(
                        AgentTool.tool_id == old_generic.id,
                        AgentTool.agent_id == agent_id,
                    )
                )
                await db.flush()
                remaining_r = await db.execute(
                    select(AgentTool.id).where(AgentTool.tool_id == old_generic.id).limit(1)
                )
                if remaining_r.scalar_one_or_none() is None:
                    await db.delete(old_generic)
                await db.flush()

            # Create one Tool record per MCP tool
            for mcp_tool in tools_discovered:
                tool_name = f"mcp_{server_id.replace('/', '_').replace('@', '')}_{mcp_tool['name']}"
                tool_display = f"{display_name}: {mcp_tool['name']}"

                existing_tool = None
                # On refresh, match by the live tool name within this server first.
                # The stored Tool.name prefix can drift from the live tool name
                # (e.g. legacy `mcp_gmail_GMAIL_FETCH_EMAILS` vs live `fetch_emails`),
                # so matching on the constructed name alone would create duplicates.
                if refresh:
                    by_mcp_r = await db.execute(
                        select(Tool).where(
                            Tool.type == "mcp",
                            Tool.mcp_server_name == display_name,
                            Tool.mcp_tool_name == mcp_tool["name"],
                        )
                    )
                    existing_tool = by_mcp_r.scalars().first()
                if existing_tool is None:
                    existing_r = await db.execute(select(Tool).where(Tool.name == tool_name))
                    existing_tool = existing_r.scalar_one_or_none()
                if existing_tool:
                    changed = False
                    if existing_tool.mcp_server_url != base_mcp_url:
                        existing_tool.mcp_server_url = base_mcp_url
                        changed = True
                    if refresh:
                        new_desc = (mcp_tool.get("description", description) or "")[:500]
                        new_schema = mcp_tool.get("inputSchema", {"type": "object", "properties": {}})
                        if (existing_tool.description or "") != new_desc:
                            existing_tool.description = new_desc
                            changed = True
                        if existing_tool.parameters_schema != new_schema:
                            existing_tool.parameters_schema = new_schema
                            changed = True
                        if not existing_tool.enabled:
                            existing_tool.enabled = True
                            changed = True
                    await _ensure_agent_tool(existing_tool.id)
                    if refresh:
                        (refresh_updated if changed else refresh_unchanged).append(tool_display)
                    elif reauthorize:
                        imported_tools.append(f"🔄 {tool_display} (reauthorized)")
                    elif config:
                        imported_tools.append(f"🔄 {tool_display} (config updated)")
                    else:
                        imported_tools.append(f"⏭️ {tool_display} (already imported)")
                    continue

                tool = Tool(
                    name=tool_name,
                    display_name=tool_display,
                    description=mcp_tool.get("description", description)[:500],
                    type="mcp",
                    category="mcp",
                    icon="🔌",
                    parameters_schema=mcp_tool.get("inputSchema", {"type": "object", "properties": {}}),
                    mcp_server_url=base_mcp_url,
                    mcp_server_name=display_name,
                    mcp_tool_name=mcp_tool["name"],
                    enabled=True,
                    is_default=False,
                    source="agent",
                )
                db.add(tool)
                await db.flush()
                await _ensure_agent_tool(tool.id)
                imported_tools.append(f"✅ {tool_display}")
                if refresh:
                    refresh_added.append(tool_display)

            # Refresh: disable local tools the live server no longer exposes
            if refresh:
                live_names = {t["name"] for t in tools_discovered}
                stale_r = await db.execute(
                    select(Tool).where(
                        Tool.type == "mcp",
                        Tool.mcp_server_name == display_name,
                    )
                )
                for st in stale_r.scalars().all():
                    if st.mcp_tool_name and st.mcp_tool_name not in live_names and st.enabled:
                        st.enabled = False
                        refresh_removed.append(st.display_name)
        else:
            # Fallback: create a single generic tool entry
            tool_name = f"mcp_{server_id.replace('/', '_').replace('@', '')}"
            tool_display = display_name

            existing_r = await db.execute(select(Tool).where(Tool.name == tool_name))
            existing_tool = existing_r.scalar_one_or_none()
            if existing_tool:
                existing_tool.mcp_server_url = base_mcp_url
                if deferred_live_resolution:
                    await _ensure_agent_tool(
                        existing_tool.id, enabled=False,
                        extra_config={"mcp_pending_live_resolution": True},
                    )
                else:
                    await _ensure_agent_tool(existing_tool.id)
                if config:
                    await db.commit()
                    return f"🔄 {tool_display} config updated. The tool is now ready to use." + (auth_message or "")
                else:
                    await db.commit()
                    if deferred_live_resolution:
                        return (
                            f"⏳ {tool_display} — tool names are synced from the live "
                            f"server; the individual tools will appear automatically "
                            f"once the connection is authorized." + (auth_message or "")
                        )
                    return f"⏭️ {tool_display} is already imported." + (auth_message or "")

            tool = Tool(
                name=tool_name,
                display_name=tool_display,
                description=description[:500] or f"MCP Server: {server_id}",
                type="mcp",
                category="mcp",
                icon="🔌",
                parameters_schema={"type": "object", "properties": {}},
                mcp_server_url=base_mcp_url,
                mcp_server_name=display_name,
                enabled=True,
                is_default=False,
                source="agent",
            )
            db.add(tool)
            await db.flush()
            if deferred_live_resolution:
                # Keep the placeholder OUT of the LLM's callable tool list (it has
                # no real mcp_tool_name, so calling it yields `-32602 Tool
                # mcp_<server> not found`). It stays linked but disabled purely as
                # a discovery anchor: the auto-refresh daemon and the lazy
                # on-demand resolver materialize the real, live-named tools once
                # the connection is authorized.
                await _ensure_agent_tool(
                    tool.id, enabled=False,
                    extra_config={"mcp_pending_live_resolution": True},
                )
                imported_tools.append(
                    f"⏳ {tool_display} — tool names are synced from the live server; "
                    f"the individual tools will appear automatically once the "
                    f"connection is authorized."
                )
            else:
                await _ensure_agent_tool(tool.id)
                imported_tools.append(f"✅ {tool_display} (tool list not available from registry — may need configuration)")

        await db.commit()

    if refresh:
        lines = [f"🔄 Refreshed MCP server: **{display_name}** (`{server_id}`)\n"]
        if refresh_added:
            lines.append(f"✅ Added {len(refresh_added)} new tool(s):")
            lines += [f"  • {n}" for n in refresh_added]
        if refresh_updated:
            lines.append(f"♻️ Updated {len(refresh_updated)} tool(s):")
            lines += [f"  • {n}" for n in refresh_updated]
        if refresh_removed:
            lines.append(f"🚫 Disabled {len(refresh_removed)} removed tool(s):")
            lines += [f"  • {n}" for n in refresh_removed]
        if not (refresh_added or refresh_updated or refresh_removed):
            lines.append(
                f"Everything is already up to date — no changes ({len(refresh_unchanged)} tools)."
            )
        else:
            lines.append(f"\n(unchanged: {len(refresh_unchanged)})")
        lines.append(f"\n📡 MCP Server URL: `{base_mcp_url}`")
        if auth_message:
            lines.append(auth_message)
        return "\n".join(lines)

    result = f"🔌 Imported MCP server: **{display_name}** (`{server_id}`)\n\n"
    result += "\n".join(imported_tools)
    result += f"\n\n📡 MCP Server URL: `{base_mcp_url}`"
    if auth_message:
        result += auth_message
    else:
        result += "\n\n💡 The imported tools are now available for use."
    return result


# ── Direct URL Import ───────────────────────────────────────────

async def import_mcp_direct(
    mcp_url: str,
    agent_id: uuid.UUID,
    server_name: str | None = None,
    api_key: str | None = None,
) -> str:
    """Import an MCP server by directly connecting to its HTTP/SSE endpoint.

    This bypasses Smithery entirely — useful for self-hosted or third-party
    MCP servers that provide their own public endpoint.
    """
    from app.services.mcp_client import MCPClient

    # Build URL with apiKey if provided
    full_url = mcp_url
    if api_key and "?" in mcp_url:
        full_url = f"{mcp_url}&apiKey={api_key}"
    elif api_key:
        full_url = f"{mcp_url}?apiKey={api_key}"

    display_name = server_name or mcp_url.split("//")[-1].split("/")[0].split(":")[0]
    safe_name = display_name.replace(".", "_").replace("/", "_").replace(":", "_").replace("-", "_")

    # Try to list tools from the endpoint
    tools_discovered = []
    try:
        client = MCPClient(full_url)
        tools_discovered = await client.list_tools()
        logger.info(f"[DirectImport] Got {len(tools_discovered)} tools from {mcp_url}")
    except Exception as e:
        logger.error(f"[DirectImport] Could not list tools from {mcp_url}: {e}")

    # Config to store in AgentTool
    agent_tool_config = {}
    if api_key:
        agent_tool_config["api_key"] = api_key

    async with async_session() as db:
        imported_tools = []

        async def _ensure_agent_tool(tool_id: uuid.UUID):
            agent_check = await db.execute(
                select(AgentTool).where(
                    AgentTool.agent_id == agent_id,
                    AgentTool.tool_id == tool_id,
                )
            )
            at = agent_check.scalar_one_or_none()
            if at:
                at.config = {**(at.config or {}), **agent_tool_config}
            else:
                db.add(AgentTool(
                    agent_id=agent_id, tool_id=tool_id, enabled=True,
                    source="user_installed", installed_by_agent_id=agent_id,
                    config=agent_tool_config,
                ))

        if tools_discovered:
            for mcp_tool in tools_discovered:
                tool_name = f"mcp_{safe_name}_{mcp_tool['name']}"
                tool_display = f"{display_name}: {mcp_tool['name']}"

                existing_r = await db.execute(select(Tool).where(Tool.name == tool_name))
                existing_tool = existing_r.scalar_one_or_none()
                if existing_tool:
                    existing_tool.mcp_server_url = mcp_url
                    await _ensure_agent_tool(existing_tool.id)
                    imported_tools.append(f"⏭️ {tool_display} (already imported)")
                    continue

                tool = Tool(
                    name=tool_name,
                    display_name=tool_display,
                    description=mcp_tool.get("description", "")[:500],
                    type="mcp",
                    category="mcp",
                    icon="🔌",
                    parameters_schema=mcp_tool.get("inputSchema", {"type": "object", "properties": {}}),
                    mcp_server_url=mcp_url,
                    mcp_server_name=display_name,
                    mcp_tool_name=mcp_tool["name"],
                    enabled=True,
                    is_default=False,
                    source="agent",
                )
                db.add(tool)
                await db.flush()
                await _ensure_agent_tool(tool.id)
                imported_tools.append(f"✅ {tool_display}")
        else:
            tool_name = f"mcp_{safe_name}"
            existing_r = await db.execute(select(Tool).where(Tool.name == tool_name))
            existing_tool = existing_r.scalar_one_or_none()
            if existing_tool:
                existing_tool.mcp_server_url = mcp_url
                await _ensure_agent_tool(existing_tool.id)
                return f"⏭️ {display_name} is already imported."

            tool = Tool(
                name=tool_name,
                display_name=display_name,
                description=f"MCP Server: {mcp_url}",
                type="mcp",
                category="mcp",
                icon="🔌",
                parameters_schema={"type": "object", "properties": {}},
                mcp_server_url=mcp_url,
                mcp_server_name=display_name,
                enabled=True,
                is_default=False,
                source="agent",
            )
            db.add(tool)
            await db.flush()
            await _ensure_agent_tool(tool.id)
            imported_tools.append(f"✅ {display_name} (tools couldn't be listed — server may need configuration)")

        await db.commit()

    result = f"🔌 Imported MCP server: **{display_name}**\n\n"
    result += "\n".join(imported_tools)
    result += f"\n\n📡 MCP Server URL: `{mcp_url}`"
    result += "\n\n💡 The imported tools are now available for use."
    return result


# ── Atlassian Rovo MCP Auto-Seeding ─────────────────────────────────────────

ATLASSIAN_ROVO_MCP_URL = "https://mcp.atlassian.com/v1/mcp"
ATLASSIAN_ROVO_SERVER_NAME = "Atlassian Rovo"
ATLASSIAN_ROVO_TOOL_PREFIX = "atlassian_rovo_"


async def seed_atlassian_rovo_tools(api_key: str) -> None:
    """Connect to Atlassian Rovo MCP and seed all available tools as platform-level MCP tools.

    Called on startup when an API key is configured. Existing tools are updated in-place;
    new tools discovered from the server are created. The api_key is stored in each tool's
    config so _execute_mcp_tool can authenticate requests.
    """
    from app.services.mcp_client import MCPClient

    logger.info(f"[AtlassianRovo] Connecting to {ATLASSIAN_ROVO_MCP_URL} ...")
    try:
        client = MCPClient(ATLASSIAN_ROVO_MCP_URL, api_key=api_key)
        tools_discovered = await client.list_tools()
    except Exception as e:
        logger.error(f"[AtlassianRovo] Could not list tools: {e}")
        return

    if not tools_discovered:
        logger.warning("[AtlassianRovo] No tools returned from server")
        return

    logger.info(f"[AtlassianRovo] Discovered {len(tools_discovered)} tools")

    async with async_session() as db:
        upserted = 0
        for mcp_tool in tools_discovered:
            raw_name = mcp_tool.get("name", "")
            if not raw_name:
                continue

            tool_name = f"{ATLASSIAN_ROVO_TOOL_PREFIX}{raw_name}"
            tool_display = f"Atlassian: {raw_name}"
            tool_desc = mcp_tool.get("description", "")[:500]
            tool_schema = mcp_tool.get("inputSchema", {"type": "object", "properties": {}})

            # Determine icon based on tool name hints
            if "jira" in raw_name.lower() or "issue" in raw_name.lower():
                icon = "🔵"
            elif "confluence" in raw_name.lower() or "page" in raw_name.lower():
                icon = "📘"
            elif "compass" in raw_name.lower() or "component" in raw_name.lower():
                icon = "🧭"
            else:
                icon = "🔷"

            existing_r = await db.execute(select(Tool).where(Tool.name == tool_name))
            existing_tool = existing_r.scalar_one_or_none()

            if existing_tool:
                # Update description and schema in case they changed
                existing_tool.description = tool_desc
                existing_tool.parameters_schema = tool_schema
                existing_tool.config = {"api_key": api_key}
            else:
                tool = Tool(
                    name=tool_name,
                    display_name=tool_display,
                    description=tool_desc,
                    type="mcp",
                    category="atlassian",
                    icon=icon,
                    parameters_schema=tool_schema,
                    mcp_server_url=ATLASSIAN_ROVO_MCP_URL,
                    mcp_server_name=ATLASSIAN_ROVO_SERVER_NAME,
                    mcp_tool_name=raw_name,
                    enabled=True,
                    is_default=False,
                    config={"api_key": api_key},
                    source="admin",
                )
                db.add(tool)
                upserted += 1

        await db.commit()

    logger.info(f"[AtlassianRovo] Seeded {upserted} new Atlassian Rovo tools")


async def refresh_atlassian_rovo_api_key(api_key: str) -> None:
    """Update the stored api_key in all Atlassian Rovo tool records.

    Called when the user updates the API key via the config UI.
    """
    async with async_session() as db:
        from sqlalchemy import update as _update
        await db.execute(
            _update(Tool)
            .where(Tool.mcp_server_name == ATLASSIAN_ROVO_SERVER_NAME, Tool.type == "mcp")
            .values(config={"api_key": api_key})
        )
        await db.commit()
    logger.info("[AtlassianRovo] API key refreshed for all Rovo tools")
