"""Cached storage backend — wraps any StorageBackend with a local filesystem cache."""

from __future__ import annotations

import os
import time
from pathlib import Path

import aiofiles

from .interface import FileInfo, StorageBackend


class CachedStorageBackend:
    """Wraps a StorageBackend with an optional local filesystem cache.

    When *cache_dir* is empty the cache is disabled and every call passes
    through to the wrapped backend with zero overhead.

    When enabled, ``read`` / ``read_bytes`` results are cached locally under
    *cache_dir*.  Writes and deletes invalidate the cached copy.  Cached files
    older than *ttl_seconds* are treated as misses.
    """

    _cache_enabled: bool
    _cache_root: Path | None

    def __init__(
        self,
        backend: StorageBackend,
        cache_dir: str = "",
        ttl_seconds: int = 60,
    ) -> None:
        self._backend = backend
        self._ttl = ttl_seconds

        if cache_dir:
            self._cache_enabled = True
            self._cache_root = Path(cache_dir)
            self._cache_root.mkdir(parents=True, exist_ok=True)
        else:
            self._cache_enabled = False
            self._cache_root = None

        self.backend_name: str = backend.backend_name

        # ---- In-memory metadata cache ----------------------------------
        # `exists` and `list` against object-storage backends (Replit,
        # S3) each cost a network round-trip.  We cache positive AND
        # negative results in-process with a short TTL so repeated calls
        # in the same request (e.g. agent_context loading skill files)
        # don't pay the round-trip every time.  Writes / deletes
        # invalidate matching entries — see `_invalidate_meta`.
        self._meta_ttl: float = float(ttl_seconds)
        self._exists_cache: dict[str, tuple[float, bool]] = {}
        self._list_cache: dict[str, tuple[float, list[FileInfo]]] = {}

    # ---- Metadata cache helpers ---------------------------------------
    def _meta_get_exists(self, key: str) -> bool | None:
        hit = self._exists_cache.get(key)
        if hit is None:
            return None
        ts, val = hit
        if (time.time() - ts) > self._meta_ttl:
            self._exists_cache.pop(key, None)
            return None
        return val

    def _meta_set_exists(self, key: str, val: bool) -> None:
        self._exists_cache[key] = (time.time(), val)

    def _meta_get_list(self, prefix: str) -> list[FileInfo] | None:
        hit = self._list_cache.get(prefix)
        if hit is None:
            return None
        ts, val = hit
        if (time.time() - ts) > self._meta_ttl:
            self._list_cache.pop(prefix, None)
            return None
        return val

    def _meta_set_list(self, prefix: str, val: list[FileInfo]) -> None:
        self._list_cache[prefix] = (time.time(), val)

    def _invalidate_meta(self, key: str) -> None:
        """Drop exists/list cache entries impacted by mutating *key*."""
        self._exists_cache.pop(key, None)
        # Also evict any list cache covering the parent prefix.  A key
        # like ``a/b/c.txt`` invalidates lists rooted at ``a`` and
        # ``a/b`` (we drop *every* matching prefix to be safe).
        parts = key.rstrip("/").split("/")
        prefixes = {""}
        cur = ""
        for p in parts[:-1]:
            cur = f"{cur}/{p}" if cur else p
            prefixes.add(cur)
        for pfx in list(self._list_cache.keys()):
            normalised = pfx.rstrip("/")
            if normalised in prefixes or normalised.startswith(key.rstrip("/")):
                self._list_cache.pop(pfx, None)

    def _cache_path(self, key: str) -> Path:
        return self._cache_root / key  # type: ignore[operator]

    async def _is_cache_hit(self, key: str) -> bool:
        path = self._cache_path(key)
        if not path.exists():
            return False
        mtime = os.path.getmtime(path)
        return (time.time() - mtime) <= self._ttl

    async def _invalidate(self, key: str) -> None:
        path = self._cache_path(key)
        path.unlink(missing_ok=True)

    async def read(self, key: str) -> str:
        if not self._cache_enabled:
            return await self._backend.read(key)

        if await self._is_cache_hit(key):
            async with aiofiles.open(self._cache_path(key), mode="r", encoding="utf-8") as f:
                return await f.read()

        content = await self._backend.read(key)
        path = self._cache_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(path, mode="w", encoding="utf-8") as f:
            await f.write(content)
        return content

    async def read_bytes(self, key: str) -> bytes:
        if not self._cache_enabled:
            return await self._backend.read_bytes(key)

        if await self._is_cache_hit(key):
            async with aiofiles.open(self._cache_path(key), mode="rb") as f:
                return await f.read()

        content = await self._backend.read_bytes(key)
        path = self._cache_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(path, mode="wb") as f:
            await f.write(content)
        return content

    async def write(self, key: str, content: str) -> None:
        await self._backend.write(key, content)
        self._invalidate_meta(key)
        if self._cache_enabled:
            await self._invalidate(key)

    async def write_bytes(self, key: str, content: bytes) -> None:
        await self._backend.write_bytes(key, content)
        self._invalidate_meta(key)
        if self._cache_enabled:
            await self._invalidate(key)

    async def delete(self, key: str) -> None:
        await self._backend.delete(key)
        self._invalidate_meta(key)
        if self._cache_enabled:
            await self._invalidate(key)

    async def delete_prefix(self, prefix: str) -> None:
        await self._backend.delete_prefix(prefix)
        # Wipe metadata caches for everything under this prefix
        norm = prefix.rstrip("/")
        for k in list(self._exists_cache.keys()):
            if k == norm or k.startswith(norm + "/"):
                self._exists_cache.pop(k, None)
        for p in list(self._list_cache.keys()):
            np = p.rstrip("/")
            if np == norm or np.startswith(norm + "/") or norm.startswith(np + "/"):
                self._list_cache.pop(p, None)
        if not self._cache_enabled:
            return
        cache_prefix = self._cache_root / prefix  # type: ignore[operator]
        if cache_prefix.is_dir():
            for path in cache_prefix.rglob("*"):
                if path.is_file():
                    path.unlink(missing_ok=True)

    async def exists(self, key: str) -> bool:
        cached = self._meta_get_exists(key)
        if cached is not None:
            return cached
        val = await self._backend.exists(key)
        self._meta_set_exists(key, val)
        return val

    async def list(self, prefix: str) -> list[FileInfo]:
        cached = self._meta_get_list(prefix)
        if cached is not None:
            return cached
        val = await self._backend.list(prefix)
        self._meta_set_list(prefix, val)
        return val

    async def copy(self, src: str, dst: str) -> None:
        await self._backend.copy(src, dst)
        self._invalidate_meta(dst)
        if self._cache_enabled:
            await self._invalidate(dst)

    async def move(self, src: str, dst: str) -> None:
        await self._backend.move(src, dst)
        self._invalidate_meta(src)
        self._invalidate_meta(dst)
        if self._cache_enabled:
            await self._invalidate(src)
            await self._invalidate(dst)

    async def get_presigned_url(self, key: str, expires_in: int = 3600) -> str:
        return await self._backend.get_presigned_url(key, expires_in)

    async def health_check(self) -> bool:
        return await self._backend.health_check()
