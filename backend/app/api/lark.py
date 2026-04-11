"""Lark/Feishu API routes.

Provides OAuth flow and credential management for Lark/Feishu integration.
"""

import uuid
from datetime import datetime, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.permissions import check_agent_access
from app.core.security import encrypt_data, get_current_user, require_role
from app.database import get_db
from app.models.lark_oauth_token import LarkOAuthToken
from app.models.user import User
from app.services import lark_service


def _oauth_popup_response(status: str, message: str = "") -> HTMLResponse:
    return HTMLResponse(f"""<!DOCTYPE html><html><body><script>
    window.opener && window.opener.postMessage({{type:'lark-oauth',status:'{status}',message:{repr(message)}}}, '*');
    window.close();
    </script><p>{'Authorization successful. This window will close automatically.' if status == 'success' else f'Error: {message}'}</p></body></html>""")

router = APIRouter(prefix="/lark", tags=["lark"])

settings = get_settings()


async def _get_lark_redirect_uri(db: AsyncSession) -> str:
    """Resolve Lark OAuth redirect URI.

    If LARK_OAUTH_REDIRECT_URI is explicitly set, use it directly.
    Otherwise, auto-generate from PUBLIC_BASE_URL + LARK_OAUTH_CALLBACK_PATH.
    """
    if settings.LARK_OAUTH_REDIRECT_URI:
        return settings.LARK_OAUTH_REDIRECT_URI
    from app.services.platform_service import platform_service
    base_url = await platform_service.get_public_base_url(db)
    return f"{base_url}{settings.LARK_OAUTH_CALLBACK_PATH}"


LARK_SCOPE_PRESETS = {
    "readonly": {
        "label": "Read Only",
        "description": "View docs, sheets, calendar, drive (no modifications)",
        "scopes": [
            "contact:user.base:readonly",
            "calendar:calendar:readonly",
            "drive:drive:readonly",
            "docx:document:readonly",
            "sheets:spreadsheet:readonly",
            "wiki:wiki:readonly",
            "task:task:readonly",
            "mail:mail:readonly",
        ],
    },
    "standard": {
        "label": "Standard (Read & Write)",
        "description": "Read and write docs, sheets, calendar, drive, IM messages, tasks, and mail",
        "scopes": [
            "contact:user.base:readonly",
            "calendar:calendar",
            "drive:drive",
            "docx:document",
            "sheets:spreadsheet",
            "wiki:wiki",
            "task:task",
            "mail:mail",
            "im:message",
            "im:message:send_as_bot",
        ],
    },
    "full": {
        "label": "Full Access",
        "description": "All standard permissions plus approval, VC, whiteboard, and base",
        "scopes": [
            "contact:user.base:readonly",
            "calendar:calendar",
            "drive:drive",
            "docx:document",
            "sheets:spreadsheet",
            "wiki:wiki",
            "task:task",
            "mail:mail",
            "im:message",
            "im:message:send_as_bot",
            "approval:approval",
            "vc:vc.readonly",
            "whiteboard:whiteboard",
            "bitable:bitable",
            "im:chat",
        ],
    },
}

