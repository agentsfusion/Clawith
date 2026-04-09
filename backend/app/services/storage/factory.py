"""Storage backend factory — creates the configured storage singleton."""

from __future__ import annotations

from functools import lru_cache

from loguru import logger

from app.config import get_settings
from app.services.storage.cache import CachedStorageBackend
from app.services.storage.interface import StorageBackend
from app.services.storage.local import LocalStorageBackend


def _create_storage() -> StorageBackend:
    """Instantiate the storage backend based on configuration."""
    settings = get_settings()
    backend_type = settings.STORAGE_BACKEND.lower().strip()

    if backend_type == "local":
        backend: StorageBackend = LocalStorageBackend(root_dir=settings.AGENT_DATA_DIR)
        logger.info(f"Storage backend: local ({settings.AGENT_DATA_DIR})")
    elif backend_type == "s3":
        from app.services.storage.s3 import S3StorageBackend

        endpoint = settings.STORAGE_ENDPOINT_URL or None
        force_path_style = not (endpoint and "amazonaws.com" in endpoint)

        backend = S3StorageBackend(
            bucket=settings.STORAGE_BUCKET,
            endpoint_url=settings.STORAGE_ENDPOINT_URL,
            region=settings.STORAGE_REGION,
            access_key=settings.STORAGE_ACCESS_KEY,
            secret_key=settings.STORAGE_SECRET_KEY,
            force_path_style=force_path_style,
        )
        logger.info(f"Storage backend: s3 (bucket={settings.STORAGE_BUCKET}, endpoint={endpoint or 'AWS default'})")
    else:
        raise ValueError(
            f"Unknown STORAGE_BACKEND={backend_type!r}. "
            f"Supported: 'local', 's3'"
        )

    if settings.STORAGE_CACHE_DIR:
        backend = CachedStorageBackend(
            backend=backend,
            cache_dir=settings.STORAGE_CACHE_DIR,
            ttl_seconds=settings.STORAGE_CACHE_TTL_SECONDS,
        )
        logger.info(f"Storage cache: enabled ({settings.STORAGE_CACHE_DIR}, TTL={settings.STORAGE_CACHE_TTL_SECONDS}s)")
    else:
        logger.info("Storage cache: disabled")

    return backend


@lru_cache
def get_storage() -> StorageBackend:
    """Get the cached storage backend singleton."""
    return _create_storage()
