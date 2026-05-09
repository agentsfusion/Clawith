"""Integration tests verifying CLI Agent sync integration points.

These tests confirm that the sync_layer functions (sync_dir_to_obs)
work correctly with the WorkspaceSyncManager (sync_to_local) and
StorageBackend implementations, simulating the CLI Agent execution flow.
"""

import uuid
from pathlib import Path
from unittest.mock import patch

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


@pytest.fixture
def fake_storage():
    return FakeStorage()


@pytest.fixture
def agent_id():
    return uuid.uuid4()


@pytest.mark.asyncio
async def test_cli_agent_sync_roundtrip(ws, agent_id, fake_storage):
    """Simulates the full CLI Agent sync lifecycle:
    1. OBS has existing files → sync_to_local downloads them
    2. CLI modifies/adds files locally
    3. sync_dir_to_obs uploads changes back to OBS
    """
    from app.services.workspace_sync.manager import WorkspaceSyncManager

    fake_storage._files[f"{agent_id}/workspace/README.md"] = b"# Old content"
    fake_storage._files[f"{agent_id}/workspace/config.json"] = b'{"key": "val"}'

    sync_mgr = WorkspaceSyncManager(
        storage=fake_storage,
        idle_timeout=300,
        debounce_ms=100,
        max_watchers=10,
        sync_ttl_seconds=0,
    )

    await sync_mgr.sync_to_local(str(agent_id), ws)

    assert (ws / "workspace" / "README.md").exists()
    assert (ws / "workspace" / "README.md").read_text() == "# Old content"

    (ws / "workspace" / "README.md").write_text("# Updated by CLI")
    (ws / "workspace" / "new_report.md").write_text("Fresh output")

    with patch("app.services.sync_layer._is_cloud_storage", return_value=True), \
         patch("app.services.sync_layer.get_storage", return_value=fake_storage):
        from app.services.sync_layer import sync_dir_to_obs
        stats = await sync_dir_to_obs(agent_id, ws, "workspace")

    assert stats["uploaded"] >= 2

    assert fake_storage.uploads[f"{agent_id}/workspace/README.md"] == b"# Updated by CLI"
    assert fake_storage.uploads[f"{agent_id}/workspace/new_report.md"] == b"Fresh output"

    assert fake_storage._files[f"{agent_id}/workspace/README.md"] == b"# Updated by CLI"


@pytest.mark.asyncio
async def test_cli_agent_sync_pre_downloads_for_cli_reading(ws, agent_id, fake_storage):
    """Verifies CLI Agent can read OBS files after pre-sync."""
    from app.services.workspace_sync.manager import WorkspaceSyncManager

    fake_storage._files[f"{agent_id}/workspace/data/input.csv"] = b"x,y\n1,2"

    sync_mgr = WorkspaceSyncManager(
        storage=fake_storage,
        idle_timeout=300,
        debounce_ms=100,
        max_watchers=10,
        sync_ttl_seconds=0,
    )

    await sync_mgr.sync_to_local(str(agent_id), ws)

    local_file = ws / "workspace" / "data" / "input.csv"
    assert local_file.exists()
    assert local_file.read_text() == "x,y\n1,2"


@pytest.fixture
def ws(tmp_path, agent_id):
    return tmp_path / str(agent_id)
