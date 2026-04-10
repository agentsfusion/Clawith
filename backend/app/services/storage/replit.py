"""Replit Object Storage backend.

Uses Replit's built-in App Storage service (backed by GCS) via the
``replit-object-storage`` SDK.  Does not support presigned URLs — the
download endpoint falls back to streaming bytes through the backend.
"""

from __future__ import annotations

import asyncio
from functools import partial

from replit.object_storage import Client
from replit.object_storage.errors import (
    BucketNotFoundError,
    DefaultBucketError,
    ForbiddenError,
    ObjectNotFoundError,
    TooManyRequestsError,
    UnauthorizedError,
)

from .interface import (
    FileInfo,
    FileNotFoundError,
    StorageConnectionError,
    StorageError,
    StoragePermissionError,
)


class ReplitStorageBackend:
    """Async storage backend backed by Replit Object Storage.

    The underlying SDK is synchronous, so every call is dispatched to
    a thread-pool executor via ``asyncio.get_event_loop().run_in_executor``.
    """

    backend_name = "replit"

    def __init__(self) -> None:
        self._client = Client()

    def _map_error(self, err: Exception, key: str) -> StorageError:
        if isinstance(err, ObjectNotFoundError):
            return FileNotFoundError(key=key, backend_name=self.backend_name)
        if isinstance(err, (ForbiddenError, UnauthorizedError)):
            return StoragePermissionError(key=key, backend_name=self.backend_name)
        if isinstance(err, (BucketNotFoundError, DefaultBucketError)):
            return StorageConnectionError(
                str(err), key=key, backend_name=self.backend_name
            )
        if isinstance(err, TooManyRequestsError):
            return StorageError(
                "Rate limited by Replit Object Storage",
                key=key,
                backend_name=self.backend_name,
            )
        return StorageError(str(err), key=key, backend_name=self.backend_name)

    async def _run(self, fn, *args, **kwargs):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, partial(fn, *args, **kwargs))

    @staticmethod
    def _normalize(key: str) -> str:
        return key.lstrip("/")

    async def read(self, key: str) -> str:
        normalized = self._normalize(key)
        try:
            return await self._run(self._client.download_as_text, normalized)
        except Exception as exc:
            raise self._map_error(exc, key) from exc

    async def read_bytes(self, key: str) -> bytes:
        normalized = self._normalize(key)
        try:
            return await self._run(self._client.download_as_bytes, normalized)
        except Exception as exc:
            raise self._map_error(exc, key) from exc

    async def write(self, key: str, content: str) -> None:
        normalized = self._normalize(key)
        try:
            await self._run(self._client.upload_from_text, normalized, content)
        except Exception as exc:
            raise self._map_error(exc, key) from exc

    async def write_bytes(self, key: str, content: bytes) -> None:
        normalized = self._normalize(key)
        try:
            await self._run(self._client.upload_from_bytes, normalized, content)
        except Exception as exc:
            raise self._map_error(exc, key) from exc

    async def delete(self, key: str) -> None:
        normalized = self._normalize(key)
        try:
            await self._run(
                self._client.delete, normalized, ignore_not_found=True
            )
        except Exception as exc:
            raise self._map_error(exc, key) from exc

    async def delete_prefix(self, prefix: str) -> None:
        normalized = self._normalize(prefix)
        if normalized and not normalized.endswith("/"):
            normalized += "/"
        try:
            objects = await self._run(self._client.list, prefix=normalized)
            for obj in objects:
                await self._run(
                    self._client.delete, obj.name, ignore_not_found=True
                )
        except Exception as exc:
            raise self._map_error(exc, prefix) from exc

    async def exists(self, key: str) -> bool:
        normalized = self._normalize(key)
        try:
            return await self._run(self._client.exists, normalized)
        except Exception as exc:
            raise self._map_error(exc, key) from exc

    async def list(self, prefix: str) -> list[FileInfo]:
        normalized = self._normalize(prefix)
        if normalized and not normalized.endswith("/"):
            normalized += "/"
        try:
            objects = await self._run(self._client.list, prefix=normalized)
        except Exception as exc:
            raise self._map_error(exc, prefix) from exc

        seen_dirs: set[str] = set()
        entries: list[FileInfo] = []

        for obj in objects:
            if not obj.name.startswith(normalized):
                continue
            rel = obj.name[len(normalized):]
            if not rel:
                continue

            if "/" in rel:
                dir_name = rel.split("/", 1)[0]
                dir_path = normalized + dir_name
                if dir_name not in seen_dirs:
                    seen_dirs.add(dir_name)
                    entries.append(
                        FileInfo(
                            name=dir_name,
                            path=dir_path,
                            is_dir=True,
                            size=0,
                            modified_at="",
                        )
                    )
            else:
                entries.append(
                    FileInfo(
                        name=rel,
                        path=obj.name,
                        is_dir=False,
                        size=0,
                        modified_at="",
                    )
                )

        return entries

    async def copy(self, src: str, dst: str) -> None:
        src_key = self._normalize(src)
        dst_key = self._normalize(dst)
        try:
            await self._run(self._client.copy, src_key, dst_key)
        except Exception as exc:
            raise self._map_error(exc, src) from exc

    async def move(self, src: str, dst: str) -> None:
        await self.copy(src, dst)
        await self.delete(src)

    async def get_presigned_url(self, key: str, expires_in: int = 3600) -> str:
        raise StorageError(
            "Presigned URLs not supported by Replit Object Storage backend",
            key=key,
            backend_name=self.backend_name,
        )

    async def health_check(self) -> bool:
        try:
            await self._run(self._client.list, prefix="")
            return True
        except Exception:
            return False
