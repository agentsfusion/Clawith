"""Unified credential resolver for sandbox environments.

Resolves OAuth tokens for (agent_id, user_id) and builds an env dict
that can be injected into any sandbox backend.  Supports Google Workspace
(GWS) and Lark OAuth providers.

Design decisions:
- Each provider resolver is independent — one failure does not block others.
- Tokens are refreshed automatically if within the 5-minute expiry buffer.
- Returns a plain dict[str, str] — backend-agnostic.
"""

import uuid
from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import select

from app.config import get_settings
from app.core.security import decrypt_data
from app.database import async_session

settings = get_settings()


async def resolve_oauth_env(agent_id: uuid.UUID, user_id: uuid.UUID) -> dict[str, str]:
    """Resolve all available OAuth credentials for (agent_id, user_id).

    Returns a dict of environment variable names → access token values.
    Only includes credentials that are active and valid.
    Tokens are auto-refreshed if expired.
    """
    env_vars: dict[str, str] = {}

    gws_token = await _resolve_gws_token(agent_id, user_id)
    if gws_token:
        env_vars["GOOGLE_WORKSPACE_CLI_TOKEN"] = gws_token

    lark_creds = await _resolve_lark_token(agent_id, user_id)
    if lark_creds:
        env_vars.update(lark_creds)

    return env_vars


async def _resolve_gws_token(agent_id: uuid.UUID, user_id: uuid.UUID) -> str | None:
    """Resolve a valid GWS access token for the given agent+user."""
    try:
        from app.models.gws_oauth_token import GwsOAuthToken

        async with async_session() as db:
            result = await db.execute(
                select(GwsOAuthToken).where(
                    GwsOAuthToken.agent_id == agent_id,
                    GwsOAuthToken.user_id == user_id,
                    GwsOAuthToken.status == "active",
                )
            )
            token_record = result.scalar_one_or_none()

        if not token_record:
            return None

        # Check expiry and refresh if needed (5-minute buffer)
        now = datetime.now(timezone.utc)
        needs_refresh = (
            token_record.token_expiry is None
            or token_record.token_expiry <= now + timedelta(minutes=5)
        )

        if needs_refresh:
            if not token_record.tenant_id:
                logger.warning(f"[CredentialResolver] GWS token has no tenant_id, cannot refresh")
                return None
            from app.services.gws_service import refresh_access_token
            logger.info(f"[CredentialResolver] Refreshing GWS token for agent {agent_id}")
            return await refresh_access_token(token_record, token_record.tenant_id)

        return decrypt_data(token_record.access_token, settings.SECRET_KEY)

    except Exception as e:
        logger.warning(f"[CredentialResolver] GWS token resolution failed: {e}")
        return None


async def _resolve_lark_token(agent_id: uuid.UUID, user_id: uuid.UUID) -> dict[str, str] | None:
    """Resolve valid Lark credentials for the given agent+user."""
    try:
        from app.models.lark_oauth_token import LarkOAuthToken

        async with async_session() as db:
            result = await db.execute(
                select(LarkOAuthToken).where(
                    LarkOAuthToken.agent_id == agent_id,
                    LarkOAuthToken.user_id == user_id,
                    LarkOAuthToken.status == "active",
                )
            )
            token_record = result.scalar_one_or_none()

        if not token_record:
            return None

        if not token_record.tenant_id:
            logger.warning(f"[CredentialResolver] Lark token has no tenant_id")
            return None

        # Check expiry and refresh if needed (5-minute buffer)
        now = datetime.now(timezone.utc)
        needs_refresh = (
            token_record.token_expiry is None
            or token_record.token_expiry <= now + timedelta(minutes=5)
        )

        if needs_refresh:
            from app.services.lark_service import refresh_access_token
            logger.info(f"[CredentialResolver] Refreshing Lark token for agent {agent_id}")
            access_token = await refresh_access_token(token_record, token_record.tenant_id)
        else:
            access_token = decrypt_data(token_record.access_token, settings.SECRET_KEY)

        from app.services.lark_service import get_tenant_lark_config
        tenant_config = await get_tenant_lark_config(token_record.tenant_id)

        if not tenant_config.get("app_id"):
            logger.warning(f"[CredentialResolver] Lark tenant config missing app_id")
            return None

        return {
            "LARKSUITE_CLI_USER_ACCESS_TOKEN": access_token,
            "LARKSUITE_CLI_APP_ID": tenant_config.get("app_id", ""),
            "LARKSUITE_CLI_APP_SECRET": tenant_config.get("app_secret", ""),
            "LARKSUITE_CLI_DEFAULT_AS": "user",
        }

    except Exception as e:
        logger.warning(f"[CredentialResolver] Lark token resolution failed: {e}")
        return None