LARK_AVAILABLE_SCOPES = [
    {"scope": "contact:user.base:readonly", "label": "Contact (Base Read)", "category": "Contact"},
    {"scope": "calendar:calendar:readonly", "label": "Calendar (Read)", "category": "Calendar"},
    {"scope": "calendar:calendar", "label": "Calendar (Full)", "category": "Calendar"},
    {"scope": "drive:drive:readonly", "label": "Drive (Read)", "category": "Drive"},
    {"scope": "drive:drive", "label": "Drive (Full)", "category": "Drive"},
    {"scope": "docx:document:readonly", "label": "Docs (Read)", "category": "Docs"},
    {"scope": "docx:document", "label": "Docs (Full)", "category": "Docs"},
    {"scope": "sheets:spreadsheet:readonly", "label": "Sheets (Read)", "category": "Sheets"},
    {"scope": "sheets:spreadsheet", "label": "Sheets (Full)", "category": "Sheets"},
    {"scope": "wiki:wiki:readonly", "label": "Wiki (Read)", "category": "Wiki"},
    {"scope": "wiki:wiki", "label": "Wiki (Full)", "category": "Wiki"},
    {"scope": "task:task:readonly", "label": "Tasks (Read)", "category": "Tasks"},
    {"scope": "task:task", "label": "Tasks (Full)", "category": "Tasks"},
    {"scope": "mail:mail:readonly", "label": "Mail (Read)", "category": "Mail"},
    {"scope": "mail:mail", "label": "Mail (Full)", "category": "Mail"},
    {"scope": "im:message", "label": "IM (Read Messages)", "category": "IM"},
    {"scope": "im:message:send_as_bot", "label": "IM (Send as Bot)", "category": "IM"},
    {"scope": "im:chat", "label": "IM (Chat Management)", "category": "IM"},
    {"scope": "approval:approval", "label": "Approval", "category": "Approval"},
    {"scope": "vc:vc.readonly", "label": "Video Conference (Read)", "category": "VC"},
    {"scope": "whiteboard:whiteboard", "label": "Whiteboard", "category": "Whiteboard"},
    {"scope": "bitable:bitable", "label": "Base (Bitable)", "category": "Base"},
]

DEFAULT_SCOPE_PRESET = "readonly"


def _resolve_scopes(config: dict) -> list[str]:
    """Resolve OAuth scopes from tenant Lark configuration.

    Reads scope_preset and custom_scopes from the tenant config.
    Falls back to DEFAULT_SCOPE_PRESET if not configured.
    """
    preset = config.get("scope_preset", DEFAULT_SCOPE_PRESET)

    if preset == "custom":
        custom = config.get("custom_scopes", [])
        if not custom:
            return LARK_SCOPE_PRESETS[DEFAULT_SCOPE_PRESET]["scopes"]
        return list(custom)

    if preset in LARK_SCOPE_PRESETS:
        return LARK_SCOPE_PRESETS[preset]["scopes"]

    return LARK_SCOPE_PRESETS[DEFAULT_SCOPE_PRESET]["scopes"]


def _get_brand_domain(brand: str) -> str:
    """Get the Lark/Feishu domain based on brand."""
    if brand == "feishu":
        return "feishu.cn"
    return "larksuite.com"


@router.put("/settings/credentials")
async def store_lark_credentials(
    data: dict,
    current_user: User = Depends(require_role("org_admin", "platform_admin")),
    db: AsyncSession = Depends(get_db),
):
    """Store Lark App credentials and scope configuration.

    Requires org_admin or platform_admin role.
    Body: {app_id, app_secret, brand, scope_preset?, custom_scopes?}
    """
    app_id = (data.get("app_id") or "").strip()
    app_secret = (data.get("app_secret") or "").strip()
    brand = (data.get("brand") or "lark").strip()
    scope_preset = (data.get("scope_preset") or DEFAULT_SCOPE_PRESET).strip()
    custom_scopes = data.get("custom_scopes") or []

    if brand not in ("lark", "feishu"):
        raise HTTPException(status_code=422, detail=f"Invalid brand: {brand}. Must be 'lark' or 'feishu'")

    if scope_preset not in (*LARK_SCOPE_PRESETS, "custom"):
        raise HTTPException(status_code=422, detail=f"Invalid scope_preset: {scope_preset}")

    tenant_id = current_user.tenant_id
    if not tenant_id:
        raise HTTPException(status_code=400, detail="User has no tenant")

    existing_config = await lark_service.get_tenant_lark_config(tenant_id, db)
    if not app_id and not existing_config.get("app_id"):
        raise HTTPException(status_code=422, detail="app_id is required")

    await lark_service.save_tenant_lark_config(
        tenant_id=tenant_id,
        app_id=app_id,
        app_secret=app_secret,
        brand=brand,
        scope_preset=scope_preset,
        custom_scopes=custom_scopes,
        db=db,
    )

    return {"ok": True, "message": "Lark credentials stored"}


