"""Tests for LocalStorageBackend using tmp_path fixture."""

import pytest

from app.services.storage.interface import (
    FileNotFoundError as StorageFileNotFoundError,
    StorageError,
    StoragePermissionError,
)
from app.services.storage.local import LocalStorageBackend


@pytest.mark.asyncio
class TestLocalStorageBackend:
    """Test LocalStorageBackend functionality."""

    async def test_write_and_read_text(self, tmp_path):
        """Write and read text file."""
        backend = LocalStorageBackend(str(tmp_path))

        await backend.write("test.txt", "Hello, World!")
        content = await backend.read("test.txt")

        assert content == "Hello, World!"

    async def test_write_overwrites_existing(self, tmp_path):
        """Writing to existing key overwrites content."""
        backend = LocalStorageBackend(str(tmp_path))

        await backend.write("test.txt", "Original")
        await backend.write("test.txt", "Updated")
        content = await backend.read("test.txt")

        assert content == "Updated"

    async def test_write_and_read_binary(self, tmp_path):
        """Write and read binary file."""
        backend = LocalStorageBackend(str(tmp_path))

        binary_data = b"\x00\x01\x02\x03\x04\x05"
        await backend.write_bytes("binary.bin", binary_data)
        content = await backend.read_bytes("binary.bin")

        assert content == binary_data

    async def test_write_binary_overwrites_existing(self, tmp_path):
        """Writing binary to existing key overwrites content."""
        backend = LocalStorageBackend(str(tmp_path))

        await backend.write_bytes("binary.bin", b"original")
        await backend.write_bytes("binary.bin", b"updated")
        content = await backend.read_bytes("binary.bin")

        assert content == b"updated"

    async def test_read_nonexistent_file_raises(self, tmp_path):
        """Reading non-existent file raises FileNotFoundError."""
        backend = LocalStorageBackend(str(tmp_path))

        with pytest.raises(StorageFileNotFoundError, match="not found"):
            await backend.read("does-not-exist.txt")

    async def test_read_bytes_nonexistent_file_raises(self, tmp_path):
        """Reading bytes from non-existent file raises FileNotFoundError."""
        backend = LocalStorageBackend(str(tmp_path))

        with pytest.raises(StorageFileNotFoundError, match="not found"):
            await backend.read_bytes("does-not-exist.bin")

    async def test_delete_existing_file(self, tmp_path):
        """Delete existing file removes it."""
        backend = LocalStorageBackend(str(tmp_path))

        await backend.write("test.txt", "content")
        await backend.delete("test.txt")

        assert not await backend.exists("test.txt")

    async def test_delete_nonexistent_file_noop(self, tmp_path):
        """Delete non-existent file is no-op (no error)."""
        backend = LocalStorageBackend(str(tmp_path))

        await backend.delete("does-not-exist.txt")  # Should not raise

        assert not await backend.exists("does-not-exist.txt")

    async def test_delete_prefix_removes_directory_tree(self, tmp_path):
        """Delete prefix recursively removes all files."""
        backend = LocalStorageBackend(str(tmp_path))

        # Create directory structure
        await backend.write("dir1/file1.txt", "content1")
        await backend.write("dir1/file2.txt", "content2")
        await backend.write("dir1/subdir/file3.txt", "content3")
        await backend.write("dir2/file4.txt", "content4")

        # Delete dir1 prefix
        await backend.delete_prefix("dir1")

        assert not await backend.exists("dir1/file1.txt")
        assert not await backend.exists("dir1/file2.txt")
        assert not await backend.exists("dir1/subdir/file3.txt")
        assert await backend.exists("dir2/file4.txt")  # dir2 should remain

    async def test_delete_prefix_nonexistent_directory(self, tmp_path):
        """Delete prefix on non-existent directory is no-op."""
        backend = LocalStorageBackend(str(tmp_path))

        await backend.delete_prefix("does-not-exist")  # Should not raise

    async def test_exists_true(self, tmp_path):
        """Exists returns True for existing file."""
        backend = LocalStorageBackend(str(tmp_path))

        await backend.write("test.txt", "content")

        assert await backend.exists("test.txt") is True

    async def test_exists_false(self, tmp_path):
        """Exists returns False for non-existent file."""
        backend = LocalStorageBackend(str(tmp_path))

        assert await backend.exists("does-not-exist.txt") is False

    async def test_list_root(self, tmp_path):
        """List root directory returns FileInfo objects."""
        backend = LocalStorageBackend(str(tmp_path))

        await backend.write("file1.txt", "content1")
        await backend.write("file2.txt", "content2")
        await backend.write("dir1/file3.txt", "content3")

        entries = await backend.list("")

        assert len(entries) == 3
        names = [e.name for e in entries]
        assert "file1.txt" in names
        assert "file2.txt" in names
        assert "dir1" in names

        # Check FileInfo attributes
        file_entry = next(e for e in entries if e.name == "file1.txt")
        assert file_entry.is_dir is False
        assert file_entry.size > 0
        assert file_entry.path == "file1.txt"
        assert file_entry.modified_at

        dir_entry = next(e for e in entries if e.name == "dir1")
        assert dir_entry.is_dir is True
        assert dir_entry.size == 0
        assert dir_entry.path == "dir1"

    async def test_list_subdirectory(self, tmp_path):
        """List subdirectory returns entries under that prefix."""
        backend = LocalStorageBackend(str(tmp_path))

        await backend.write("docs/readme.md", "readme content")
        await backend.write("docs/guide.md", "guide content")
        await backend.write("other.txt", "other content")

        entries = await backend.list("docs")

        assert len(entries) == 2
        names = [e.name for e in entries]
        assert "readme.md" in names
        assert "guide.md" in names

    async def test_list_empty_directory(self, tmp_path):
        """List empty directory returns empty list."""
        backend = LocalStorageBackend(str(tmp_path))

        entries = await backend.list("")

        assert entries == []

    async def test_list_nonexistent_prefix(self, tmp_path):
        """List non-existent prefix returns empty list."""
        backend = LocalStorageBackend(str(tmp_path))

        entries = await backend.list("does-not-exist")

        assert entries == []

    async def test_copy_file(self, tmp_path):
        """Copy file from src to dst."""
        backend = LocalStorageBackend(str(tmp_path))

        await backend.write("source.txt", "content")
        await backend.copy("source.txt", "destination.txt")

        assert await backend.exists("source.txt")
        assert await backend.exists("destination.txt")
        assert await backend.read("destination.txt") == "content"

    async def test_copy_to_subdirectory(self, tmp_path):
        """Copy file to subdirectory creates parent dirs."""
        backend = LocalStorageBackend(str(tmp_path))

        await backend.write("source.txt", "content")
        await backend.copy("source.txt", "subdir/dest.txt")

        assert await backend.exists("source.txt")
        assert await backend.exists("subdir/dest.txt")
        assert await backend.read("subdir/dest.txt") == "content"

    async def test_copy_nonexistent_file_raises(self, tmp_path):
        """Copy non-existent file raises FileNotFoundError."""
        backend = LocalStorageBackend(str(tmp_path))

        with pytest.raises(StorageFileNotFoundError, match="not found"):
            await backend.copy("does-not-exist.txt", "dest.txt")

    async def test_move_file(self, tmp_path):
        """Move file from src to dst removes src."""
        backend = LocalStorageBackend(str(tmp_path))

        await backend.write("source.txt", "content")
        await backend.move("source.txt", "destination.txt")

        assert not await backend.exists("source.txt")
        assert await backend.exists("destination.txt")
        assert await backend.read("destination.txt") == "content"

    async def test_move_to_subdirectory(self, tmp_path):
        """Move file to subdirectory creates parent dirs."""
        backend = LocalStorageBackend(str(tmp_path))

        await backend.write("source.txt", "content")
        await backend.move("source.txt", "subdir/dest.txt")

        assert not await backend.exists("source.txt")
        assert await backend.exists("subdir/dest.txt")
        assert await backend.read("subdir/dest.txt") == "content"

    async def test_move_nonexistent_file_raises(self, tmp_path):
        """Move non-existent file raises FileNotFoundError."""
        backend = LocalStorageBackend(str(tmp_path))

        with pytest.raises(StorageFileNotFoundError, match="not found"):
            await backend.move("does-not-exist.txt", "dest.txt")

    async def test_get_presigned_url_raises(self, tmp_path):
        """Presigned URLs are not supported by local backend."""
        backend = LocalStorageBackend(str(tmp_path))

        with pytest.raises(StorageError, match="not supported"):
            await backend.get_presigned_url("test.txt")

    async def test_health_check_true(self, tmp_path):
        """Health check returns True when root directory exists."""
        backend = LocalStorageBackend(str(tmp_path))

        assert await backend.health_check() is True

    async def test_health_check_false_nonexistent_root(self):
        """Health check returns False when root directory does not exist."""
        backend = LocalStorageBackend("/nonexistent/directory/that/does/not/exist")

        assert await backend.health_check() is False

    async def test_path_traversal_protection(self, tmp_path):
        """Path traversal with ../ is blocked."""
        backend = LocalStorageBackend(str(tmp_path))

        with pytest.raises(StoragePermissionError, match="Permission denied"):
            await backend.write("../etc/passwd", "hacked")

        with pytest.raises(StoragePermissionError, match="Permission denied"):
            await backend.read("../../../etc/passwd")

        with pytest.raises(StoragePermissionError, match="Permission denied"):
            await backend.write("safe/../../etc/passwd", "hacked")

    async def test_path_traversal_in_delete_prefix(self, tmp_path):
        """Path traversal in delete_prefix is blocked."""
        backend = LocalStorageBackend(str(tmp_path))

        with pytest.raises(StoragePermissionError, match="Permission denied"):
            await backend.delete_prefix("../../etc")

    async def test_subdirectory_support(self, tmp_path):
        """Support for nested subdirectories."""
        backend = LocalStorageBackend(str(tmp_path))

        # Create nested structure
        await backend.write("agent_id/memory/memory.md", "# Agent Memory")
        await backend.write("agent_id/context/context.json", '{"key": "value"}')
        await backend.write("agent_id/skills/python.md", "# Python Skill")

        # Read from nested paths
        assert await backend.read("agent_id/memory/memory.md") == "# Agent Memory"
        assert await backend.read("agent_id/context/context.json") == '{"key": "value"}'

        # List intermediate directory
        entries = await backend.list("agent_id")
        names = [e.name for e in entries]
        assert "memory" in names
        assert "context" in names
        assert "skills" in names

        # List subdirectory
        entries = await backend.list("agent_id/memory")
        assert len(entries) == 1
        assert entries[0].name == "memory.md"

    async def test_unicode_in_filename(self, tmp_path):
        """Unicode characters in filenames work correctly."""
        backend = LocalStorageBackend(str(tmp_path))

        await backend.write("测试文件.txt", "中文内容")
        content = await backend.read("测试文件.txt")

        assert content == "中文内容"

    async def test_large_file_read_write(self, tmp_path):
        """Large file operations work correctly."""
        backend = LocalStorageBackend(str(tmp_path))

        # 1MB file
        large_content = "x" * (1024 * 1024)
        await backend.write("large.txt", large_content)
        content = await backend.read("large.txt")

        assert content == large_content
        assert len(content) == 1024 * 1024

    async def test_list_sorting_directories_first(self, tmp_path):
        """List returns directories before files."""
        backend = LocalStorageBackend(str(tmp_path))

        await backend.write("z_file.txt", "content")
        await backend.write("a_dir/file.txt", "content")
        await backend.write("m_file.txt", "content")

        entries = await backend.list("")

        # Directories should come before files
        dir_entries = [e for e in entries if e.is_dir]
        file_entries = [e for e in entries if not e.is_dir]

        assert len(dir_entries) > 0
        assert len(file_entries) > 0

        # All directories should appear before any files in sorted order
        max_dir_index = max(i for i, e in enumerate(entries) if e.is_dir)
        min_file_index = min(i for i, e in enumerate(entries) if not e.is_dir)
        assert max_dir_index < min_file_index
