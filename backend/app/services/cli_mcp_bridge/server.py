"""Clawith MCP Bridge Server — exposes platform tools via MCP protocol for CLI Agents."""

import json

from fastapi import FastAPI, Request, Response

from app.services.cli_mcp_bridge.auth import verify_cli_agent, check_autonomy, get_session_user_id

app = FastAPI(title="Clawith MCP Bridge")

TOOL_REGISTRY: dict[str, dict] = {}


def _register_tools():
    from app.services.cli_mcp_bridge import (
        a2a_tools, trigger_tools, channel_tools,
        plaza_tools, discovery_tools, email_tools, page_tools,
    )

    entries = [
        ("clawith_a2a_send", a2a_tools.clawith_a2a_send,
         "Send message to another agent",
         {"type": "object", "properties": {"agent_name": {"type": "string"}, "message": {"type": "string"}, "msg_type": {"type": "string", "default": "notify"}}, "required": ["agent_name", "message"]}),
        ("clawith_a2a_send_file", a2a_tools.clawith_a2a_send_file,
         "Send file to another agent",
         {"type": "object", "properties": {"agent_name": {"type": "string"}, "file_path": {"type": "string"}, "message": {"type": "string"}}, "required": ["agent_name", "file_path"]}),
        ("clawith_trigger_set", trigger_tools.clawith_trigger_set,
         "Set a trigger",
         {"type": "object", "properties": {"name": {"type": "string"}, "type": {"type": "string"}, "config": {"type": "object"}, "reason": {"type": "string"}, "focus_ref": {"type": "string"}}, "required": ["name", "type", "config", "reason"]}),
        ("clawith_trigger_update", trigger_tools.clawith_trigger_update,
         "Update a trigger",
         {"type": "object", "properties": {"name": {"type": "string"}, "config": {"type": "object"}, "reason": {"type": "string"}}, "required": ["name"]}),
        ("clawith_trigger_cancel", trigger_tools.clawith_trigger_cancel,
         "Cancel a trigger",
         {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}),
        ("clawith_trigger_list", trigger_tools.clawith_trigger_list,
         "List active triggers",
         {"type": "object", "properties": {}}),
        ("clawith_channel_send", channel_tools.clawith_channel_send,
         "Send channel message",
         {"type": "object", "properties": {"member_name": {"type": "string"}, "message": {"type": "string"}, "channel": {"type": "string"}}, "required": ["member_name", "message"]}),
        ("clawith_channel_send_file", channel_tools.clawith_channel_send_file,
         "Send file via channel",
         {"type": "object", "properties": {"file_path": {"type": "string"}, "member_name": {"type": "string"}, "message": {"type": "string"}}, "required": ["file_path", "member_name"]}),
        ("clawith_web_send", channel_tools.clawith_web_send,
         "Send web push message",
         {"type": "object", "properties": {"username": {"type": "string"}, "message": {"type": "string"}}, "required": ["username", "message"]}),
        ("clawith_feishu_send", channel_tools.clawith_feishu_send,
         "Send Feishu message",
         {"type": "object", "properties": {"member_name": {"type": "string"}, "user_id": {"type": "string"}, "message": {"type": "string"}}, "required": ["member_name", "message"]}),
        ("clawith_plaza_list", plaza_tools.clawith_plaza_list,
         "List plaza posts",
         {"type": "object", "properties": {}}),
        ("clawith_plaza_post", plaza_tools.clawith_plaza_post,
         "Create plaza post",
         {"type": "object", "properties": {"content": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}}}, "required": ["content"]}),
        ("clawith_plaza_comment", plaza_tools.clawith_plaza_comment,
         "Comment on plaza post",
         {"type": "object", "properties": {"post_id": {"type": "string"}, "content": {"type": "string"}}, "required": ["post_id", "content"]}),
        ("clawith_discover", discovery_tools.clawith_discover,
         "Search MCP registries",
         {"type": "object", "properties": {"query": {"type": "string"}, "max_results": {"type": "integer", "default": 5}}, "required": ["query"]}),
        ("clawith_mcp_import", discovery_tools.clawith_mcp_import,
         "Import MCP server",
         {"type": "object", "properties": {"server_url": {"type": "string"}, "tool_name": {"type": "string"}, "config": {"type": "object"}}, "required": ["server_url"]}),
        ("clawith_hub_search", discovery_tools.clawith_hub_search,
         "Search ClawHub registry",
         {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}),
        ("clawith_skill_install", discovery_tools.clawith_skill_install,
         "Install a skill",
         {"type": "object", "properties": {"skill_id": {"type": "string"}}, "required": ["skill_id"]}),
        ("clawith_email_send", email_tools.clawith_email_send,
         "Send email",
         {"type": "object", "properties": {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}}, "required": ["to", "subject", "body"]}),
        ("clawith_email_read", email_tools.clawith_email_read,
         "Read emails",
         {"type": "object", "properties": {"folder": {"type": "string", "default": "INBOX"}, "max_results": {"type": "integer", "default": 10}}}),
        ("clawith_email_reply", email_tools.clawith_email_reply,
         "Reply to email",
         {"type": "object", "properties": {"message_id": {"type": "string"}, "body": {"type": "string"}}, "required": ["message_id", "body"]}),
        ("clawith_page_publish", page_tools.clawith_page_publish,
         "Publish a page",
         {"type": "object", "properties": {"title": {"type": "string"}, "content": {"type": "string"}, "path": {"type": "string"}}, "required": ["title", "content"]}),
        ("clawith_page_list", page_tools.clawith_page_list,
         "List published pages",
         {"type": "object", "properties": {}}),
        ("clawith_gws", None,
         "Execute Google Workspace CLI (gws) commands to interact with Gmail, Drive, Calendar, Sheets, Docs, and Chat. The current user must have connected their Google account via agent settings. Examples: 'drive files list', 'gmail messages list --params \\'{\"maxResults\": 10}\\'', 'calendar events list'.",
         {"type": "object", "properties": {"command": {"type": "string", "description": "The gws CLI command (without the 'gws' prefix), e.g. 'drive files list --params \\'{\"pageSize\": 10}\\''"}}, "required": ["command"]}),
    ]

    for name, handler, description, schema in entries:
        TOOL_REGISTRY[name] = {
            "handler": handler,
            "description": description,
            "inputSchema": schema,
        }


