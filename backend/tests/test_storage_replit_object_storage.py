"""Tests for the Replit Object Storage backend.

These exercise the adapter against an in-memory fake GCS bucket so we can verify
key prefixing, generation-based conditional writes, directory emulation, and the
local materialization fallback without touching the network.
"""

from __future__ import annotations

from google.api_core.exceptions import PreconditionFailed
from google.cloud.exceptions import NotFound

from app.services.storage_runtime.base import WriteCondition
from app.services.storage_runtime.replit_object_storage import ReplitObjectStorageBackend


class _Record:
    def __init__(self, data: bytes, generation: int):
        self.data = data
        self.generation = generation


class _FakeBlob:
    def __init__(self, bucket: "_FakeBucket", name: str, snapshot: _Record | None = None):
        self._bucket = bucket
        self.name = name
        if snapshot is not None:
            self.size = len(snapshot.data)
            self.generation = snapshot.generation
            self.updated = "2026-01-01T00:00:00Z"
            self.etag = f"etag-{snapshot.generation}"
            self.md5_hash = f"md5-{snapshot.generation}"
        else:
            self.size = 0
            self.generation = None
            self.updated = ""
            self.etag = ""
            self.md5_hash = ""

    def exists(self) -> bool:
        return self.name in self._bucket.store

    def download_as_bytes(self) -> bytes:
        if self.name not in self._bucket.store:
            raise NotFound(self.name)
        return self._bucket.store[self.name].data

    def upload_from_string(self, data, content_type=None, if_generation_match=None) -> None:
        current = self._bucket.store.get(self.name)
        if if_generation_match is not None:
            current_gen = current.generation if current else 0
            if current_gen != if_generation_match:
                raise PreconditionFailed("generation mismatch")
        self._bucket.counter += 1
        payload = data if isinstance(data, bytes) else data.encode("utf-8")
        self._bucket.store[self.name] = _Record(payload, self._bucket.counter)

    def delete(self, if_generation_match=None) -> None:
        current = self._bucket.store.get(self.name)
        if current is None:
            raise NotFound(self.name)
        if if_generation_match is not None and current.generation != if_generation_match:
            raise PreconditionFailed("generation mismatch")
        del self._bucket.store[self.name]


class _FakeBlobIterator(list):
    def __init__(self, blobs, prefixes):
        super().__init__(blobs)
        self.prefixes = prefixes


class _FakeBucket:
    def __init__(self):
        self.store: dict[str, _Record] = {}
        self.counter = 0

    def blob(self, name: str) -> _FakeBlob:
        return _FakeBlob(self, name)

    def get_blob(self, name: str) -> _FakeBlob | None:
        record = self.store.get(name)
        if record is None:
            return None
        return _FakeBlob(self, name, snapshot=record)

    def list_blobs(self, prefix="", delimiter=None, max_results=None):
        prefix = prefix or ""
        matched = sorted(name for name in self.store if name.startswith(prefix))
        if delimiter is None:
            blobs = [self.get_blob(name) for name in matched]
            if max_results is not None:
                blobs = blobs[:max_results]
            return _FakeBlobIterator(blobs, set())
        prefixes: set[str] = set()
        files: list[_FakeBlob] = []
        for name in matched:
            rest = name[len(prefix):]
            if delimiter in rest:
                sub = rest.split(delimiter, 1)[0]
                prefixes.add(prefix + sub + delimiter)
            else:
                files.append(self.get_blob(name))
        return _FakeBlobIterator(files, prefixes)


def _backend_with_bucket(prefix: str = "agents") -> tuple[ReplitObjectStorageBackend, _FakeBucket]:
    bucket = _FakeBucket()
    backend = ReplitObjectStorageBackend(bucket_id="dummy", prefix=prefix)
    backend._bucket = bucket
    return backend, bucket


def test_object_key_applies_prefix_and_normalizes():
    backend, _ = _backend_with_bucket(prefix="agents")
    assert backend._object_key("a//b/../c.txt") == "agents/a/c.txt"


async def test_write_read_roundtrip_and_version():
    backend, bucket = _backend_with_bucket()
    await backend.write_bytes("agent1/notes.txt", b"hello", content_type="text/plain")
    assert "agents/agent1/notes.txt" in bucket.store
    assert await backend.read_bytes("agent1/notes.txt") == b"hello"
    assert await backend.exists("agent1/notes.txt") is True

    version = await backend.get_version("agent1/notes.txt")
    assert version.exists and not version.is_dir
    assert version.size == 5
    # Generation is exposed as the version token used for conditional writes.
    assert version.token == version.version_id == "1"


async def test_read_missing_raises_file_not_found():
    backend, _ = _backend_with_bucket()
    try:
        await backend.read_bytes("missing.txt")
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("expected FileNotFoundError")


async def test_conditional_write_requires_absent():
    backend, _ = _backend_with_bucket()
    first = await backend.write_bytes_if_match(
        "doc.txt", b"v1", condition=WriteCondition(require_absent=True)
    )
    assert first.ok and not first.conflict

    # Second create-if-absent must conflict because the object now exists.
    second = await backend.write_bytes_if_match(
        "doc.txt", b"v2", condition=WriteCondition(require_absent=True)
    )
    assert not second.ok and second.conflict
    assert await backend.read_bytes("doc.txt") == b"v1"


