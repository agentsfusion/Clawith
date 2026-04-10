"""Storage backend interface — protocol, data models, and exceptions.

This module defines the contract that all storage backends must satisfy.
It uses Python's typing.Protocol for structural subtyping, allowing
third-party implementations without inheritance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


# ─── Data Models ──────────────────────────────────────────────────────


@dataclass
class FileInfo:
    """Metadata about a file or directory in storage."""

    name: str
    path: str
    is_dir: bool
    size: int = 0
    modified_at: str = ""


# ─── Exceptions ───────────────────────────────────────────────────────


class StorageError(Exception):
    """Base exception for all storage operations."""

    def __init__(self, message: str, key: str = "", backend_name: str = ""):
        self.key = key
        self.backend_name = backend_name
        prefix = f"[{backend_name}] " if backend_name else ""
        suffix = f" (key={key!r})" if key else ""
        super().__init__(f"{prefix}{message}{suffix}")


class FileNotFoundError(StorageError):
    """Raised when the requested key does not exist in storage."""

    def __init__(self, key: str, backend_name: str = ""):
        super().__init__("File not found", key=key, backend_name=backend_name)


class StorageConnectionError(StorageError):
    """Raised when the storage backend is unreachable."""

    def __init__(self, message: str = "Storage connection failed", key: str = "", backend_name: str = ""):
        super().__init__(message, key=key, backend_name=backend_name)


class StoragePermissionError(StorageError):
    """Raised when access to the key is denied."""

    def __init__(self, key: str = "", backend_name: str = ""):
        super().__init__("Permission denied", key=key, backend_name=backend_name)


# ─── Protocol ─────────────────────────────────────────────────────────


@runtime_checkable
class StorageBackend(Protocol):
    """Async storage backend protocol.

    All methods are async. Keys use '/' as path separator.
    Errors raise StorageError subclasses.
    """

    backend_name: str

    async def read(self, key: str) -> str:
        """Read a text file and return its content as a UTF-8 string."""
        ...

    async def read_bytes(self, key: str) -> bytes:
        """Read a binary file and return raw bytes."""
        ...

    async def write(self, key: str, content: str) -> None:
        """Write text content to a file (create or overwrite).

        Parent directories/prefixes are implicitly created.
        """
        ...

    async def write_bytes(self, key: str, content: bytes) -> None:
        """Write binary content to a file (create or overwrite).

        Parent directories/prefixes are implicitly created.
        """
        ...

    async def delete(self, key: str) -> None:
        """Delete a single file. No-op if the file does not exist."""
        ...

    async def delete_prefix(self, prefix: str) -> None:
        """Recursively delete all files under a prefix (directory)."""
        ...

    async def exists(self, key: str) -> bool:
        """Check if a file exists."""
        ...

    async def list(self, prefix: str) -> list[FileInfo]:
        """List files and directories directly under a prefix.

        Returns one level of entries (non-recursive).
        """
        ...

    async def copy(self, src: str, dst: str) -> None:
        """Copy a file from one key to another."""
        ...

    async def move(self, src: str, dst: str) -> None:
        """Move a file from one key to another."""
        ...

    async def get_presigned_url(self, key: str, expires_in: int = 3600) -> str:
        """Generate a presigned URL for temporary file access.

        Raises StorageError if the backend does not support presigned URLs.
        """
        ...

    async def health_check(self) -> bool:
        """Verify the storage backend is reachable and operational.

        Returns True if healthy, False otherwise (does not raise).
        """
        ...