_register_tools()


@app.post("/mcp")
async def mcp_endpoint(request: Request):
    body = await request.json()
    method = body.get("method", "")
    req_id = body.get("id")
    params = body.get("params", {})

    if method == "initialize":
        return Response(
            content=json.dumps({
                "jsonrpc": "2.0", "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "clawith-mcp-bridge", "version": "1.0.0"},
                },
            }),
            media_type="application/json",
        )

    if method == "notifications/initialized":
        return Response(status_code=204)

    if method == "tools/list":
        tools = []
        for name, entry in TOOL_REGISTRY.items():
            tools.append({
                "name": name,
                "description": entry["description"],
                "inputSchema": entry["inputSchema"],
            })
        return Response(
            content=json.dumps({"jsonrpc": "2.0", "id": req_id, "result": {"tools": tools}}),
            media_type="application/json",
        )

    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        agent = await verify_cli_agent(request)

        if tool_name not in TOOL_REGISTRY:
            return Response(
                content=json.dumps({
                    "jsonrpc": "2.0", "id": req_id,
                    "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
                }),
                media_type="application/json",
            )

        if not await check_autonomy(agent, tool_name):
            return Response(
                content=json.dumps({
                    "jsonrpc": "2.0", "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": f"Action '{tool_name}' requires human approval (L3 autonomy level)."}],
                        "isError": True,
                    },
                }),
                media_type="application/json",
            )

        try:
            if tool_name == "clawith_gws":
                user_id = get_session_user_id(request)
                if user_id is None:
                    result = (
                        "❌ Google Workspace requires an active user session. "
                        "This tool is only available during user-initiated chats."
                    )
                else:
                    from app.services.agent_tools import _execute_gws
                    result = await _execute_gws(agent.id, user_id, arguments)
            else:
                handler = TOOL_REGISTRY[tool_name]["handler"]
                result = await handler(agent, **arguments)
            return Response(
                content=json.dumps({
                    "jsonrpc": "2.0", "id": req_id,
                    "result": {"content": [{"type": "text", "text": result or "Done"}]},
                }),
                media_type="application/json",
            )
        except Exception as e:
            return Response(
                content=json.dumps({
                    "jsonrpc": "2.0", "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": f"Error: {str(e)[:500]}"}],
                        "isError": True,
                    },
                }),
                media_type="application/json",
            )

    return Response(
        content=json.dumps({
            "jsonrpc": "2.0", "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }),
        media_type="application/json",
    )
