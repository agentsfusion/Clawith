"""Database-backed seeder state tracking.

Stores per-seeder completion markers in the `system_settings` table so that
seeding logic survives container restarts, volume changes, and environment
migrations — as long as the database persists.

Usage in seeders::

    from app.services.seeder_state import is_seeder_done, mark_seeder_done

    async def seed_foo():
        if await is_seeder_done("seeder:foo", version=1):
            return
        # ... do seeding work ...
        await mark_seeder_done("seeder:foo", version=1)
"""

from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select

from app.database import async_session
from app.models.system_settings import SystemSetting


async def is_seeder_done(key: str, expected_version: int) -> bool:
    """Check whether a seeder has already completed for the given version.

    Args:
        key: The system_settings key (e.g. ``"seeder:tools"``).
        expected_version: The version the seeder expects. If the stored version
            is lower (or the key does not exist), the seeder should re-run.

    Returns:
        ``True`` if the seeder has completed at *or above* the expected version.
    """
    try:
        async with async_session() as db:
            result = await db.execute(
                select(SystemSetting).where(SystemSetting.key == key)
            )
            setting = result.scalar_one_or_none()
            if setting is None:
                return False
            stored_version = (setting.value or {}).get("version", 0)
            if stored_version >= expected_version:
                logger.debug(f"[SeederState] {key} already done (v{stored_version} >= v{expected_version})")
                return True
            return False
    except Exception as e:
        # If the check itself fails (e.g., table doesn't exist yet), allow
        # the seeder to run — the seeder's own DB-level dedup will protect.
        logger.warning(f"[SeederState] Could not check {key}: {e}")
        return False


async def get_seeder_state(key: str) -> dict | None:
    """Retrieve the full stored state dict for a seeder, or None."""
    try:
        async with async_session() as db:
            result = await db.execute(
                select(SystemSetting).where(SystemSetting.key == key)
            )
            setting = result.scalar_one_or_none()
            return setting.value if setting else None
    except Exception:
        return None


async def mark_seeder_done(key: str, version: int, extra: dict | None = None) -> None:
    """Record that a seeder has completed successfully.

    Creates or updates a ``system_settings`` row with the given key, storing
    the version, completion timestamp, and any extra metadata.

    Args:
        key: The system_settings key (e.g. ``"seeder:tools"``).
        version: The seeder version that just completed.
        extra: Optional dict merged into the stored value (e.g. ``{"count": 50}``).
    """
    value: dict = {
        "version": version,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    if extra:
        value.update(extra)

    try:
        async with async_session() as db:
            result = await db.execute(
                select(SystemSetting).where(SystemSetting.key == key)
            )
            setting = result.scalar_one_or_none()
            if setting:
                setting.value = value
            else:
                db.add(SystemSetting(key=key, value=value))
            await db.commit()
        logger.info(f"[SeederState] Marked {key} done (v{version})")
    except Exception as e:
        logger.warning(f"[SeederState] Failed to mark {key} done: {e}")


async def all_seeders_done(keys_and_versions: list[tuple[str, int]]) -> bool:
    """Check whether *all* given seeders have completed.

    Useful for ``seed.py`` to quickly determine if anything needs to run.

    Args:
        keys_and_versions: List of ``(key, expected_version)`` tuples.

    Returns:
        ``True`` only if every seeder is done at or above its expected version.
    """
    for key, version in keys_and_versions:
        if not await is_seeder_done(key, version):
            return False
    return True
