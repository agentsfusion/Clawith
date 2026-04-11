"""Lark/Feishu service layer — OAuth token management and API helpers."""

import json
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.security import decrypt_data, encrypt_data
from app.database import async_session
from app.models.lark_oauth_token import LarkOAuthToken
from app.models.tenant_setting import TenantSetting

settings = get_settings()


async def get_tenant_lark_config(tenant_id: uuid.UUID, db: AsyncSession | None = None) -> dict:
    """Read Lark configuration from TenantSetting.

    Returns dict with app_id, app_secret (decrypted), brand, scope_preset, custom_scopes.
    Returns empty dict if not configured.
    """
    async def _fetch(session: AsyncSession) -> dict:
        result = await session.execute(
            select(TenantSetting).where(
                TenantSetting.tenant_id == tenant_id,
                TenantSetting.key == "lark",
            )
        )
        setting = result.scalar_one_or_none()
        if not setting or not setting.value:
            return {}

        config = setting.value.copy()

        if config.get("app_secret"):
            try:
                config["app_secret"] = decrypt_data(config["app_secret"], settings.SECRET_KEY)
            except Exception as e:
                logger.error(f"Failed to decrypt Lark app_secret: {e}")
                config["app_secret"] = ""

        return config

    if db is not None:
        return await _fetch(db)
    async with async_session() as session:
        return await _fetch(session)


async def save_tenant_lark_config(
    tenant_id: uuid.UUID,
    app_id: str,
    app_secret: str,
    brand: str,
    scope_preset: str = "standard",
    custom_scopes: list[str] | None = None,
    db: AsyncSession | None = None,
) -> None:
    """Encrypt and store Lark configuration in TenantSetting."""
    async def _save(session: AsyncSession) -> None:
        encrypted_secret = encrypt_data(app_secret, settings.SECRET_KEY) if app_secret else ""

        result = await session.execute(
            select(TenantSetting).where(
                TenantSetting.tenant_id == tenant_id,
                TenantSetting.key == "lark",
            )
        )
        setting = result.scalar_one_or_none()

        if setting and setting.value:
            value = setting.value.copy()
            if app_id:
                value["app_id"] = app_id
            if encrypted_secret:
                value["app_secret"] = encrypted_secret
            if brand:
                value["brand"] = brand
            value["scope_preset"] = scope_preset
            value["custom_scopes"] = custom_scopes or []
        else:
            value = {
                "app_id": app_id,
                "app_secret": encrypted_secret,
                "brand": brand,
                "scope_preset": scope_preset,
                "custom_scopes": custom_scopes or [],
            }

        if setting:
            setting.value = value
        else:
            setting = TenantSetting(
                tenant_id=tenant_id,
                key="lark",
                value=value,
            )
            session.add(setting)

        await session.commit()

    if db is not None:
        return await _save(db)
    async with async_session() as session:
        return await _save(session)


def generate_oauth_state(agent_id: uuid.UUID, user_id: uuid.UUID, tenant_id: uuid.UUID) -> str:
    """Create encrypted OAuth state parameter.

    Returns encrypted JSON containing agent_id, user_id, tenant_id, timestamp.
    """
    state_data = json.dumps({
        "agent_id": str(agent_id),
        "user_id": str(user_id),
        "tenant_id": str(tenant_id),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    return encrypt_data(state_data, settings.SECRET_KEY)


def decrypt_oauth_state(state: str) -> dict:
    """Decrypt OAuth state parameter.

    Returns dict with agent_id, user_id, tenant_id.
    Raises ValueError if decryption or parsing fails.
    """
    try:
        decrypted = decrypt_data(state, settings.SECRET_KEY)
        return json.loads(decrypted)
    except Exception as e:
        raise ValueError(f"Invalid OAuth state: {e}") from e


async def resolve_lark_base_url(tenant_id: uuid.UUID, db: AsyncSession | None = None) -> str:
    """Resolve brand-specific Lark API base URL from tenant config.

    Returns https://open.larksuite.com for brand="lark",
    https://open.feishu.cn for brand="feishu".
    Defaults to "lark" if not configured.
    """
    config = await get_tenant_lark_config(tenant_id, db)
    brand = config.get("brand", "lark")
    if brand == "feishu":
        return "https://open.feishu.cn"
    return "https://open.larksuite.com"


async def _get_app_access_token(
    app_id: str, app_secret: str, base_url: str,
) -> str:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{base_url}/open-apis/auth/v3/app_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
        )
        resp.raise_for_status()
        body = resp.json()
        if body.get("code", -1) != 0:
            raise ValueError(f"Failed to get app_access_token: {body.get('msg', body)}")
        return body["app_access_token"]


async def exchange_code_for_tokens(
    code: str,
    tenant_id: uuid.UUID,
    redirect_uri: str,
    db: AsyncSession | None = None,
) -> dict:
    """Exchange OAuth authorization code for access and refresh tokens.

    1. Obtain an app_access_token via internal endpoint.
    2. POST to OIDC access_token endpoint with Bearer header.
    Returns token response data dict with access_token, refresh_token, open_id, etc.
    """
    config = await get_tenant_lark_config(tenant_id, db)

    if not config.get("app_id") or not config.get("app_secret"):
        raise ValueError("Lark not configured for tenant")

    base_url = await resolve_lark_base_url(tenant_id, db)
    app_token = await _get_app_access_token(config["app_id"], config["app_secret"], base_url)

    token_url = f"{base_url}/open-apis/authen/v1/oidc/access_token"
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            token_url,
            json=data,
            headers={"Authorization": f"Bearer {app_token}"},
        )
        response.raise_for_status()
        body = response.json()
        if body.get("code", -1) != 0:
            raise ValueError(f"Lark token exchange failed ({body.get('code')}): {body.get('message', body.get('msg', ''))}")
        return body.get("data", body)


