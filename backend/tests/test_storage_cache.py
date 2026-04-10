"""Tests for CachedStorageBackend TTL and invalidation."""

import time

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services.storage.cache import CachedStorageBackend
from app.services.storage.interface import FileInfo


@pytest.mark.asyncio
class TestCachedStorageBackend:
    """Test CachedStorageBackend functionality."""

    async def test_cache_miss_reads_from_backend(self, tmp_path):
        """Cache miss reads from backend and caches locally."""
        # Mock backend
        mock_backend = AsyncMock()
        mock_backend.read.return_value = "backend content"
        mock_backend.backend_name = "mock"
        mock_backend.read_bytes.return_value = b"binary content"

        cache_dir = str(tmp_path / "cache")
        cached = CachedStorageBackend(mock_backend, cache_dir=cache_dir, ttl_seconds=60)

        # First read should hit backend
        content = await cached.read("test.txt")

        assert content == "backend content"
        mock_backend.read.assert_called_once_with("test.txt")

    async def test_cache_hit_reads_from_cache(self, tmp_path):
        """Cache hit reads from local cache (backend not called)."""
        # Mock backend
        mock_backend = AsyncMock()
        mock_backend.read.return_value = "backend content"
        mock_backend.backend_name = "mock"

        cache_dir = str(tmp_path / "cache")
        cached = CachedStorageBackend(mock_backend, cache_dir=cache_dir, ttl_seconds=60)

        # First read - cache miss
        await cached.read("test.txt")
        mock_backend.read.assert_called_once()

        # Reset mock
        mock_backend.reset_mock()

        # Second read - cache hit
        content = await cached.read("test.txt")

        assert content == "backend content"
        mock_backend.read.assert_not_called()  # Should not be called on cache hit

    async def test_write_invalidates_cache(self, tmp_path):
        """Write operation invalidates cache entry."""
        mock_backend = AsyncMock()
        mock_backend.read.return_value = "old content"
        mock_backend.backend_name = "mock"

        cache_dir = str(tmp_path / "cache")
        cached = CachedStorageBackend(mock_backend, cache_dir=cache_dir, ttl_seconds=60)

        # Read to populate cache
        await cached.read("test.txt")

        # Write new content
        await cached.write("test.txt", "new content")

        mock_backend.write.assert_called_once_with("test.txt", "new content")

        # Read again - should hit backend (cache invalidated)
        mock_backend.read.reset_mock()
        content = await cached.read("test.txt")

        assert content == "old content"
        mock_backend.read.assert_called_once()  # Backend called again after write

    async def test_delete_invalidates_cache(self, tmp_path):
        """Delete operation invalidates cache entry."""
        mock_backend = AsyncMock()
        mock_backend.read.return_value = "content"
        mock_backend.backend_name = "mock"

        cache_dir = str(tmp_path / "cache")
        cached = CachedStorageBackend(mock_backend, cache_dir=cache_dir, ttl_seconds=60)

        # Read to populate cache
        await cached.read("test.txt")

        # Delete
        await cached.delete("test.txt")

        mock_backend.delete.assert_called_once_with("test.txt")

        # Read again - should hit backend (cache invalidated)
        mock_backend.read.reset_mock()
        content = await cached.read("test.txt")

        assert content == "content"
        mock_backend.read.assert_called_once()  # Backend called again after delete

    async def test_ttl_expiry_triggers_fresh_read(self, tmp_path):
        """Stale cache (TTL expired) triggers fresh read from backend."""
        mock_backend = AsyncMock()
        mock_backend.read.return_value = "v1 content"
        mock_backend.backend_name = "mock"

        cache_dir = str(tmp_path / "cache")
        cached = CachedStorageBackend(mock_backend, cache_dir=cache_dir, ttl_seconds=1)

        # First read - cache miss
        await cached.read("test.txt")
        mock_backend.read.assert_called_once()

        # Update backend return value
        mock_backend.read.reset_mock()
        mock_backend.read.return_value = "v2 content"

        # Wait for TTL to expire
        time.sleep(1.5)

        # Read again - should hit backend (TTL expired)
        content = await cached.read("test.txt")

        assert content == "v2 content"
        mock_backend.read.assert_called_once()

    async def test_ttl_not_expired_reads_from_cache(self, tmp_path):
        """Fresh cache (within TTL) reads from cache."""
        mock_backend = AsyncMock()
        mock_backend.read.return_value = "content"
        mock_backend.backend_name = "mock"

        cache_dir = str(tmp_path / "cache")
        cached = CachedStorageBackend(mock_backend, cache_dir=cache_dir, ttl_seconds=60)

        # First read
        await cached.read("test.txt")
        mock_backend.read.assert_called_once()

        # Reset mock
        mock_backend.reset_mock()

        # Wait short time (within TTL)
        time.sleep(0.5)

        # Read again - should hit cache
        content = await cached.read("test.txt")

        assert content == "content"
        mock_backend.read.assert_not_called()

    async def test_disabled_cache_pass_through(self, tmp_path):
        """Disabled cache (empty cache_dir) passes through to backend."""
        mock_backend = AsyncMock()
        mock_backend.read.return_value = "content"
        mock_backend.backend_name = "mock"

        # Empty cache_dir disables cache
        cached = CachedStorageBackend(mock_backend, cache_dir="", ttl_seconds=60)

        # First read
        await cached.read("test.txt")
        mock_backend.read.assert_called_once()

        # Reset mock
        mock_backend.reset_mock()

        # Second read - still hits backend (cache disabled)
        await cached.read("test.txt")

        mock_backend.read.assert_called_once()  # Backend called every time

    async def test_list_always_hits_backend(self, tmp_path):
        """list() always hits backend, never caches."""
        mock_backend = AsyncMock()
        mock_backend.list.return_value = [
            FileInfo(name="file1.txt", path="file1.txt", is_dir=False, size=100),
            FileInfo(name="dir1", path="dir1", is_dir=True, size=0),
        ]
        mock_backend.backend_name = "mock"

        cache_dir = str(tmp_path / "cache")
        cached = CachedStorageBackend(mock_backend, cache_dir=cache_dir, ttl_seconds=60)

        # First list
        entries = await cached.list("")

        assert len(entries) == 2
        mock_backend.list.assert_called_once_with("")

        # Second list - still hits backend
        mock_backend.list.reset_mock()
        entries = await cached.list("")

        assert len(entries) == 2
        mock_backend.list.assert_called_once_with("")  # Called again, not cached

    async def test_binary_read_cache_hit(self, tmp_path):
        """Binary read_bytes caches and hits cache."""
        mock_backend = AsyncMock()
        mock_backend.read_bytes.return_value = b"binary content"
        mock_backend.backend_name = "mock"

        cache_dir = str(tmp_path / "cache")
        cached = CachedStorageBackend(mock_backend, cache_dir=cache_dir, ttl_seconds=60)

        # First read - cache miss
        content = await cached.read_bytes("binary.bin")

        assert content == b"binary content"
        mock_backend.read_bytes.assert_called_once_with("binary.bin")

        # Second read - cache hit
        mock_backend.read_bytes.reset_mock()
        content = await cached.read_bytes("binary.bin")

        assert content == b"binary content"
        mock_backend.read_bytes.assert_not_called()

    async def test_binary_write_invalidates_cache(self, tmp_path):
        """Binary write_bytes invalidates cache."""
        mock_backend = AsyncMock()
        mock_backend.read_bytes.return_value = b"old content"
        mock_backend.backend_name = "mock"

        cache_dir = str(tmp_path / "cache")
        cached = CachedStorageBackend(mock_backend, cache_dir=cache_dir, ttl_seconds=60)

        # Read to populate cache
        await cached.read_bytes("binary.bin")

        # Write new binary content
        await cached.write_bytes("binary.bin", b"new content")

        mock_backend.write_bytes.assert_called_once_with("binary.bin", b"new content")

        # Read again - should hit backend (cache invalidated)
        mock_backend.read_bytes.reset_mock()
        content = await cached.read_bytes("binary.bin")

        assert content == b"old content"
        mock_backend.read_bytes.assert_called_once()

    async def test_delete_prefix_invalidates_cache(self, tmp_path):
        """delete_prefix invalidates all cache entries under prefix."""
        mock_backend = AsyncMock()
        mock_backend.read.return_value = "content"
        mock_backend.backend_name = "mock"

        cache_dir = str(tmp_path / "cache")
        cached = CachedStorageBackend(mock_backend, cache_dir=cache_dir, ttl_seconds=60)

        # Read to populate cache
        await cached.read("dir1/file1.txt")
        await cached.read("dir1/file2.txt")
        await cached.read("dir2/file3.txt")

        # Delete prefix
        await cached.delete_prefix("dir1")

        mock_backend.delete_prefix.assert_called_once_with("dir1")

        # Read dir1 files - should hit backend (cache invalidated)
        mock_backend.read.reset_mock()
        await cached.read("dir1/file1.txt")
        await cached.read("dir1/file2.txt")

        assert mock_backend.read.call_count == 2  # Both re-read from backend

    async def test_copy_invalidates_destination_cache(self, tmp_path):
        """copy invalidates cache for destination key."""
        mock_backend = AsyncMock()
        mock_backend.read.return_value = "content"
        mock_backend.backend_name = "mock"

        cache_dir = str(tmp_path / "cache")
        cached = CachedStorageBackend(mock_backend, cache_dir=cache_dir, ttl_seconds=60)

        # Populate cache for destination
        await cached.read("dest.txt")
        mock_backend.read.assert_called_once()

        # Copy
        await cached.copy("src.txt", "dest.txt")

        mock_backend.copy.assert_called_once_with("src.txt", "dest.txt")

        # Read destination - should hit backend (cache invalidated)
        mock_backend.read.reset_mock()
        content = await cached.read("dest.txt")

        assert content == "content"
        mock_backend.read.assert_called_once()

    async def test_move_invalidates_both_caches(self, tmp_path):
        """move invalidates cache for both src and dst."""
        mock_backend = AsyncMock()
        mock_backend.read.return_value = "content"
        mock_backend.backend_name = "mock"

        cache_dir = str(tmp_path / "cache")
        cached = CachedStorageBackend(mock_backend, cache_dir=cache_dir, ttl_seconds=60)

        # Populate cache for both src and dst
        await cached.read("src.txt")
        await cached.read("dest.txt")

        # Move
        await cached.move("src.txt", "dest.txt")

        mock_backend.move.assert_called_once_with("src.txt", "dest.txt")

        # Read both - should hit backend (cache invalidated)
        mock_backend.read.reset_mock()
        await cached.read("src.txt")
        await cached.read("dest.txt")

        assert mock_backend.read.call_count == 2  # Both re-read from backend

    async def test_exists_pass_through(self, tmp_path):
        """exists() passes through to backend."""
        mock_backend = AsyncMock()
        mock_backend.exists.return_value = True
        mock_backend.backend_name = "mock"

        cache_dir = str(tmp_path / "cache")
        cached = CachedStorageBackend(mock_backend, cache_dir=cache_dir, ttl_seconds=60)

        result = await cached.exists("test.txt")

        assert result is True
        mock_backend.exists.assert_called_once_with("test.txt")

    async def test_get_presigned_url_pass_through(self, tmp_path):
        """get_presigned_url() passes through to backend."""
        mock_backend = AsyncMock()
        mock_backend.get_presigned_url.return_value = "https://example.com/presigned"
        mock_backend.backend_name = "mock"

        cache_dir = str(tmp_path / "cache")
        cached = CachedStorageBackend(mock_backend, cache_dir=cache_dir, ttl_seconds=60)

        url = await cached.get_presigned_url("test.txt", expires_in=3600)

        assert url == "https://example.com/presigned"
        mock_backend.get_presigned_url.assert_called_once_with("test.txt", 3600)

    async def test_health_check_pass_through(self, tmp_path):
        """health_check() passes through to backend."""
        mock_backend = AsyncMock()
        mock_backend.health_check.return_value = True
        mock_backend.backend_name = "mock"

        cache_dir = str(tmp_path / "cache")
        cached = CachedStorageBackend(mock_backend, cache_dir=cache_dir, ttl_seconds=60)

        result = await cached.health_check()

        assert result is True
        mock_backend.health_check.assert_called_once()

    async def test_backend_name_forwarded(self, tmp_path):
        """Backend name is forwarded from wrapped backend."""
        mock_backend = MagicMock()
        mock_backend.backend_name = "s3"

        cache_dir = str(tmp_path / "cache")
        cached = CachedStorageBackend(mock_backend, cache_dir=cache_dir, ttl_seconds=60)

        assert cached.backend_name == "s3"

    async def test_cache_directory_created(self, tmp_path):
        """Cache directory is created if it doesn't exist."""
        mock_backend = AsyncMock()
        mock_backend.backend_name = "mock"

        cache_dir = str(tmp_path / "cache" / "nested" / "path")
        cached = CachedStorageBackend(mock_backend, cache_dir=cache_dir, ttl_seconds=60)

        # Cache directory should be created
        import os

        assert os.path.exists(cache_dir)

    async def test_multiple_keys_cached_independently(self, tmp_path):
        """Different keys are cached independently."""
        mock_backend = AsyncMock()
        mock_backend.read.side_effect = ["content1", "content2", "content3"]
        mock_backend.backend_name = "mock"

        cache_dir = str(tmp_path / "cache")
        cached = CachedStorageBackend(mock_backend, cache_dir=cache_dir, ttl_seconds=60)

        # Read different keys
        await cached.read("file1.txt")
        await cached.read("file2.txt")
        await cached.read("file3.txt")

        assert mock_backend.read.call_count == 3

        # Reset mock
        mock_backend.read.reset_mock()

        # Read again - all should hit cache
        await cached.read("file1.txt")
        await cached.read("file2.txt")
        await cached.read("file3.txt")

        mock_backend.read.assert_not_called()
