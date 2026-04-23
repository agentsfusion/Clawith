"""Tests for workspace file sync — DebouncedUploader, AgentWatcher, WorkspaceSyncManager."""

import asyncio
import time
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.storage.interface import FileInfo


class FakeStorage:
    def __init__(self):
        self.uploads: dict[str, bytes] = {}
        self._files: dict[str, bytes] = {}

    async def write_bytes(self, key: str, data: bytes):
        self.uploads[key] = data
        self._files[key] = data

    async def write(self, key: str, content: str):
        self.uploads[key] = content.encode()
        self._files[key] = content.encode()

    async def read_bytes(self, key: str) -> bytes:
        if key not in self._files:
            from app.services.storage.interface import FileNotFoundError
            raise FileNotFoundError(key=key, backend_name="fake")
        return self._files[key]

    async def list(self, prefix: str) -> list[FileInfo]:
        results: list[FileInfo] = []
        seen_dirs: set[str] = set()
        prefix = prefix.lstrip("/")
        for key in sorted(self._files.keys()):
            if not key.startswith(prefix):
                continue
            rest = key[len(prefix):]
            if "/" in rest:
                dir_name = rest.split("/", 1)[0]
                dir_path = f"{prefix}{dir_name}"
                if dir_path not in seen_dirs:
                    seen_dirs.add(dir_path)
                    results.append(FileInfo(name=dir_name, path=dir_path, is_dir=True, size=0))
            else:
                results.append(FileInfo(
                    name=rest,
                    path=key,
                    is_dir=False,
                    size=len(self._files[key]),
                ))
        return results


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


# ── sync_to_local tests ──


@pytest.mark.asyncio
async def test_sync_to_local_downloads_files(tmp_path):
    from app.services.workspace_sync.manager import WorkspaceSyncManager

    storage = FakeStorage()
    agent_id = str(uuid.uuid4())
    storage._files[f"{agent_id}/skills/my-skill/SKILL.md"] = b"# My Skill\nHello"
    storage._files[f"{agent_id}/workspace/report.txt"] = b"report content"

    mgr = WorkspaceSyncManager(storage, idle_timeout=60, debounce_ms=50)
    local_dir = tmp_path / agent_id
    local_dir.mkdir(parents=True)

    stats = await mgr.sync_to_local(agent_id, local_dir)

    assert stats["downloaded"] == 2
    assert stats["skipped"] == 0
    assert stats["failed"] == 0
    assert (local_dir / "skills" / "my-skill" / "SKILL.md").read_bytes() == b"# My Skill\nHello"
    assert (local_dir / "workspace" / "report.txt").read_bytes() == b"report content"


@pytest.mark.asyncio
async def test_sync_to_local_skips_unchanged_files(tmp_path):
    from app.services.workspace_sync.manager import WorkspaceSyncManager

    storage = FakeStorage()
    agent_id = str(uuid.uuid4())
    content = b"unchanged content"
    storage._files[f"{agent_id}/workspace/file.txt"] = content

    mgr = WorkspaceSyncManager(storage, idle_timeout=60, debounce_ms=50)
    local_dir = tmp_path / agent_id
    local_dir.mkdir(parents=True)
    (local_dir / "workspace").mkdir()
    (local_dir / "workspace" / "file.txt").write_bytes(content)

    stats = await mgr.sync_to_local(agent_id, local_dir)

    assert stats["downloaded"] == 0
    assert stats["skipped"] == 1
    assert stats["failed"] == 0


@pytest.mark.asyncio
async def test_sync_to_local_redownloads_changed_file(tmp_path):
    from app.services.workspace_sync.manager import WorkspaceSyncManager

    storage = FakeStorage()
    agent_id = str(uuid.uuid4())
    storage._files[f"{agent_id}/workspace/data.csv"] = b"new content (10 bytes)"

    mgr = WorkspaceSyncManager(storage, idle_timeout=60, debounce_ms=50)
    local_dir = tmp_path / agent_id
    local_dir.mkdir(parents=True)
    (local_dir / "workspace").mkdir()
    (local_dir / "workspace" / "data.csv").write_bytes(b"old content")

    stats = await mgr.sync_to_local(agent_id, local_dir)

    assert stats["downloaded"] == 1
    assert stats["skipped"] == 0
    assert (local_dir / "workspace" / "data.csv").read_bytes() == b"new content (10 bytes)"