@router.get("/settings/credentials")
async def get_lark_credentials(
    current_user: User = Depends(require_role("org_admin", "platform_admin")),
    db: AsyncSession = Depends(get_db),
):
    """Get Lark credential status for the tenant.

    Returns {configured, masked_app_id, has_app_secret, brand, scope_preset, custom_scopes, resolved_scopes}.
    """
    tenant_id = current_user.tenant_id
    if not tenant_id:
        raise HTTPException(status_code=400, detail="User has no tenant")

    config = await lark_service.get_tenant_lark_config(tenant_id, db)

    if not config:
        return {
            "configured": False,
            "masked_app_id": "",
            "has_app_secret": False,
            "brand": "",
            "scope_preset": DEFAULT_SCOPE_PRESET,
            "custom_scopes": [],
            "resolved_scopes": LARK_SCOPE_PRESETS[DEFAULT_SCOPE_PRESET]["scopes"],
        }

    app_id = config.get("app_id", "")
    masked_app_id = ""
    if app_id and len(app_id) > 8:
        masked_app_id = app_id[:4] + "****" + app_id[-4:]
    elif app_id:
        masked_app_id = app_id[:2] + "****"

    scope_preset = config.get("scope_preset", DEFAULT_SCOPE_PRESET)
    custom_scopes = config.get("custom_scopes", [])

    return {
        "configured": True,
        "masked_app_id": masked_app_id,
        "has_app_secret": bool(config.get("app_secret")),
        "brand": config.get("brand", "lark"),
        "scope_preset": scope_preset,
        "custom_scopes": custom_scopes,
        "resolved_scopes": _resolve_scopes(config),
    }


