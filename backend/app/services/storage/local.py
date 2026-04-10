"""Local filesystem storage backend."""

from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import aiofiles

from .interface import (
    FileInfo,
    FileNotFoundError,
    StorageError,
    StoragePermissionError,
)


class LocalStorageBackend:
    """Storage backend that uses the local filesystem.

    Maps storage keys to filesystem paths under a root directory.
    All methods are async and use aiofiles for file I/O.
    """

    backend_name = "local"

    def __init__(self, root_dir: str) -> None:
        self._root = Path(root_dir).resolve()

    def _resolve(self, key: str) -> Path:
        """Resolve a storage key to an absolute filesystem path.

        Validates that the resolved path stays within the root directory
        to prevent path traversal attacks.
        """
        normalized = key.lstrip("/")
        path = (self._root / normalized).resolve()
        if not str(path).startswith(str(self._root)):
            raise StoragePermissionError(key=key, backend_name=self.backend_name)
        return path

    # ── Read ──────────────────────────────────────────────────────────

    async def read(self, key: str) -> str:
        """Read a text file and return its content as a UTF-8 string."""
        path = self._resolve(key)
        if not path.exists():
            raise FileNotFoundError(key=key, backend_name=self.backend_name)
        async with aiofiles.open(path, "r", encoding="utf-8") as f:
            return await f.read()

    async def read_bytes(self, key: str) -> bytes:
        """Read a binary file and return raw bytes."""
        path = self._resolve(key)
        if not path.exists():
            raise FileNotFoundError(key=key, backend_name=self.backend_name)
        async with aiofiles.open(path, "rb") as f:
            return await f.read()

    # ── Write ─────────────────────────────────────────────────────────

    async def write(self, key: str, content: str) -> None:
        """Write text content to a file (create or overwrite)."""
        path = self._resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(path, "w", encoding="utf-8") as f:
            await f.write(content)

    async def write_bytes(self, key: str, content: bytes) -> None:
        """Write binary content to a file (create or overwrite)."""
        path = self._resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(path, "wb") as f:
            await f.write(content)

    # ── Delete ────────────────────────────────────────────────────────

    async def delete(self, key: str) -> None:
        """Delete a single file. No-op if the file does not exist."""
        path = self._resolve(key)
        path.unlink(missing_ok=True)

    async def delete_prefix(self, prefix: str) -> None:
        """Recursively delete all files under a prefix (directory)."""
        normalized = prefix.rstrip("/").lstrip("/")
        target = (self._root / normalized).resolve() if normalized else self._root
        if not str(target).startswith(str(self._root)):
            raise StoragePermissionError(key=prefix, backend_name=self.backend_name)
        if target.is_dir():
            shutil.rmtree(target)

    # ── Exists ────────────────────────────────────────────────────────

    async def exists(self, key: str) -> bool:
        """Check if a file exists."""
        path = self._resolve(key)
        return path.exists()

    # ── List ──────────────────────────────────────────────────────────

    async def list(self, prefix: str) -> list[FileInfo]:
        """List files and directories directly under a prefix (one level)."""
        normalized = prefix.lstrip("/")
        if not normalized:
            target = self._root
        else:
            target = self._resolve(prefix)

        if not target.is_dir():
            return []

        entries: list[FileInfo] = []
        for entry in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name)):
            if entry.name == ".gitkeep":
                continue
            rel = str(entry.relative_to(self._root))
            is_dir = entry.is_dir()
            size = 0 if is_dir else entry.stat().st_size
            mtime = os.path.getmtime(entry)
            modified_at = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
            entries.append(
                FileInfo(
                    name=entry.name,
                    path=rel,
                    is_dir=is_dir,
                    size=size,
                    modified_at=modified_at,
                )
            )
        return entries

    # ── Copy / Move ──────────────────────────────────────────────────

    async def copy(self, src: str, dst: str) -> None:
        """Copy a file from one key to another."""
        src_path = self._resolve(src)
        dst_path = self._resolve(dst)
        if not src_path.exists():
            raise FileNotFoundError(key=src, backend_name=self.backend_name)
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        content = await self.read_bytes(src)
        await self.write_bytes(dst, content)

    async def move(self, src: str, dst: str) -> None:
        """Move a file from one key to another."""
        src_path = self._resolve(src)
        dst_path = self._resolve(dst)
        if not src_path.exists():
            raise FileNotFoundError(key=src, backend_name=self.backend_name)
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src_path), str(dst_path))

    # ── Presigned URL ─────────────────────────────────────────────────

    async def get_presigned_url(self, key: str, expires_in: int = 3600) -> str:
        """Not supported by the local filesystem backend."""
        raise StorageError(
            "Presigned URLs not supported by local backend",
            key=key,
            backend_name=self.backend_name,
        )

    # ── Health ────────────────────────────────────────────────────────

    async def health_check(self) -> bool:
        """Return True if root_dir exists, False otherwise (never raises)."""
        return self._root.exists()
