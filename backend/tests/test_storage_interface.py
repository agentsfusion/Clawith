"""Tests for storage interface: FileInfo dataclass and exception types."""

import pytest

from app.services.storage.interface import (
    FileInfo,
    StorageError,
    FileNotFoundError as StorageFileNotFoundError,
    StorageConnectionError,
    StoragePermissionError,
)


class TestFileInfo:
    """Test FileInfo dataclass."""

    def test_create_with_all_fields(self):
        """FileInfo can be created with all fields specified."""
        file_info = FileInfo(
            name="test.txt",
            path="/path/to/test.txt",
            is_dir=False,
            size=1024,
            modified_at="2024-01-01T00:00:00Z",
        )

        assert file_info.name == "test.txt"
        assert file_info.path == "/path/to/test.txt"
        assert file_info.is_dir is False
        assert file_info.size == 1024
        assert file_info.modified_at == "2024-01-01T00:00:00Z"

    def test_create_with_defaults(self):
        """FileInfo uses default values for size and modified_at."""
        file_info = FileInfo(name="test.txt", path="/path/to/test.txt", is_dir=False)

        assert file_info.name == "test.txt"
        assert file_info.path == "/path/to/test.txt"
        assert file_info.is_dir is False
        assert file_info.size == 0
        assert file_info.modified_at == ""

    def test_directory_info(self):
        """FileInfo can represent a directory."""
        dir_info = FileInfo(
            name="docs", path="/path/to/docs", is_dir=True, size=0, modified_at=""
        )

        assert dir_info.name == "docs"
        assert dir_info.path == "/path/to/docs"
        assert dir_info.is_dir is True
        assert dir_info.size == 0
        assert dir_info.modified_at == ""


class TestStorageError:
    """Test StorageError base exception."""

    def test_message_only(self):
        """StorageError with message only formats correctly."""
        error = StorageError("Test error message")

        assert str(error) == "Test error message"
        assert error.key == ""
        assert error.backend_name == ""

    def test_message_with_key(self):
        """StorageError with message and key formats correctly."""
        error = StorageError("Test error message", key="test/key.txt")

        assert str(error) == "Test error message (key='test/key.txt')"
        assert error.key == "test/key.txt"
        assert error.backend_name == ""

    def test_message_with_key_and_backend_name(self):
        """StorageError with all parameters formats correctly."""
        error = StorageError(
            "Test error message", key="test/key.txt", backend_name="local"
        )

        assert str(error) == "[local] Test error message (key='test/key.txt')"
        assert error.key == "test/key.txt"
        assert error.backend_name == "local"

    def test_backend_name_only(self):
        """StorageError with backend_name only formats correctly."""
        error = StorageError("Test error message", backend_name="s3")

        assert str(error) == "[s3] Test error message"
        assert error.key == ""
        assert error.backend_name == "s3"

    def test_key_only_with_backend_name(self):
        """StorageError with key and backend_name formats correctly."""
        error = StorageError("Test error message", key="test/key.txt", backend_name="s3")

        assert str(error) == "[s3] Test error message (key='test/key.txt')"
        assert error.key == "test/key.txt"
        assert error.backend_name == "s3"

    def test_is_exception(self):
        """StorageError is a proper Exception subclass."""
        error = StorageError("Test")

        assert isinstance(error, Exception)
        assert isinstance(error, StorageError)

    def test_can_be_raised(self):
        """StorageError can be raised and caught."""
        with pytest.raises(StorageError, match="Test error"):
            raise StorageError("Test error")


class TestStorageFileNotFoundError:
    """Test FileNotFoundError exception."""

    def test_inheritance(self):
        """FileNotFoundError is a subclass of StorageError."""
        error = StorageFileNotFoundError("test/key.txt")

        assert isinstance(error, StorageError)
        assert isinstance(error, StorageFileNotFoundError)

    def test_default_message(self):
        """FileNotFoundError has correct default message."""
        error = StorageFileNotFoundError("test/key.txt")

        assert str(error) == "File not found (key='test/key.txt')"
        assert error.key == "test/key.txt"
        assert error.backend_name == ""

    def test_with_backend_name(self):
        """FileNotFoundError with backend_name formats correctly."""
        error = StorageFileNotFoundError("test/key.txt", backend_name="s3")

        assert str(error) == "[s3] File not found (key='test/key.txt')"
        assert error.key == "test/key.txt"
        assert error.backend_name == "s3"

    def test_key_stored(self):
        """FileNotFoundError stores key attribute."""
        error = StorageFileNotFoundError("missing/file.txt")

        assert error.key == "missing/file.txt"

    def test_backend_name_stored(self):
        """FileNotFoundError stores backend_name attribute."""
        error = StorageFileNotFoundError("test.txt", backend_name="local")

        assert error.backend_name == "local"


class TestStorageConnectionError:
    """Test StorageConnectionError exception."""

    def test_inheritance(self):
        """StorageConnectionError is a subclass of StorageError."""
        error = StorageConnectionError()

        assert isinstance(error, StorageError)
        assert isinstance(error, StorageConnectionError)

    def test_default_message(self):
        """StorageConnectionError has correct default message."""
        error = StorageConnectionError()

        assert str(error) == "Storage connection failed"
        assert error.key == ""
        assert error.backend_name == ""

    def test_custom_message(self):
        """StorageConnectionError accepts custom message."""
        error = StorageConnectionError("Custom connection failed")

        assert str(error) == "Custom connection failed"

    def test_with_key(self):
        """StorageConnectionError with key formats correctly."""
        error = StorageConnectionError(key="test/key.txt")

        assert str(error) == "Storage connection failed (key='test/key.txt')"
        assert error.key == "test/key.txt"

    def test_with_backend_name(self):
        """StorageConnectionError with backend_name formats correctly."""
        error = StorageConnectionError(backend_name="s3")

        assert str(error) == "[s3] Storage connection failed"
        assert error.backend_name == "s3"

    def test_with_all_parameters(self):
        """StorageConnectionError with all parameters formats correctly."""
        error = StorageConnectionError(
            message="Connection timeout", key="test/key.txt", backend_name="s3"
        )

        assert str(error) == "[s3] Connection timeout (key='test/key.txt')"
        assert error.key == "test/key.txt"
        assert error.backend_name == "s3"


class TestStoragePermissionError:
    """Test StoragePermissionError exception."""

    def test_inheritance(self):
        """StoragePermissionError is a subclass of StorageError."""
        error = StoragePermissionError()

        assert isinstance(error, StorageError)
        assert isinstance(error, StoragePermissionError)

    def test_default_message(self):
        """StoragePermissionError has correct default message."""
        error = StoragePermissionError()

        assert str(error) == "Permission denied"
        assert error.key == ""
        assert error.backend_name == ""

    def test_with_key(self):
        """StoragePermissionError with key formats correctly."""
        error = StoragePermissionError(key="test/key.txt")

        assert str(error) == "Permission denied (key='test/key.txt')"
        assert error.key == "test/key.txt"

    def test_with_backend_name(self):
        """StoragePermissionError with backend_name formats correctly."""
        error = StoragePermissionError(backend_name="s3")

        assert str(error) == "[s3] Permission denied"
        assert error.backend_name == "s3"

    def test_with_key_and_backend_name(self):
        """StoragePermissionError with both parameters formats correctly."""
        error = StoragePermissionError(key="test/key.txt", backend_name="local")

        assert str(error) == "[local] Permission denied (key='test/key.txt')"
        assert error.key == "test/key.txt"
        assert error.backend_name == "local"