async def test_conditional_write_detects_stale_token():
    backend, _ = _backend_with_bucket()
    await backend.write_bytes("doc.txt", b"v1")
    version = await backend.get_version("doc.txt")

    # Matching token succeeds and advances the generation.
    ok = await backend.write_bytes_if_match(
        "doc.txt", b"v2", condition=WriteCondition(version_token=version.token)
    )
    assert ok.ok and not ok.conflict

    # Reusing the now-stale token must be rejected.
    stale = await backend.write_bytes_if_match(
        "doc.txt", b"v3", condition=WriteCondition(version_token=version.token)
    )
    assert not stale.ok and stale.conflict
    assert await backend.read_bytes("doc.txt") == b"v2"


async def test_delete_if_match_token_conflict():
    backend, _ = _backend_with_bucket()
    await backend.write_bytes("doc.txt", b"v1")
    result = await backend.delete_if_match(
        "doc.txt", condition=WriteCondition(version_token="999")
    )
    assert not result.ok and result.conflict
    assert await backend.exists("doc.txt") is True

    version = await backend.get_version("doc.txt")
    ok = await backend.delete_if_match(
        "doc.txt", condition=WriteCondition(version_token=version.token)
    )
    assert ok.ok and not ok.conflict
    assert await backend.exists("doc.txt") is False


async def test_list_dir_and_is_dir():
    backend, _ = _backend_with_bucket()
    await backend.write_bytes("agent1/a.txt", b"a")
    await backend.write_bytes("agent1/sub/b.txt", b"b")

    assert await backend.is_dir("agent1") is True
    entries = await backend.list_dir("agent1")
    by_name = {e.name: e for e in entries}
    assert by_name["sub"].is_dir is True
    assert by_name["a.txt"].is_dir is False
    assert by_name["a.txt"].key == "agent1/a.txt"
    assert by_name["a.txt"].size == 1


async def test_delete_tree_removes_all_descendants():
    backend, bucket = _backend_with_bucket()
    await backend.write_bytes("agent1/a.txt", b"a")
    await backend.write_bytes("agent1/sub/b.txt", b"b")
    await backend.write_bytes("agent2/keep.txt", b"keep")

    await backend.delete_tree("agent1")
    remaining = set(bucket.store)
    assert remaining == {"agents/agent2/keep.txt"}


async def test_delete_if_match_token_on_absent_object_conflicts():
    backend, _ = _backend_with_bucket()
    # No object exists, but the caller expects a specific version. Matching the
    # base read-then-compare semantics, this must report a conflict rather than a
    # silent success.
    result = await backend.delete_if_match(
        "ghost.txt", condition=WriteCondition(version_token="5")
    )
    assert not result.ok and result.conflict


async def test_unconditional_delete_if_match_is_idempotent():
    backend, _ = _backend_with_bucket()
    result = await backend.delete_if_match("ghost.txt")
    assert result.ok and not result.conflict


async def test_non_numeric_token_falls_back_to_read_then_compare():
    backend, _ = _backend_with_bucket()
    await backend.write_bytes("doc.txt", b"v1")

    # A token from another backend (non-numeric) cannot be a GCS generation, so
    # the adapter falls back to the base read-then-compare path. A mismatching
    # token must conflict for both write and delete.
    write_conflict = await backend.write_bytes_if_match(
        "doc.txt", b"v2", condition=WriteCondition(version_token="opaque-token")
    )
    assert not write_conflict.ok and write_conflict.conflict
    assert await backend.read_bytes("doc.txt") == b"v1"

    delete_conflict = await backend.delete_if_match(
        "doc.txt", condition=WriteCondition(version_token="opaque-token")
    )
    assert not delete_conflict.ok and delete_conflict.conflict
    assert await backend.exists("doc.txt") is True


def test_facade_selects_replit_backend_with_fallback(monkeypatch):
    from app.services.storage_runtime import facade
    from app.services.storage_runtime.fallback import FallbackStorageBackend

    class _Settings:
        STORAGE_BACKEND = "replit"
        STORAGE_LOCAL_FALLBACK_ENABLED = True
        STORAGE_LOCAL_ROOT = None
        AGENT_DATA_DIR = "/tmp/clawith-test-agent-data"
        REPLIT_OBJECT_STORAGE_BUCKET_ID = "dummy-bucket"
        REPLIT_OBJECT_STORAGE_PREFIX = "agents"

    monkeypatch.setattr(facade, "get_settings", lambda: _Settings())
    monkeypatch.setattr(facade, "_storage_backend", None)

    backend = facade.get_storage_backend()
    assert isinstance(backend, FallbackStorageBackend)
    assert isinstance(backend.primary, ReplitObjectStorageBackend)
    # Reset the module-level cache so other tests are unaffected.
    monkeypatch.setattr(facade, "_storage_backend", None)


async def test_local_path_for_materializes_file(tmp_path):
    backend, _ = _backend_with_bucket()
    await backend.write_bytes("agent1/data.bin", b"payload")
    path = await backend.local_path_for("agent1/data.bin")
    assert path is not None
    assert path.read_bytes() == b"payload"


async def test_presign_returns_none():
    backend, _ = _backend_with_bucket()
    assert await backend.presign_download_url("agent1/x.txt") is None
