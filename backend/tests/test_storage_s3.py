"""Tests for S3StorageBackend using moto mock."""

import pytest

try:
    from moto import mock_aws
except ImportError:
    pytest.skip("moto not installed", allow_module_level=True)

from app.services.storage.interface import (
    FileNotFoundError as StorageFileNotFoundError,
    StoragePermissionError,
)
from app.services.storage.s3 import S3StorageBackend


@pytest.fixture
def s3_backend():
    """Create S3 backend with moto mock."""
    with mock_aws():
        # Create backend with test bucket
        backend = S3StorageBackend(
            bucket="test-bucket",
            region="us-east-1",
            access_key="test-access-key",
            secret_key="test-secret-key",
            force_path_style=True,
        )

        # Create bucket
        import boto3

        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="test-bucket")

        yield backend


@pytest.fixture
def s3_backend_custom_endpoint():
    """Create S3 backend with custom endpoint (e.g., MinIO, Huawei OBS)."""
    with mock_aws():
        backend = S3StorageBackend(
            bucket="test-bucket",
            endpoint_url="https://minio.example.com",
            region="us-east-1",
            access_key="test-access-key",
            secret_key="test-secret-key",
            force_path_style=True,
        )

        # Create bucket with custom endpoint
        import boto3

        s3 = boto3.client(
            "s3",
            region_name="us-east-1",
            endpoint_url="https://minio.example.com",
            aws_access_key_id="test-access-key",
            aws_secret_access_key="test-secret-key",
        )
        s3.create_bucket(Bucket="test-bucket")

        yield backend


