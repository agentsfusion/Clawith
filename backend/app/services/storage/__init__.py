"""Cloud storage abstraction layer for Clawith agent data.

Provides a unified async interface for reading/writing agent workspace files
across multiple storage backends (local filesystem, S3-compatible object storage).

Usage:
    from app.services.storage import get_storage, StorageBackend

    storage = get_storage()
    content = await storage.read(f"{agent_id}/soul.md")
    await storage.write(f"{agent_id}/memory/memory.md", "new content")
"""

from app.services.storage.interface import (
    FileInfo,
    FileNotFoundError,
    StorageBackend,
    StorageConnectionError,
    StorageError,
    StoragePermissionError,
)

__all__ = [
    "FileInfo",
    "FileNotFoundError",
    "StorageBackend",
    "StorageConnectionError",
    "StorageError",
    "StoragePermissionError",
]
