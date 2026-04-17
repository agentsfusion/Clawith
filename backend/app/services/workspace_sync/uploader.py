"""Debounced upload pipeline for workspace file sync."""

import asyncio
import time
from loguru import logger


class DebouncedUploader:
    """Uploads file changes to Storage with per-path debounce.

    When the same path is submitted multiple times within the debounce window,
    only the final upload is performed.  Uploads run concurrently but each
    path is uploaded sequentially (last-write-wins).
    """

    def __init__(self, storage, debounce_ms: int = 500):
        self._storage = storage
        self._debounce_ms = debounce_ms
        self._pending: dict[str, float] = {}
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._running = False

    def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="debounced-uploader")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def submit(self, abs_path: str):
        if not self._running:
            self.start()
        self._pending[abs_path] = time.monotonic()
        self._queue.put_nowait(abs_path)

    async def _loop(self):
        while self._running:
            try:
                path = await asyncio.wait_for(self._queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            submit_time = self._pending.get(path)
            if submit_time is None:
                continue

            elapsed_ms = (time.monotonic() - submit_time) * 1000
            remaining_ms = self._debounce_ms - elapsed_ms
            if remaining_ms > 0:
                await asyncio.sleep(remaining_ms / 1000)

            latest = self._pending.get(path)
            if latest != submit_time:
                continue

            self._pending.pop(path, None)
            await self._upload_with_retry(path)

    async def _upload_with_retry(self, abs_path: str):
        for attempt in range(2):
            try:
                from pathlib import Path
                p = Path(abs_path)
                if not p.exists():
                    return
                data = p.read_bytes()
                if not data and p.stat().st_size == 0:
                    data = b""
                # key is derived from the workspace structure
                # expects path like .../<agent_id>/workspace/file.txt
                parts = p.parts
                agent_idx = None
                for i, part in enumerate(parts):
                    if len(part) == 36 and "-" in part:
                        agent_idx = i
                        break
                if agent_idx is None:
                    logger.warning(f"[StorageSync] Cannot determine agent_id from path: {abs_path}")
                    return
                rel = str(Path(*parts[agent_idx:])).replace("\\", "/")
                await self._storage.write_bytes(rel, data)
                logger.debug(f"[StorageSync] Uploaded {rel} ({len(data)} bytes)")
                return
            except Exception as e:
                if attempt == 0:
                    logger.warning(f"[StorageSync] Upload failed for {abs_path}, retrying: {e}")
                    await asyncio.sleep(0.5 * (attempt + 1))
                else:
                    logger.error(f"[StorageSync] Upload failed after retry for {abs_path}: {e}")

    @property
    def is_running(self) -> bool:
        return self._running
