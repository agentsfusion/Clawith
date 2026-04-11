"""Lark CLI tool executor.

Executes lark-cli commands with automatic OAuth credential injection.
"""

import asyncio
import os
import shutil
import uuid

from loguru import logger
from sqlalchemy import select

from app.config import get_settings
from app.core.security import decrypt_data
from app.database import async_session
from app.models.lark_oauth_token import LarkOAuthToken
from app.services.lark_service import refresh_access_token, get_tenant_lark_config

settings = get_settings()


async def execute_lark_command(
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
    command: str,
    timeout: int = 60,
) -> dict:
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
        return {
            "error": "Lark not authorized. Ask the user to connect their Lark account in agent settings."
        }

    if not token_record.tenant_id:
        return {"error": "Lark token has no tenant association. Please re-authorize."}

    access_token = await _get_valid_access_token(token_record)

    tenant_config = await get_tenant_lark_config(token_record.tenant_id)  # type: ignore[arg-type]
    if not tenant_config.get("app_id"):
        return {"error": "Lark not configured for tenant. Ask the admin to configure App credentials."}
    app_id = tenant_config["app_id"]
    app_secret = tenant_config.get("app_secret", "")

    lark_cli_path = await _ensure_lark_installed()
    if not lark_cli_path:
        return {
            "error": "Lark CLI is not available. Please contact your administrator to install @larksuite/cli, or wait a moment while the system attempts automatic installation."
        }

    full_command = f"{lark_cli_path} {command}"

    safe_env = dict(os.environ)
    safe_env["LARKSUITE_CLI_USER_ACCESS_TOKEN"] = access_token
    safe_env["LARKSUITE_CLI_APP_ID"] = app_id
    safe_env["LARKSUITE_CLI_APP_SECRET"] = app_secret
    safe_env["LARKSUITE_CLI_DEFAULT_AS"] = "user"

    try:
        proc = await asyncio.create_subprocess_shell(
            full_command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=safe_env,
        )

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return {"error": f"Command timed out after {timeout}s"}

        stdout_str = stdout.decode("utf-8", errors="replace")
        stderr_str = stderr.decode("utf-8", errors="replace")

        if proc.returncode != 0:
            return {
                "output": stdout_str[:10000],
                "error": stderr_str[:5000] or f"Exit code: {proc.returncode}",
                "exit_code": proc.returncode,
            }

        return {"output": stdout_str[:10000]}

    except Exception as e:
        logger.exception(f"[Lark] Execution failed: {e}")
        return {"error": f"Execution error: {str(e)[:200]}"}


async def _get_valid_access_token(token_record: LarkOAuthToken) -> str:
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    needs_refresh = (
        token_record.token_expiry is None
        or token_record.token_expiry <= now + timedelta(minutes=5)
    )

    if needs_refresh:
        if not token_record.tenant_id:
            raise ValueError("Cannot refresh token: tenant_id is missing")

        logger.info(f"[Lark] Refreshing expired token for agent {token_record.agent_id}")
        return await refresh_access_token(token_record, token_record.tenant_id)

    return decrypt_data(token_record.access_token, settings.SECRET_KEY)


def _find_lark_cli() -> str | None:
    lark = shutil.which("lark-cli")
    if lark:
        return lark

    common_paths = [
        "/usr/local/bin/lark-cli",
        "/usr/bin/lark-cli",
        os.path.expanduser("~/.npm-global/bin/lark-cli"),
        os.path.expanduser("~/node_modules/.bin/lark-cli"),
    ]

    for path in common_paths:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path

    return None


async def _ensure_lark_installed() -> str | None:
    lark_path = _find_lark_cli()
    if lark_path:
        return lark_path

    logger.info("[Lark] lark-cli not found, attempting on-demand installation...")

    try:
        proc = await asyncio.create_subprocess_exec(
            "npm", "install", "-g", "@larksuite/cli",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)

        if proc.returncode == 0:
            logger.info("[Lark] Successfully installed @larksuite/cli")
            return _find_lark_cli()
        else:
            stderr_str = stderr.decode('utf-8', errors='replace') if stderr else ''
            logger.error(f"[Lark] Installation failed with code {proc.returncode}: {stderr_str[:500]}")
            return None
    except asyncio.TimeoutError:
        logger.error("[Lark] Installation timed out after 120s")
        return None
    except Exception as e:
        logger.error(f"[Lark] Failed to install lark-cli: {e}")
        return None
