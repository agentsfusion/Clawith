"""Unified credential resolver for sandbox environments.

Resolves OAuth tokens for (agent_id, user_id) and builds an env dict
that can be injected into any sandbox backend.  Supports Google Workspace
(GWS) and Lark OAuth providers.

Design decisions:
- Each provider resolver is independent — one failure does not block others.
- Tokens are refreshed automatically if within the 5-minute expiry buffer.
- Returns a plain dict[str, str] — backend-agnostic.
- Errors are collected and returned alongside env vars for transparency.
"""

import uuid
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field

from loguru import logger
from sqlalchemy import select

from app.config import get_settings
from app.core.security import decrypt_data
from app.database import async_session

settings = get_settings()


@dataclass
class CredentialResult:
    env_vars: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


async def resolve_oauth_env(agent_id: uuid.UUID, user_id: uuid.UUID) -> CredentialResult:
    """Resolve all available OAuth credentials for (agent_id, user_id).

    Returns a CredentialResult containing:
    - env_vars: dict of environment variable names → access token values
    - errors: list of human-readable error messages for failed providers
    """
    result = CredentialResult()

    gws_token, gws_err = await _resolve_gws_token(agent_id, user_id)
    if gws_token:
        result.env_vars["GOOGLE_WORKSPACE_CLI_TOKEN"] = gws_token
    if gws_err:
        result.errors.append(gws_err)

    lark_creds, lark_err = await _resolve_lark_token(agent_id, user_id)
    if lark_creds:
        result.env_vars.update(lark_creds)
    if lark_err:
        result.errors.append(lark_err)

    return result


async def _resolve_gws_token(
    agent_id: uuid.UUID, user_id: uuid.UUID
) -> tuple[str | None, str | None]:
    """Resolve a valid GWS access token for the given agent+user.

    Returns (access_token, error_message).
    """
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
                return None, None

            now = datetime.now(timezone.utc)
            needs_refresh = (
                token_record.token_expiry is None
                or token_record.token_expiry <= now + timedelta(minutes=5)
            )

            if needs_refresh:
                if not token_record.tenant_id:
                    msg = "GWS token has no tenant_id, cannot refresh"
                    logger.warning(f"[CredentialResolver] {msg}")
                    return None, msg
                from app.services.gws_service import refresh_access_token

                logger.info(f"[CredentialResolver] Refreshing GWS token for agent {agent_id}")
                try:
                    new_token = await refresh_access_token(token_record, token_record.tenant_id, db)
                    return new_token, None
                except Exception as refresh_err:
                    msg = f"GWS token refresh failed: {refresh_err}"
                    logger.error(f"[CredentialResolver] {msg}")
                    return None, msg

            return decrypt_data(token_record.access_token, settings.SECRET_KEY), None

    except Exception as e:
        msg = f"GWS token resolution failed: {e}"
        logger.error(f"[CredentialResolver] {msg}")
        return None, msg


async def _resolve_lark_token(
    agent_id: uuid.UUID, user_id: uuid.UUID
) -> tuple[dict[str, str] | None, str | None]:
    """Resolve valid Lark credentials for the given agent+user.

    Returns (env_dict, error_message).
    """
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
                return None, None

            if not token_record.tenant_id:
                msg = "Lark token has no tenant_id"
                logger.warning(f"[CredentialResolver] {msg}")
                return None, msg

            now = datetime.now(timezone.utc)
            needs_refresh = (
                token_record.token_expiry is None
                or token_record.token_expiry <= now + timedelta(minutes=5)
            )

            if needs_refresh:
                from app.services.lark_service import refresh_access_token

                logger.info(f"[CredentialResolver] Refreshing Lark token for agent {agent_id}")
                try:
                    access_token = await refresh_access_token(
                        token_record, token_record.tenant_id, db
                    )
                except Exception as refresh_err:
                    msg = f"Lark token refresh failed: {refresh_err}"
                    logger.error(f"[CredentialResolver] {msg}")
                    return None, msg
            else:
                access_token = decrypt_data(token_record.access_token, settings.SECRET_KEY)

        from app.services.lark_service import get_tenant_lark_config
        tenant_config = await get_tenant_lark_config(token_record.tenant_id)

        if not tenant_config.get("app_id"):
            msg = "Lark tenant config missing app_id"
            logger.warning(f"[CredentialResolver] {msg}")
            return None, msg

        return {
            "LARKSUITE_CLI_USER_ACCESS_TOKEN": access_token,
            "LARKSUITE_CLI_APP_ID": tenant_config.get("app_id", ""),
            "LARKSUITE_CLI_APP_SECRET": tenant_config.get("app_secret", ""),
            "LARKSUITE_CLI_DEFAULT_AS": "user",
        }, None

    except Exception as e:
        msg = f"Lark token resolution failed: {e}"
        logger.error(f"[CredentialResolver] {msg}")
        return None, msg
