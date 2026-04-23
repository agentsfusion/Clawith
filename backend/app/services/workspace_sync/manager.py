"""WorkspaceSyncManager — singleton managing per-agent file watchers
and bidirectional workspace sync (upload via watchers, download via
sync_to_local).
"""

import time
from pathlib import Path
from uuid import UUID

from loguru import logger

from app.services.workspace_sync.uploader import DebouncedUploader
from app.services.workspace_sync.watcher import AgentWatcher


class WorkspaceSyncManager:
    def __init__(
        self,
        storage,
        idle_timeout: int = 300,
        debounce_ms: int = 500,
        max_watchers: int = 100,
        sync_ttl_seconds: int = 60,
    ):
        self._storage = storage
        self._idle_timeout = idle_timeout
        self._debounce_ms = debounce_ms
        self._max_watchers = max_watchers
        self._sync_ttl_seconds = sync_ttl_seconds
        self._watchers: dict[str, AgentWatcher] = {}
        self._last_sync: dict[str, float] = {}

    async def ensure_watcher(self, agent_id: UUID, workspace_path: Path):
        key = str(agent_id)
        existing = self._watchers.get(key)
        if existing and existing.is_running:
            return existing

        await self._evict_if_needed()

        uploader = DebouncedUploader(self._storage, debounce_ms=self._debounce_ms)
        watcher = AgentWatcher(
            agent_id=key,
            watch_path=workspace_path,
            uploader=uploader,
            idle_timeout=self._idle_timeout,
        )
        self._watchers[key] = watcher
        watcher.start()
        return watcher

    async def sync_to_local(
        self,
        agent_id: str,
        local_dir: Path,
        prefix: str = "",
    ) -> dict[str, int]:
        """Download files from cloud storage to the local workspace directory.

        Recursively lists all files under ``{agent_id}/{prefix}`` in the
        configured storage backend and downloads them to the corresponding
        paths under *local_dir*.  Existing files with the same size as the
        remote object are skipped to minimise redundant transfers.

        Args:
            agent_id: Agent UUID string (used as storage key prefix).
            local_dir: Local filesystem directory to write files into.
            prefix: Optional sub-path to scope the sync (e.g. ``"skills/"``).

        Returns:
            A dict with counts: ``{"downloaded": n, "skipped": n, "failed": n}``.
        """
        stats = {"downloaded": 0, "skipped": 0, "failed": 0}

        now = time.monotonic()
        last = self._last_sync.get(agent_id, 0.0)
        if (now - last) < self._sync_ttl_seconds:
            logger.debug(
                f"[StorageSync] Skipping sync for {agent_id[:8]} — "
                f"last synced {now - last:.1f}s ago (TTL={self._sync_ttl_seconds}s)"
            )
            return stats

        list_prefix = f"{agent_id}/{prefix}" if prefix else f"{agent_id}/"
        await self._sync_prefix(list_prefix, agent_id, local_dir, stats)
        self._last_sync[agent_id] = time.monotonic()
        logger.info(
            f"[StorageSync] sync_to_local completed for {agent_id[:8]}: "
            f"downloaded={stats['downloaded']}, skipped={stats['skipped']}, "
            f"failed={stats['failed']}"
        )
        return stats

    async def _sync_prefix(
        self,
        list_prefix: str,
        agent_id: str,
        local_dir: Path,
        stats: dict[str, int],
    ) -> None:
        """Recursively sync one level of the storage prefix."""
        try:
            entries = await self._storage.list(list_prefix)
        except Exception as exc:
            logger.warning(
                f"[StorageSync] Failed to list prefix {list_prefix!r}: {exc}"
            )
            return

        for entry in entries:
            if entry.is_dir:
                await self._sync_prefix(
                    f"{entry.path}/", agent_id, local_dir, stats
                )
                continue

            agent_prefix = f"{agent_id}/"
            rel = entry.path.removeprefix(agent_prefix) if entry.path.startswith(agent_prefix) else entry.path
            local_path = local_dir / rel

            if local_path.exists() and local_path.stat().st_size == entry.size:
                stats["skipped"] += 1
                continue

            local_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                data = await self._storage.read_bytes(entry.path)
                local_path.write_bytes(data)
                stats["downloaded"] += 1
                logger.debug(
                    f"[StorageSync] Downloaded {entry.path} -> {local_path} ({len(data)} bytes)"
                )
            except Exception as exc:
                stats["failed"] += 1
                logger.warning(
                    f"[StorageSync] Failed to download {entry.path}: {exc}"
                )

    async def stop_watcher(self, agent_id: UUID | str):
        key = str(agent_id)
        watcher = self._watchers.pop(key, None)
        if watcher:
            await watcher.stop()

    async def stop_all(self):
        for key in list(self._watchers.keys()):
            watcher = self._watchers.pop(key, None)
            if watcher:
                await watcher.stop()
        logger.info("[StorageSync] All watchers stopped")

    async def _evict_if_needed(self):
        active = {k: w for k, w in self._watchers.items() if w.is_running}
        if len(active) < self._max_watchers:
            return

        oldest_key = min(active, key=lambda k: active[k].last_event_time)
        logger.info(f"[StorageSync] Max watchers reached, evicting {oldest_key[:8]}")
        await self.stop_watcher(oldest_key)

    @property
    def active_count(self) -> int:
        return sum(1 for w in self._watchers.values() if w.is_running)
