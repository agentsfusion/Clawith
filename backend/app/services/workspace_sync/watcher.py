"""Per-agent filesystem watcher using watchfiles."""

import asyncio
import re
import time
from pathlib import Path

from loguru import logger

from app.services.workspace_sync.uploader import DebouncedUploader

_EXCLUDED_PATTERNS = re.compile(r"(_exec_tmp\..*|\.gitkeep|\.DS_Store)")
_HIDDEN_RE = re.compile(r"(^|/)\.[^/]+")


def _should_ignore(path_str: str) -> bool:
    name = Path(path_str).name
    if _EXCLUDED_PATTERNS.fullmatch(name):
        return True
    if name.startswith("."):
        return True
    return False


class AgentWatcher:
    """Watches a single agent's workspace directory for file changes
    and pushes them to a DebouncedUploader.
    """

    def __init__(
        self,
        agent_id: str,
        watch_path: Path,
        uploader: DebouncedUploader,
        idle_timeout: int = 300,
    ):
        self.agent_id = agent_id
        self.watch_path = watch_path
        self.uploader = uploader
        self.idle_timeout = idle_timeout
        self._task: asyncio.Task | None = None
        self._last_event_time: float = time.monotonic()
        self._stopped = False

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def last_event_time(self) -> float:
        return self._last_event_time

    def start(self):
        if self.is_running:
            return
        self._stopped = False
        self._task = asyncio.create_task(self._watch_loop(), name=f"watcher-{self.agent_id[:8]}")
        self.uploader.start()
        logger.info(f"[StorageSync] Started watcher for agent {self.agent_id[:8]}")

    async def stop(self):
        self._stopped = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await self.uploader.stop()
        logger.info(f"[StorageSync] Stopped watcher for agent {self.agent_id[:8]}")

    async def _watch_loop(self):
        try:
            from watchfiles import awatch, Change
        except ImportError:
            logger.warning("[StorageSync] watchfiles not installed, file sync disabled")
            return

        try:
            async for changes in awatch(
                str(self.watch_path),
                stop_event=asyncio.Event() if self._stopped else None,
                rust_timeout=1_000,
                yield_on_timeout=True,
            ):
                if self._stopped:
                    break

                now = time.monotonic()
                idle_elapsed = now - self._last_event_time
                if idle_elapsed > self.idle_timeout:
                    logger.info(
                        f"[StorageSync] Idle timeout ({self.idle_timeout}s) for agent {self.agent_id[:8]}"
                    )
                    break

                if not changes:
                    continue

                self._last_event_time = now
                for change_type, path_str in changes:
                    if change_type not in (Change.added, Change.modified):
                        continue
                    if _should_ignore(path_str):
                        continue
                    p = Path(path_str)
                    if not p.is_file():
                        continue
                    self.uploader.submit(path_str)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[StorageSync] Watcher error for agent {self.agent_id[:8]}: {e}")
        finally:
            self._stopped = True
