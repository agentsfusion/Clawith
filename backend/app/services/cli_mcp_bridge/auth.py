import base64
import hashlib
import hmac
import json
import time
import uuid

from fastapi import HTTPException, Request
from sqlalchemy import select

from app.database import async_session


# ─── Signed CLI session tokens ────────────────────────────────────────
#
# The CLI MCP bridge needs to know which (agent_id, user_id) pair is
# driving the current chat — for example, `clawith_gws` looks up the
# Google OAuth token under (agent_id, user_id). A raw `X-User-Id` header
# would be spoofable by any holder of the agent API key, letting them
# select another user's token row for the same agent.
#
# We instead mint a short-lived HMAC token in the WebSocket handler
# (using the server's SECRET_KEY) that binds {agent_id, user_id, exp}
# together. The bridge verifies the signature, the binding to the
# request's X-Agent-Id, and the expiry before trusting the user_id.

_SESSION_TOKEN_TTL_S = 30 * 60  # 30 minutes
_SESSION_TOKEN_VERSION = "v1"
_BRIDGE_AUTH_TOKEN_TTL_S = 30 * 60  # 30 minutes
_BRIDGE_AUTH_TOKEN_VERSION = "b1"


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _secret_key() -> bytes:
    from app.config import get_settings
    return get_settings().SECRET_KEY.encode("utf-8")


def mint_session_token(agent_id: uuid.UUID | str, user_id: uuid.UUID | str,
                       ttl_s: int = _SESSION_TOKEN_TTL_S) -> str:
    """Mint a short-lived HMAC token binding (agent_id, user_id, exp).

    Format: <payload_b64url>.<sig_b64url> where payload is JSON:
        {"v": "v1", "a": <agent_id>, "u": <user_id>, "e": <exp_unix>}
    and sig is HMAC-SHA256(SECRET_KEY, payload_b64url).
    """
    payload = {
        "v": _SESSION_TOKEN_VERSION,
        "a": str(agent_id),
        "u": str(user_id),
        "e": int(time.time()) + ttl_s,
    }
    payload_b = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig_b = _b64url(hmac.new(_secret_key(), payload_b.encode("ascii"), hashlib.sha256).digest())
    return f"{payload_b}.{sig_b}"


def _verify_session_token(token: str, expected_agent_id: str) -> uuid.UUID | None:
    """Verify a session token and return the bound user_id.

    Returns None on any failure (bad format, bad signature, expired,
    or agent mismatch). Never raises.
    """
    try:
        payload_b, sig_b = token.split(".", 1)
    except ValueError:
        return None
    try:
        expected_sig = _b64url(
            hmac.new(_secret_key(), payload_b.encode("ascii"), hashlib.sha256).digest()
        )
        if not hmac.compare_digest(expected_sig, sig_b):
            return None
        payload = json.loads(_b64url_decode(payload_b))
    except Exception:
        return None
    if payload.get("v") != _SESSION_TOKEN_VERSION:
        return None
    if str(payload.get("a", "")) != str(expected_agent_id):
        return None
    if int(payload.get("e", 0)) < int(time.time()):
        return None
    try:
        return uuid.UUID(str(payload.get("u", "")))
    except (ValueError, TypeError):
        return None


def get_session_user_id(request: Request) -> uuid.UUID | None:
    """Return the verified user_id bound to the current bridge call.

    Requires a valid, unexpired `X-Session-Token` HMAC'd by the server
    and bound to the same `X-Agent-Id` header. A raw `X-User-Id` header
    is NOT trusted — it is informational only and ignored.
    """
    token = request.headers.get("X-Session-Token", "")
    agent_id = request.headers.get("X-Agent-Id", "")
    if not token or not agent_id:
        return None
    return _verify_session_token(token, agent_id)


def mint_bridge_auth_token(agent_id: uuid.UUID | str,
                           ttl_s: int = _BRIDGE_AUTH_TOKEN_TTL_S) -> str:
    """Mint a short-lived HMAC token authenticating the bearer as the
    given CLI agent for bridge calls.

    Format mirrors mint_session_token but with version "b1" and only an
    agent_id claim:
        {"v": "b1", "a": <agent_id>, "e": <exp_unix>}

    The bridge listens on localhost only and is called exclusively by
    our own WebSocket handler in-process, so we replace the previous
    sha256(api_key) check (which was broken — the raw key generated at
    agent creation was thrown away and could never be reconstructed)
    with a server-internal HMAC check.
    """
    payload = {
        "v": _BRIDGE_AUTH_TOKEN_VERSION,
        "a": str(agent_id),
        "e": int(time.time()) + ttl_s,
    }
    payload_b = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig_b = _b64url(hmac.new(_secret_key(), payload_b.encode("ascii"), hashlib.sha256).digest())
    return f"{payload_b}.{sig_b}"


def _verify_bridge_auth_token(token: str, expected_agent_id: str) -> bool:
    """Return True iff token is a valid, unexpired bridge-auth token
    bound to expected_agent_id. Never raises."""
    try:
        payload_b, sig_b = token.split(".", 1)
    except ValueError:
        return False
    try:
        expected_sig = _b64url(
            hmac.new(_secret_key(), payload_b.encode("ascii"), hashlib.sha256).digest()
        )
        if not hmac.compare_digest(expected_sig, sig_b):
            return False
        payload = json.loads(_b64url_decode(payload_b))
    except Exception:
        return False
    if payload.get("v") != _BRIDGE_AUTH_TOKEN_VERSION:
        return False
    if str(payload.get("a", "")) != str(expected_agent_id):
        return False
    if int(payload.get("e", 0)) < int(time.time()):
        return False
    return True


async def verify_cli_agent(request: Request):
    agent_id = request.headers.get("X-Agent-Id")
    bearer = request.headers.get("Authorization", "").replace("Bearer ", "").strip()
    if not agent_id or not bearer:
        raise HTTPException(401, "Missing agent credentials")

    if not _verify_bridge_auth_token(bearer, agent_id):
        raise HTTPException(403, "Invalid API key")

    from app.models.agent import Agent
    async with async_session() as db:
        agent = await db.get(Agent, agent_id)
        if not agent or agent.agent_type != "cli":
            raise HTTPException(403, "Not a CLI agent")
        return agent


_TOOL_AUTONOMY_MAP = {
    "clawith_a2a_send": "send_feishu_message",
    "clawith_channel_send": "send_feishu_message",
    "clawith_trigger_set": "write_workspace_files",
    "clawith_plaza_post": "write_workspace_files",
    "clawith_email_send": "send_external_message",
}


async def check_autonomy(agent, bridge_tool: str) -> bool:
    action = _TOOL_AUTONOMY_MAP.get(bridge_tool)
    if not action:
        return True
    level = (agent.autonomy_policy or {}).get(action, "L2")
    return level != "L3"
