"""WorkspaceSyncManager — singleton managing per-agent file watchers."""

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
    ):
        self._storage = storage
        self._idle_timeout = idle_timeout
        self._debounce_ms = debounce_ms
        self._max_watchers = max_watchers
        self._watchers: dict[str, AgentWatcher] = {}

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