@pytest.mark.asyncio
class TestS3StorageBackend:
    """Test S3StorageBackend functionality."""

    async def test_write_and_read_text(self, s3_backend):
        """Write and read text file."""
        await s3_backend.write("test.txt", "Hello, World!")
        content = await s3_backend.read("test.txt")

        assert content == "Hello, World!"

    async def test_write_overwrites_existing(self, s3_backend):
        """Writing to existing key overwrites content."""
        await s3_backend.write("test.txt", "Original")
        await s3_backend.write("test.txt", "Updated")
        content = await s3_backend.read("test.txt")

        assert content == "Updated"

    async def test_write_and_read_binary(self, s3_backend):
        """Write and read binary file."""
        binary_data = b"\x00\x01\x02\x03\x04\x05"
        await s3_backend.write_bytes("binary.bin", binary_data)
        content = await s3_backend.read_bytes("binary.bin")

        assert content == binary_data

    async def test_write_binary_overwrites_existing(self, s3_backend):
        """Writing binary to existing key overwrites content."""
        await s3_backend.write_bytes("binary.bin", b"original")
        await s3_backend.write_bytes("binary.bin", b"updated")
        content = await s3_backend.read_bytes("binary.bin")

        assert content == b"updated"

    async def test_read_nonexistent_key_raises(self, s3_backend):
        """Reading non-existent key raises FileNotFoundError."""
        with pytest.raises(StorageFileNotFoundError, match="not found"):
            await s3_backend.read("does-not-exist.txt")

    async def test_read_bytes_nonexistent_key_raises(self, s3_backend):
        """Reading bytes from non-existent key raises FileNotFoundError."""
        with pytest.raises(StorageFileNotFoundError, match="not found"):
            await s3_backend.read_bytes("does-not-exist.bin")

    async def test_delete_existing_key(self, s3_backend):
        """Delete existing key removes it."""
        await s3_backend.write("test.txt", "content")
        await s3_backend.delete("test.txt")

        assert not await s3_backend.exists("test.txt")

    async def test_delete_nonexistent_key_noop(self, s3_backend):
        """Delete non-existent key is no-op (no error)."""
        await s3_backend.delete("does-not-exist.txt")  # Should not raise

        assert not await s3_backend.exists("does-not-exist.txt")

    async def test_delete_prefix_removes_all_keys(self, s3_backend):
        """Delete prefix recursively removes all matching keys."""
        # Create keys with prefix
        await s3_backend.write("dir1/file1.txt", "content1")
        await s3_backend.write("dir1/file2.txt", "content2")
        await s3_backend.write("dir1/subdir/file3.txt", "content3")
        await s3_backend.write("dir2/file4.txt", "content4")

        # Delete dir1 prefix
        await s3_backend.delete_prefix("dir1")

        assert not await s3_backend.exists("dir1/file1.txt")
        assert not await s3_backend.exists("dir1/file2.txt")
        assert not await s3_backend.exists("dir1/subdir/file3.txt")
        assert await s3_backend.exists("dir2/file4.txt")  # dir2 should remain

    async def test_exists_true(self, s3_backend):
        """Exists returns True for existing key."""
        await s3_backend.write("test.txt", "content")

        assert await s3_backend.exists("test.txt") is True

    async def test_exists_false(self, s3_backend):
        """Exists returns False for non-existent key."""
        assert await s3_backend.exists("does-not-exist.txt") is False

    async def test_list_root(self, s3_backend):
        """List root returns FileInfo objects."""
        await s3_backend.write("file1.txt", "content1")
        await s3_backend.write("file2.txt", "content2")
        await s3_backend.write("dir1/file3.txt", "content3")

        entries = await s3_backend.list("")

        assert len(entries) == 3
        names = [e.name for e in entries]
        assert "file1.txt" in names
        assert "file2.txt" in names
        assert "dir1" in names

        # Check file entry
        file_entry = next(e for e in entries if e.name == "file1.txt")
        assert file_entry.is_dir is False
        assert file_entry.size > 0
        assert file_entry.path == "file1.txt"
        assert file_entry.modified_at  # Should have timestamp

        # Check directory entry
        dir_entry = next(e for e in entries if e.name == "dir1")
        assert dir_entry.is_dir is True
        assert dir_entry.size == 0
        assert dir_entry.path == "dir1"

    async def test_list_prefix(self, s3_backend):
        """List with prefix returns entries under that prefix."""
        await s3_backend.write("docs/readme.md", "readme content")
        await s3_backend.write("docs/guide.md", "guide content")
        await s3_backend.write("other.txt", "other content")

        entries = await s3_backend.list("docs")

        assert len(entries) == 2
        names = [e.name for e in entries]
        assert "readme.md" in names
        assert "guide.md" in names

    async def test_list_empty_prefix(self, s3_backend):
        """List empty prefix returns empty list."""
        entries = await s3_backend.list("")

        assert entries == []

    async def test_list_nonexistent_prefix(self, s3_backend):
        """List non-existent prefix returns empty list."""
        entries = await s3_backend.list("does-not-exist")

        assert entries == []

    async def test_list_pagination(self, s3_backend):
        """List with pagination (more than 1000 keys)."""
        # Create 1500 keys to test pagination
        for i in range(1500):
            await s3_backend.write(f"file{i:04d}.txt", f"content{i}")

        # List should still work (S3 paginates internally)
        entries = await s3_backend.list("")

        # With Delimiter="/", all files should be listed (no directories in this case)
        assert len(entries) == 1500
        assert all(e.name == f"file{i:04d}.txt" for i, e in enumerate(sorted(entries, key=lambda x: x.name)))

    async def test_copy_key(self, s3_backend):
        """Copy key from src to dst."""
        await s3_backend.write("source.txt", "content")
        await s3_backend.copy("source.txt", "destination.txt")

        assert await s3_backend.exists("source.txt")
        assert await s3_backend.exists("destination.txt")
        assert await s3_backend.read("destination.txt") == "content"

    async def test_copy_to_subdirectory(self, s3_backend):
        """Copy key to subdirectory."""
        await s3_backend.write("source.txt", "content")
        await s3_backend.copy("source.txt", "subdir/dest.txt")

        assert await s3_backend.exists("source.txt")
        assert await s3_backend.exists("subdir/dest.txt")
        assert await s3_backend.read("subdir/dest.txt") == "content"

    async def test_copy_nonexistent_key_raises(self, s3_backend):
        """Copy non-existent key raises FileNotFoundError."""
        with pytest.raises(StorageFileNotFoundError, match="not found"):
            await s3_backend.copy("does-not-exist.txt", "dest.txt")

    async def test_move_key(self, s3_backend):
        """Move key from src to dst removes src."""
        await s3_backend.write("source.txt", "content")
        await s3_backend.move("source.txt", "destination.txt")

        assert not await s3_backend.exists("source.txt")
        assert await s3_backend.exists("destination.txt")
        assert await s3_backend.read("destination.txt") == "content"

    async def test_move_to_subdirectory(self, s3_backend):
        """Move key to subdirectory."""
        await s3_backend.write("source.txt", "content")
        await s3_backend.move("source.txt", "subdir/dest.txt")

        assert not await s3_backend.exists("source.txt")
        assert await s3_backend.exists("subdir/dest.txt")
        assert await s3_backend.read("subdir/dest.txt") == "content"

    async def test_move_nonexistent_key_raises(self, s3_backend):
        """Move non-existent key raises FileNotFoundError."""
        with pytest.raises(StorageFileNotFoundError, match="not found"):
            await s3_backend.move("does-not-exist.txt", "dest.txt")

    async def test_get_presigned_url(self, s3_backend):
        """Get presigned URL returns a URL string."""
        await s3_backend.write("test.txt", "content")

        url = await s3_backend.get_presigned_url("test.txt", expires_in=3600)

        assert isinstance(url, str)
        assert "test.txt" in url
        assert "X-Amz" in url or "AWSAccessKeyId" in url  # Signature present

    async def test_get_presigned_url_custom_expires(self, s3_backend):
        """Get presigned URL with custom expiration."""
        await s3_backend.write("test.txt", "content")

        url = await s3_backend.get_presigned_url("test.txt", expires_in=7200)

        assert isinstance(url, str)
        assert "test.txt" in url

    async def test_get_presigned_url_nonexistent_key(self, s3_backend):
        """Get presigned URL for non-existent key still returns URL."""
        # S3 allows presigned URL generation for non-existent keys
        url = await s3_backend.get_presigned_url("does-not-exist.txt")

        assert isinstance(url, str)
        assert "does-not-exist.txt" in url

    async def test_health_check_true(self, s3_backend):
        """Health check returns True when bucket exists."""
        assert await s3_backend.health_check() is True

    async def test_health_check_false_nonexistent_bucket(self):
        """Health check returns False when bucket does not exist."""
        with mock_aws():
            backend = S3StorageBackend(
                bucket="nonexistent-bucket",
                region="us-east-1",
                access_key="test",
                secret_key="test",
            )

            assert await backend.health_check() is False

    async def test_custom_endpoint(self, s3_backend_custom_endpoint):
        """S3 backend works with custom endpoint."""
        await s3_backend_custom_endpoint.write("test.txt", "content")
        content = await s3_backend_custom_endpoint.read("test.txt")

        assert content == "content"

    async def test_unicode_in_key(self, s3_backend):
        """Unicode characters in keys work correctly."""
        await s3_backend.write("测试文件.txt", "中文内容")
        content = await s3_backend.read("测试文件.txt")

        assert content == "中文内容"

    async def test_large_file_read_write(self, s3_backend):
        """Large file operations work correctly."""
        # 5MB file
        large_content = "x" * (5 * 1024 * 1024)
        await s3_backend.write("large.txt", large_content)
        content = await s3_backend.read("large.txt")

        assert content == large_content
        assert len(content) == 5 * 1024 * 1024

    async def test_key_normalization_leading_slash(self, s3_backend):
        """Leading slash in key is normalized (removed)."""
        await s3_backend.write("/test.txt", "content")

        assert await s3_backend.exists("test.txt") is True
        assert await s3_backend.exists("/test.txt") is True  # Both work

    async def test_force_path_style_disabled(self):
        """S3 backend can be created with path-style disabled."""
        with mock_aws():
            backend = S3StorageBackend(
                bucket="test-bucket",
                region="us-east-1",
                force_path_style=False,  # Virtual-hosted style
                access_key="test",
                secret_key="test",
            )

            # Create bucket
            import boto3

            s3 = boto3.client("s3", region_name="us-east-1")
            s3.create_bucket(Bucket="test-bucket")

            # Should still work
            await backend.write("test.txt", "content")
            content = await backend.read("test.txt")

            assert content == "content"