async def refresh_access_token(
    token_record: LarkOAuthToken,
    tenant_id: uuid.UUID,
    db: AsyncSession | None = None,
) -> str:
    """Refresh expired access token using refresh_token.

    POST to brand-specific OIDC refresh_access_token endpoint.
    Lark returns both a new access_token AND a new refresh_token.
    Updates DB record with new tokens and token_expiry.
    Returns new plaintext access_token.
    """
    config = await get_tenant_lark_config(tenant_id, db)

    if not config.get("app_id") or not config.get("app_secret"):
        raise ValueError("Lark not configured for tenant")

    refresh_token = decrypt_data(token_record.refresh_token, settings.SECRET_KEY)

    base_url = await resolve_lark_base_url(tenant_id, db)
    app_token = await _get_app_access_token(config["app_id"], config["app_secret"], base_url)

    token_url = f"{base_url}/open-apis/authen/v1/oidc/refresh_access_token"
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            token_url,
            json=data,
            headers={"Authorization": f"Bearer {app_token}"},
        )
        response.raise_for_status()
        body = response.json()
        if body.get("code", -1) != 0:
            raise ValueError(f"Lark token refresh failed ({body.get('code')}): {body.get('message', body.get('msg', ''))}")
        token_data = body.get("data", body)

    new_access_token = token_data["access_token"]
    new_refresh_token = token_data.get("refresh_token", refresh_token)
    expires_in = token_data.get("expires_in", 3600)

    token_record.access_token = encrypt_data(new_access_token, settings.SECRET_KEY)
    token_record.refresh_token = encrypt_data(new_refresh_token, settings.SECRET_KEY)
    token_record.token_expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    token_record.status = "active"
    token_record.last_used_at = datetime.now(timezone.utc)

    async def _update(session: AsyncSession):
        session.add(token_record)
        await session.commit()
        await session.refresh(token_record)

    if db is not None:
        await _update(db)
    else:
        async with async_session() as session:
            await _update(session)

    return new_access_token


async def revoke_oauth_token(
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
    db: AsyncSession | None = None,
) -> None:
    """Revoke OAuth token by marking it as revoked in DB.

    Lark does not provide a standard token revocation endpoint,
    so we simply mark the token status as "revoked".
    """
    async def _revoke(session: AsyncSession) -> None:
        result = await session.execute(
            select(LarkOAuthToken).where(
                LarkOAuthToken.agent_id == agent_id,
                LarkOAuthToken.user_id == user_id,
            )
        )
        token_record = result.scalar_one_or_none()

        if not token_record:
            return  # No token to revoke

        token_record.status = "revoked"
        session.add(token_record)
        await session.commit()

    if db is not None:
        return await _revoke(db)
    async with async_session() as session:
        return await _revoke(session)


async def list_agent_oauth_accounts(
    agent_id: uuid.UUID,
    db: AsyncSession | None = None,
) -> list[dict]:
    """List all OAuth accounts for an agent (never exposes raw tokens).

    Returns list of dicts with lark_user_name, lark_user_id, lark_avatar_url,
    status, scopes, authorized_at, last_used_at.
    """
    async def _list(session: AsyncSession) -> list[dict]:
        result = await session.execute(
            select(LarkOAuthToken)
            .where(LarkOAuthToken.agent_id == agent_id)
            .order_by(LarkOAuthToken.created_at.desc())
        )
        tokens = result.scalars().all()

        return [
            {
                "lark_user_name": token.lark_user_name,
                "lark_user_id": token.lark_user_id,
                "lark_avatar_url": token.lark_avatar_url,
                "status": token.status,
                "scopes": token.scopes or [],
                "authorized_at": token.created_at.isoformat() if token.created_at else None,
                "last_used_at": token.last_used_at.isoformat() if token.last_used_at else None,
            }
            for token in tokens
        ]

    if db is not None:
        return await _list(db)
    async with async_session() as session:
        return await _list(session)


async def get_user_token_for_agent(
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
    db: AsyncSession | None = None,
) -> str:
    """Get plaintext access_token for (agent_id, user_id) pair.

    - Looks up LarkOAuthToken
    - Decrypts access_token
    - Refreshes if expired (within 5-min buffer)
    - Returns plaintext access_token

    Raises ValueError if no token found or token is revoked.
    """
    async def _fetch(session: AsyncSession) -> str:
        result = await session.execute(
            select(LarkOAuthToken).where(
                LarkOAuthToken.agent_id == agent_id,
                LarkOAuthToken.user_id == user_id,
            )
        )
        token_record = result.scalar_one_or_none()

        if not token_record:
            raise ValueError("No OAuth token found for this agent and user")

        if token_record.status == "revoked":
            raise ValueError("OAuth token has been revoked")

        now = datetime.now(timezone.utc)
        if token_record.token_expiry and token_record.token_expiry <= now + timedelta(minutes=5):
            if not token_record.tenant_id:
                raise ValueError("Cannot refresh token: tenant_id is missing")
            logger.info(f"Refreshing expired Lark token for agent {agent_id}, user {user_id}")
            return await refresh_access_token(token_record, token_record.tenant_id, session)

        plaintext_token = decrypt_data(token_record.access_token, settings.SECRET_KEY)

        token_record.last_used_at = now
        session.add(token_record)
        await session.commit()

        return plaintext_token

    if db is not None:
        return await _fetch(db)
    async with async_session() as session:
        return await _fetch(session)