@router.post("/agents/{agent_id}/auth/authorize")
async def get_lark_authorize_url(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate Lark OAuth authorization URL for agent.

    Returns {authorize_url}.
    """
    await check_agent_access(db, current_user, agent_id)

    tenant_id = current_user.tenant_id
    if not tenant_id:
        raise HTTPException(status_code=400, detail="User has no tenant")

    config = await lark_service.get_tenant_lark_config(tenant_id, db)
    if not config or not config.get("app_id"):
        raise HTTPException(
            status_code=400,
            detail="Lark not configured for tenant",
        )

    state = lark_service.generate_oauth_state(
        agent_id=agent_id,
        user_id=current_user.id,
        tenant_id=tenant_id,
    )

    redirect_uri = await _get_lark_redirect_uri(db)
    brand = config.get("brand", "lark")
    domain = _get_brand_domain(brand)

    params = {
        "app_id": config["app_id"],
        "redirect_uri": redirect_uri,
        "state": state,
    }

    authorize_url = f"https://open.{domain}/open-apis/authen/v1/authorize?{urlencode(params)}"

    return {"authorize_url": authorize_url}


@router.get("/auth/callback")
async def handle_lark_oauth_callback(
    code: str,
    state: str,
    db: AsyncSession = Depends(get_db),
):
    """Handle Lark OAuth callback.

    Exchanges code for tokens, stores them encrypted, redirects to frontend.
    """
    try:
        state_data = lark_service.decrypt_oauth_state(state)
    except ValueError as e:
        logger.error(f"Invalid OAuth state: {e}")
        raise HTTPException(status_code=400, detail="Invalid OAuth state")

    agent_id = uuid.UUID(state_data["agent_id"])
    user_id = uuid.UUID(state_data["user_id"])
    tenant_id = uuid.UUID(state_data["tenant_id"])

    redirect_uri = await _get_lark_redirect_uri(db)

    try:
        token_response = await lark_service.exchange_code_for_tokens(
            code=code,
            tenant_id=tenant_id,
            redirect_uri=redirect_uri,
            db=db,
        )
    except Exception as e:
        logger.error(f"Failed to exchange OAuth code: {e}")
        return _oauth_popup_response("error", str(e)[:100])

    if "access_token" not in token_response:
        err_msg = token_response.get("message", token_response.get("msg", "Unknown error"))
        err_code = token_response.get("code", "?")
        logger.error(f"Lark token exchange returned error: code={err_code}, msg={err_msg}")
        return _oauth_popup_response("error", str(err_msg)[:100])

    access_token = token_response["access_token"]
    refresh_token = token_response.get("refresh_token", "")
    expires_in = token_response.get("expires_in", 3600)
    scopes = token_response.get("scope", "").split(" ") if token_response.get("scope") else []
    lark_user_id = token_response.get("open_id", "")

    user_info = await lark_service.fetch_user_info(access_token, tenant_id, db)
    lark_user_name = user_info.get("name", "") or token_response.get("name", "")
    lark_avatar_url = user_info.get("avatar_url", "") or token_response.get("avatar_url", "")

    result = await db.execute(
        select(LarkOAuthToken).where(
            LarkOAuthToken.agent_id == agent_id,
            LarkOAuthToken.user_id == user_id,
        )
    )
    existing_token = result.scalar_one_or_none()

    encrypted_access_token = encrypt_data(access_token, settings.SECRET_KEY)
    encrypted_refresh_token = encrypt_data(refresh_token, settings.SECRET_KEY) if refresh_token else ""
    token_expiry = datetime.now(timezone.utc) + __import__("datetime").timedelta(seconds=expires_in)

    if existing_token:
        existing_token.access_token = encrypted_access_token
        if refresh_token:
            existing_token.refresh_token = encrypted_refresh_token
        existing_token.token_expiry = token_expiry
        existing_token.scopes = scopes
        existing_token.status = "active"
        existing_token.last_used_at = datetime.now(timezone.utc)
        existing_token.lark_user_id = lark_user_id
        if lark_user_name:
            existing_token.lark_user_name = lark_user_name
        if lark_avatar_url:
            existing_token.lark_avatar_url = lark_avatar_url
        db.add(existing_token)
    else:
        new_token = LarkOAuthToken(
            agent_id=agent_id,
            user_id=user_id,
            tenant_id=tenant_id,
            lark_user_id=lark_user_id,
            lark_user_name=lark_user_name or "unknown",
            lark_avatar_url=lark_avatar_url,
            access_token=encrypted_access_token,
            refresh_token=encrypted_refresh_token,
            token_expiry=token_expiry,
            scopes=scopes,
            status="active",
            last_used_at=datetime.now(timezone.utc),
        )
        db.add(new_token)

    await db.commit()

    return _oauth_popup_response("success")


@router.delete("/agents/{agent_id}/auth/revoke")
async def revoke_lark_oauth(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Revoke Lark OAuth token for current user."""
    await check_agent_access(db, current_user, agent_id)

    await lark_service.revoke_oauth_token(
        agent_id=agent_id,
        user_id=current_user.id,
        db=db,
    )

    return {"ok": True, "message": "OAuth token revoked"}


@router.get("/agents/{agent_id}/auth/accounts")
async def list_lark_oauth_accounts(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all authorized Lark accounts for an agent."""
    await check_agent_access(db, current_user, agent_id)

    accounts = await lark_service.list_agent_oauth_accounts(agent_id, db)

    return accounts


@router.get("/settings/scope-options")
async def get_scope_options(
    current_user: User = Depends(require_role("org_admin", "platform_admin")),
):
    """Return available scope presets and individual scopes for the config UI."""
    presets = {
        k: {"label": v["label"], "description": v["description"], "scopes": v["scopes"]}
        for k, v in LARK_SCOPE_PRESETS.items()
    }
    return {
        "presets": presets,
        "available_scopes": LARK_AVAILABLE_SCOPES,
        "default_preset": DEFAULT_SCOPE_PRESET,
    }


@router.post("/skills/import")
async def import_lark_skills_endpoint(
    current_user: User = Depends(require_role("org_admin", "platform_admin")),
):
    """Manually re-import Lark skills from GitHub.

    Requires org_admin or platform_admin role.
    Imports skills scoped to the current user's tenant.
    """
    from app.services.lark_skill_seeder import import_lark_skills
    from pydantic import BaseModel

    class LarkImportResponse(BaseModel):
        ok: bool
        imported: int

    tenant_id = str(current_user.tenant_id) if current_user.tenant_id else None
    imported_count = await import_lark_skills(tenant_id)

    return LarkImportResponse(ok=True, imported=imported_count)
