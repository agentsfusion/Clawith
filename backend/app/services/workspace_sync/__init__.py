"""Workspace file sync — automatic upload of execute_code output to cloud storage."""

from functools import lru_cache

from app.config import get_settings


@lru_cache
def get_sync_manager():
    from app.services.storage.factory import get_storage
    from app.services.workspace_sync.manager import WorkspaceSyncManager

    settings = get_settings()
    if settings.STORAGE_BACKEND.lower() == "local":
        return None

    return WorkspaceSyncManager(
        storage=get_storage(),
        idle_timeout=settings.WORKSPACE_SYNC_IDLE_TIMEOUT,
        debounce_ms=settings.WORKSPACE_SYNC_DEBOUNCE_MS,
        max_watchers=settings.WORKSPACE_SYNC_MAX_WATCHERS,
        sync_ttl_seconds=settings.STORAGE_CACHE_TTL_SECONDS,
    )
