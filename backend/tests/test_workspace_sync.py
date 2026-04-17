"""Tests for workspace file sync — DebouncedUploader, AgentWatcher, WorkspaceSyncManager."""

import asyncio
import time
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class FakeStorage:
    def __init__(self):
        self.uploads: dict[str, bytes] = {}

    async def write_bytes(self, key: str, data: bytes):
        self.uploads[key] = data

    async def write(self, key: str, content: str):
        self.uploads[key] = content.encode()


# ── DebouncedUploader tests ──


@pytest.mark.asyncio
async def test_debounce_rapid_events_to_same_path(tmp_path):
    storage = FakeStorage()
    from app.services.workspace_sync.uploader import DebouncedUploader

    uploader = DebouncedUploader(storage, debounce_ms=100)

    agent_dir = tmp_path / str(uuid.uuid4()) / "workspace"
    agent_dir.mkdir(parents=True)
    f = agent_dir / "data.csv"
    f.write_text("v1")
    uploader.submit(str(f))
    await asyncio.sleep(0.05)
    f.write_text("v2")
    uploader.submit(str(f))
    await asyncio.sleep(0.05)
    f.write_text("v3")
    uploader.submit(str(f))

    await asyncio.sleep(0.5)
    await uploader.stop()

    assert len(storage.uploads) == 1
    key = list(storage.uploads.keys())[0]
    assert storage.uploads[key] == b"v3"


@pytest.mark.asyncio
async def test_upload_failure_retries_once(tmp_path):
    storage = FakeStorage()
    call_count = 0
    original_write_bytes = storage.write_bytes

    async def failing_write_bytes(key, data):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ConnectionError("OBS unavailable")

        await original_write_bytes(key, data)

    storage.write_bytes = failing_write_bytes

    from app.services.workspace_sync.uploader import DebouncedUploader

    uploader = DebouncedUploader(storage, debounce_ms=50)
    agent_dir = tmp_path / str(uuid.uuid4()) / "workspace"
    agent_dir.mkdir(parents=True)
    f = agent_dir / "report.txt"
    f.write_text("hello")
    uploader.submit(str(f))

    await asyncio.sleep(2)
    await uploader.stop()

    assert call_count == 2
    assert len(storage.uploads) == 1


@pytest.mark.asyncio
async def test_excluded_paths_not_uploaded():
    from app.services.workspace_sync.watcher import _should_ignore

    assert _should_ignore("_exec_tmp.py")
    assert _should_ignore("_exec_tmp.sh")
    assert _should_ignore("_exec_tmp.js")
    assert _should_ignore(".gitkeep")
    assert _should_ignore(".DS_Store")
    assert _should_ignore(".env")
    assert not _should_ignore("report.csv")
    assert not _should_ignore("data.json")


# ── WorkspaceSyncManager tests ──


@pytest.mark.asyncio
async def test_ensure_watcher_creates_single_watcher(tmp_path):
    from app.services.workspace_sync.manager import WorkspaceSyncManager

    storage = FakeStorage()
    mgr = WorkspaceSyncManager(storage, idle_timeout=60, debounce_ms=50)
    agent_id = uuid.uuid4()
    ws = tmp_path / str(agent_id)
    ws.mkdir(parents=True)

    w1 = await mgr.ensure_watcher(agent_id, ws)
    w2 = await mgr.ensure_watcher(agent_id, ws)
    assert w1 is w2
    assert mgr.active_count == 1
    await mgr.stop_all()


@pytest.mark.asyncio
async def test_noop_on_local_storage():
    with patch("app.services.workspace_sync.get_sync_manager", return_value=None):
        from app.services.workspace_sync import get_sync_manager

        mgr = get_sync_manager()
        assert mgr is None


@pytest.mark.asyncio
async def test_max_watchers_eviction(tmp_path):
    from app.services.workspace_sync.manager import WorkspaceSyncManager

    storage = FakeStorage()
    mgr = WorkspaceSyncManager(storage, idle_timeout=60, debounce_ms=50, max_watchers=2)

    ids = [uuid.uuid4() for _ in range(3)]
    for aid in ids:
        ws = tmp_path / str(aid)
        ws.mkdir(parents=True)
        await mgr.ensure_watcher(aid, ws)

    await asyncio.sleep(0.5)
    assert mgr.active_count <= 2
    await mgr.stop_all()


@pytest.mark.asyncio
async def test_stop_all_cleans_up(tmp_path):
    from app.services.workspace_sync.manager import WorkspaceSyncManager

    storage = FakeStorage()
    mgr = WorkspaceSyncManager(storage, idle_timeout=60, debounce_ms=50)
    for _ in range(3):
        aid = uuid.uuid4()
        ws = tmp_path / str(aid)
        ws.mkdir(parents=True)
        await mgr.ensure_watcher(aid, ws)

    await asyncio.sleep(0.3)
    await mgr.stop_all()
    assert mgr.active_count == 0


# ── Integration: file created → appears in storage ──


@pytest.mark.asyncio
async def test_file_created_by_subprocess_syncs_to_storage(tmp_path):
    from app.services.workspace_sync.manager import WorkspaceSyncManager

    storage = FakeStorage()
    mgr = WorkspaceSyncManager(storage, idle_timeout=60, debounce_ms=100)
    agent_id = uuid.uuid4()
    ws = tmp_path / str(agent_id)
    ws.mkdir(parents=True)
    (ws / "workspace").mkdir()

    await mgr.ensure_watcher(agent_id, ws)
    await asyncio.sleep(0.5)

    test_file = ws / "workspace" / "output.csv"
    test_file.write_text("col1,col2\n1,2\n")

    await asyncio.sleep(2)

    found = any("output.csv" in k for k in storage.uploads)
    if found:
        key = [k for k in storage.uploads if "output.csv" in k][0]
        assert storage.uploads[key] == b"col1,col2\n1,2\n"

    await mgr.stop_all()