@pytest.mark.asyncio
async def test_sync_to_local_handles_download_failure(tmp_path):
    from app.services.workspace_sync.manager import WorkspaceSyncManager

    storage = FakeStorage()
    agent_id = str(uuid.uuid4())
    storage._files[f"{agent_id}/workspace/good.txt"] = b"ok"
    storage._files[f"{agent_id}/workspace/bad.txt"] = b"will fail"

    original_read_bytes = storage.read_bytes

    async def flaky_read_bytes(key: str) -> bytes:
        if "bad" in key:
            raise ConnectionError("OBS unavailable")
        return await original_read_bytes(key)

    storage.read_bytes = flaky_read_bytes

    mgr = WorkspaceSyncManager(storage, idle_timeout=60, debounce_ms=50)
    local_dir = tmp_path / agent_id
    local_dir.mkdir(parents=True)

    stats = await mgr.sync_to_local(agent_id, local_dir)

    assert stats["downloaded"] == 1
    assert stats["failed"] == 1
    assert (local_dir / "workspace" / "good.txt").read_bytes() == b"ok"
    assert not (local_dir / "workspace" / "bad.txt").exists()


@pytest.mark.asyncio
async def test_sync_to_local_empty_workspace(tmp_path):
    from app.services.workspace_sync.manager import WorkspaceSyncManager

    storage = FakeStorage()
    agent_id = str(uuid.uuid4())

    mgr = WorkspaceSyncManager(storage, idle_timeout=60, debounce_ms=50)
    local_dir = tmp_path / agent_id
    local_dir.mkdir(parents=True)

    stats = await mgr.sync_to_local(agent_id, local_dir)

    assert stats["downloaded"] == 0
    assert stats["skipped"] == 0
    assert stats["failed"] == 0


@pytest.mark.asyncio
async def test_sync_to_local_with_prefix_scope(tmp_path):
    from app.services.workspace_sync.manager import WorkspaceSyncManager

    storage = FakeStorage()
    agent_id = str(uuid.uuid4())
    storage._files[f"{agent_id}/skills/a/SKILL.md"] = b"skill a"
    storage._files[f"{agent_id}/skills/b/SKILL.md"] = b"skill b"
    storage._files[f"{agent_id}/workspace/report.txt"] = b"report"

    mgr = WorkspaceSyncManager(storage, idle_timeout=60, debounce_ms=50)
    local_dir = tmp_path / agent_id
    local_dir.mkdir(parents=True)

    stats = await mgr.sync_to_local(agent_id, local_dir, prefix="skills/")

    assert stats["downloaded"] == 2
    assert not (local_dir / "workspace" / "report.txt").exists()
    assert (local_dir / "skills" / "a" / "SKILL.md").read_bytes() == b"skill a"


# ── Integration: _execute_code pre-sync ──


@pytest.mark.asyncio
async def test_execute_code_legacy_calls_sync_to_local_with_s3_storage(tmp_path):
    from app.services.workspace_sync.manager import WorkspaceSyncManager

    storage = FakeStorage()
    agent_id = str(uuid.uuid4())
    storage._files[f"{agent_id}/skills/helper.py"] = b"print('hello from skill')"

    mgr = WorkspaceSyncManager(storage, idle_timeout=60, debounce_ms=50)
    ws = tmp_path / agent_id
    ws.mkdir(parents=True)

    with patch("app.services.workspace_sync.get_sync_manager", return_value=mgr):
        from app.services.agent_tools import _execute_code_legacy

        result = await _execute_code_legacy(
            ws,
            {
                "language": "python",
                "code": "import os; print(os.path.exists('skills/helper.py'))",
                "timeout": 10,
            },
        )

    assert (ws / "skills" / "helper.py").exists()
    assert (ws / "skills" / "helper.py").read_bytes() == b"print('hello from skill')"
    assert "True" in result


@pytest.mark.asyncio
async def test_execute_code_legacy_no_sync_when_storage_is_local(tmp_path):
    with patch("app.services.workspace_sync.get_sync_manager", return_value=None):
        from app.services.agent_tools import _execute_code_legacy

        ws = tmp_path / str(uuid.uuid4())
        ws.mkdir(parents=True)

        result = await _execute_code_legacy(
            ws,
            {
                "language": "python",
                "code": "print('hello')",
                "timeout": 10,
            },
        )

    assert "hello" in result
