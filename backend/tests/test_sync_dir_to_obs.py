import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

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


@pytest.fixture
def ws(tmp_path, agent_id):
    d = tmp_path / str(agent_id)
    d.mkdir()
    return d


@pytest.mark.asyncio
async def test_upload_new_files(ws, agent_id, fake_storage):
    (ws / "workspace").mkdir()
    (ws / "workspace" / "report.md").write_text("hello")
    (ws / "workspace" / "sub").mkdir()
    (ws / "workspace" / "sub" / "data.csv").write_text("a,b\n1,2")

    with patch("app.services.sync_layer._is_cloud_storage", return_value=True), \
         patch("app.services.sync_layer.get_storage", return_value=fake_storage):
        from app.services.sync_layer import sync_dir_to_obs
        stats = await sync_dir_to_obs(agent_id, ws, "workspace")

    assert stats["uploaded"] == 2
    assert stats["skipped"] == 0
    assert stats["failed"] == 0
    assert fake_storage.uploads[f"{agent_id}/workspace/report.md"] == b"hello"
    assert fake_storage.uploads[f"{agent_id}/workspace/sub/data.csv"] == b"a,b\n1,2"


@pytest.mark.asyncio
async def test_skip_unchanged_files(ws, agent_id, fake_storage):
    content = b"existing"
    fake_storage._files[f"{agent_id}/workspace/old.txt"] = content

    (ws / "workspace").mkdir()
    (ws / "workspace" / "old.txt").write_bytes(content)

    with patch("app.services.sync_layer._is_cloud_storage", return_value=True), \
         patch("app.services.sync_layer.get_storage", return_value=fake_storage):
        from app.services.sync_layer import sync_dir_to_obs
        stats = await sync_dir_to_obs(agent_id, ws, "workspace")

    assert stats["uploaded"] == 0
    assert stats["skipped"] == 1
    assert f"{agent_id}/workspace/old.txt" not in fake_storage.uploads


@pytest.mark.asyncio
async def test_upload_failure_does_not_block_others(ws, agent_id, fake_storage):
    (ws / "workspace").mkdir()
    (ws / "workspace" / "good.txt").write_text("ok")
    (ws / "workspace" / "bad.txt").write_text("fail")

    original_write_bytes = fake_storage.write_bytes
    call_count = 0

    async def flaky_write_bytes(key, data):
        nonlocal call_count
        call_count += 1
        if "bad.txt" in key:
            raise RuntimeError("OBS unavailable")
        await original_write_bytes(key, data)

    fake_storage.write_bytes = flaky_write_bytes

    with patch("app.services.sync_layer._is_cloud_storage", return_value=True), \
         patch("app.services.sync_layer.get_storage", return_value=fake_storage):
        from app.services.sync_layer import sync_dir_to_obs
        stats = await sync_dir_to_obs(agent_id, ws, "workspace")

    assert stats["uploaded"] == 1
    assert stats["failed"] == 1
    assert f"{agent_id}/workspace/good.txt" in fake_storage.uploads


@pytest.mark.asyncio
async def test_noop_when_local_storage(ws, agent_id, fake_storage):
    (ws / "workspace").mkdir()
    (ws / "workspace" / "file.txt").write_text("data")

    with patch("app.services.sync_layer._is_cloud_storage", return_value=False), \
         patch("app.services.sync_layer.get_storage", return_value=fake_storage):
        from app.services.sync_layer import sync_dir_to_obs
        stats = await sync_dir_to_obs(agent_id, ws, "workspace")

    assert stats["uploaded"] == 0
    assert stats["skipped"] == 0
    assert stats["failed"] == 0
    assert len(fake_storage.uploads) == 0


@pytest.mark.asyncio
async def test_empty_directory(ws, agent_id, fake_storage):
    (ws / "workspace").mkdir()

    with patch("app.services.sync_layer._is_cloud_storage", return_value=True), \
         patch("app.services.sync_layer.get_storage", return_value=fake_storage):
        from app.services.sync_layer import sync_dir_to_obs
        stats = await sync_dir_to_obs(agent_id, ws, "workspace")

    assert stats["uploaded"] == 0
    assert stats["failed"] == 0


@pytest.mark.asyncio
async def test_missing_subdir(ws, agent_id, fake_storage):
    with patch("app.services.sync_layer._is_cloud_storage", return_value=True), \
         patch("app.services.sync_layer.get_storage", return_value=fake_storage):
        from app.services.sync_layer import sync_dir_to_obs
        stats = await sync_dir_to_obs(agent_id, ws, "workspace")

    assert stats["uploaded"] == 0
    assert stats["failed"] == 0
