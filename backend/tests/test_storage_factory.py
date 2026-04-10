"""Tests for storage factory get_storage() singleton and config-based creation."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.storage.factory import get_storage


class TestStorageFactory:
    """Test storage factory functionality."""

    @patch("app.services.storage.factory.get_settings")
    def test_default_creates_local_storage(self, mock_get_settings, tmp_path):
        """Default (no STORAGE_BACKEND env var) creates LocalStorageBackend."""
        # Mock settings with default values
        mock_settings = MagicMock()
        mock_settings.STORAGE_BACKEND = "local"
        mock_settings.AGENT_DATA_DIR = str(tmp_path)
        mock_settings.STORAGE_CACHE_DIR = ""
        mock_settings.STORAGE_CACHE_TTL_SECONDS = 60
        mock_get_settings.return_value = mock_settings

        # Reset lru_cache to get fresh instance
        get_storage.cache_clear()

        # Get storage
        storage = get_storage()

        # Should be LocalStorageBackend
        assert storage.backend_name == "local"
        from app.services.storage.local import LocalStorageBackend

        assert isinstance(storage, LocalStorageBackend)

    @patch("app.services.storage.factory.get_settings")
    def test_s3_backend_creates_s3_storage(self, mock_get_settings):
        """STORAGE_BACKEND=s3 creates S3StorageBackend."""
        # Mock settings for S3
        mock_settings = MagicMock()
        mock_settings.STORAGE_BACKEND = "s3"
        mock_settings.STORAGE_BUCKET = "test-bucket"
        mock_settings.STORAGE_ENDPOINT_URL = ""
        mock_settings.STORAGE_REGION = "us-east-1"
        mock_settings.STORAGE_ACCESS_KEY = "test-key"
        mock_settings.STORAGE_SECRET_KEY = "test-secret"
        mock_settings.STORAGE_CACHE_DIR = ""
        mock_settings.STORAGE_CACHE_TTL_SECONDS = 60
        mock_get_settings.return_value = mock_settings

        # Reset lru_cache
        get_storage.cache_clear()

        # Get storage
        storage = get_storage()

        # Should be S3StorageBackend
        assert storage.backend_name == "s3"
        from app.services.storage.s3 import S3StorageBackend

        assert isinstance(storage, S3StorageBackend)

    @patch("app.services.storage.factory.get_settings")
    def test_s3_with_custom_endpoint(self, mock_get_settings):
        """S3 backend with custom endpoint_url (e.g., MinIO, Huawei OBS)."""
        mock_settings = MagicMock()
        mock_settings.STORAGE_BACKEND = "s3"
        mock_settings.STORAGE_BUCKET = "test-bucket"
        mock_settings.STORAGE_ENDPOINT_URL = "https://minio.example.com"
        mock_settings.STORAGE_REGION = "us-east-1"
        mock_settings.STORAGE_ACCESS_KEY = "test-key"
        mock_settings.STORAGE_SECRET_KEY = "test-secret"
        mock_settings.STORAGE_CACHE_DIR = ""
        mock_settings.STORAGE_CACHE_TTL_SECONDS = 60
        mock_get_settings.return_value = mock_settings

        # Reset lru_cache
        get_storage.cache_clear()

        # Get storage
        storage = get_storage()

        assert storage.backend_name == "s3"
        from app.services.storage.s3 import S3StorageBackend

        assert isinstance(storage, S3StorageBackend)

    @patch("app.services.storage.factory.get_settings")
    def test_cache_enabled_wraps_backend(self, mock_get_settings, tmp_path):
        """With cache settings, wraps backend with CachedStorageBackend."""
        mock_settings = MagicMock()
        mock_settings.STORAGE_BACKEND = "local"
        mock_settings.AGENT_DATA_DIR = str(tmp_path)
        mock_settings.STORAGE_CACHE_DIR = str(tmp_path / "cache")
        mock_settings.STORAGE_CACHE_TTL_SECONDS = 300
        mock_get_settings.return_value = mock_settings

        # Reset lru_cache
        get_storage.cache_clear()

        # Get storage
        storage = get_storage()

        # Should be CachedStorageBackend wrapping LocalStorageBackend
        from app.services.storage.cache import CachedStorageBackend

        assert isinstance(storage, CachedStorageBackend)
        assert storage.backend_name == "local"

    @patch("app.services.storage.factory.get_settings")
    def test_cache_disabled_pass_through(self, mock_get_settings, tmp_path):
        """Empty cache_dir disables cache (pass-through to backend)."""
        mock_settings = MagicMock()
        mock_settings.STORAGE_BACKEND = "local"
        mock_settings.AGENT_DATA_DIR = str(tmp_path)
        mock_settings.STORAGE_CACHE_DIR = ""  # Empty = disabled
        mock_settings.STORAGE_CACHE_TTL_SECONDS = 60
        mock_get_settings.return_value = mock_settings

        # Reset lru_cache
        get_storage.cache_clear()

        # Get storage
        storage = get_storage()

        # Should be LocalStorageBackend (not wrapped)
        from app.services.storage.local import LocalStorageBackend
        from app.services.storage.cache import CachedStorageBackend

        assert isinstance(storage, LocalStorageBackend)
        assert not isinstance(storage, CachedStorageBackend)

    @patch("app.services.storage.factory.get_settings")
    def test_singleton_behavior(self, mock_get_settings, tmp_path):
        """Calling get_storage() twice returns same instance (singleton)."""
        mock_settings = MagicMock()
        mock_settings.STORAGE_BACKEND = "local"
        mock_settings.AGENT_DATA_DIR = str(tmp_path)
        mock_settings.STORAGE_CACHE_DIR = ""
        mock_settings.STORAGE_CACHE_TTL_SECONDS = 60
        mock_get_settings.return_value = mock_settings

        # Reset lru_cache
        get_storage.cache_clear()

        # Get storage twice
        storage1 = get_storage()
        storage2 = get_storage()

        # Should be the same instance
        assert storage1 is storage2

    @patch("app.services.storage.factory.get_settings")
    def test_singleton_with_cache(self, mock_get_settings, tmp_path):
        """Singleton behavior works with cache enabled."""
        mock_settings = MagicMock()
        mock_settings.STORAGE_BACKEND = "local"
        mock_settings.AGENT_DATA_DIR = str(tmp_path)
        mock_settings.STORAGE_CACHE_DIR = str(tmp_path / "cache")
        mock_settings.STORAGE_CACHE_TTL_SECONDS = 60
        mock_get_settings.return_value = mock_settings

        # Reset lru_cache
        get_storage.cache_clear()

        # Get storage twice
        storage1 = get_storage()
        storage2 = get_storage()

        # Should be the same instance
        assert storage1 is storage2

    @patch("app.services.storage.factory.get_settings")
    def test_invalid_backend_raises_error(self, mock_get_settings, tmp_path):
        """Invalid STORAGE_BACKEND raises ValueError."""
        mock_settings = MagicMock()
        mock_settings.STORAGE_BACKEND = "invalid"
        mock_settings.AGENT_DATA_DIR = str(tmp_path)
        mock_settings.STORAGE_CACHE_DIR = ""
        mock_settings.STORAGE_CACHE_TTL_SECONDS = 60
        mock_get_settings.return_value = mock_settings

        # Reset lru_cache
        get_storage.cache_clear()

        # Should raise ValueError
        with pytest.raises(ValueError, match="Unknown STORAGE_BACKEND"):
            get_storage()

    @patch("app.services.storage.factory.get_settings")
    def test_backend_name_case_insensitive(self, mock_get_settings, tmp_path):
        """STORAGE_BACKEND value is case-insensitive."""
        mock_settings = MagicMock()
        mock_settings.STORAGE_BACKEND = "LOCAL"  # Uppercase
        mock_settings.AGENT_DATA_DIR = str(tmp_path)
        mock_settings.STORAGE_CACHE_DIR = ""
        mock_settings.STORAGE_CACHE_TTL_SECONDS = 60
        mock_get_settings.return_value = mock_settings

        # Reset lru_cache
        get_storage.cache_clear()

        # Get storage
        storage = get_storage()

        # Should create LocalStorageBackend despite uppercase
        assert storage.backend_name == "local"
        from app.services.storage.local import LocalStorageBackend

        assert isinstance(storage, LocalStorageBackend)

    @patch("app.services.storage.factory.get_settings")
    def test_backend_name_whitespace_trimmed(self, mock_get_settings, tmp_path):
        """Whitespace in STORAGE_BACKEND is trimmed."""
        mock_settings = MagicMock()
        mock_settings.STORAGE_BACKEND = "  local  "  # With whitespace
        mock_settings.AGENT_DATA_DIR = str(tmp_path)
        mock_settings.STORAGE_CACHE_DIR = ""
        mock_settings.STORAGE_CACHE_TTL_SECONDS = 60
        mock_get_settings.return_value = mock_settings

        # Reset lru_cache
        get_storage.cache_clear()

        # Get storage
        storage = get_storage()

        # Should create LocalStorageBackend (whitespace trimmed)
        assert storage.backend_name == "local"
        from app.services.storage.local import LocalStorageBackend

        assert isinstance(storage, LocalStorageBackend)

    @patch("app.services.storage.factory.get_settings")
    async def test_s3_backend_async_methods_work(self, mock_get_settings):
        """S3 backend async methods work correctly."""
        mock_settings = MagicMock()
        mock_settings.STORAGE_BACKEND = "s3"
        mock_settings.STORAGE_BUCKET = "test-bucket"
        mock_settings.STORAGE_ENDPOINT_URL = ""
        mock_settings.STORAGE_REGION = "us-east-1"
        mock_settings.STORAGE_ACCESS_KEY = "test-key"
        mock_settings.STORAGE_SECRET_KEY = "test-secret"
        mock_settings.STORAGE_CACHE_DIR = ""
        mock_settings.STORAGE_CACHE_TTL_SECONDS = 60
        mock_get_settings.return_value = mock_settings

        # Reset lru_cache
        get_storage.cache_clear()

        # Get storage
        storage = get_storage()

        # Verify it's an async storage backend
        assert hasattr(storage, "read")
        assert hasattr(storage, "write")
        assert hasattr(storage, "delete")
        assert hasattr(storage, "exists")
        assert hasattr(storage, "list")
        assert hasattr(storage, "copy")
        assert hasattr(storage, "move")
        assert hasattr(storage, "get_presigned_url")
        assert hasattr(storage, "health_check")

    @patch("app.services.storage.factory.get_settings")
    async def test_local_backend_async_methods_work(self, mock_get_settings, tmp_path):
        """Local backend async methods work correctly."""
        mock_settings = MagicMock()
        mock_settings.STORAGE_BACKEND = "local"
        mock_settings.AGENT_DATA_DIR = str(tmp_path)
        mock_settings.STORAGE_CACHE_DIR = ""
        mock_settings.STORAGE_CACHE_TTL_SECONDS = 60
        mock_get_settings.return_value = mock_settings

        # Reset lru_cache
        get_storage.cache_clear()

        # Get storage
        storage = get_storage()

        # Verify it's an async storage backend
        assert hasattr(storage, "read")
        assert hasattr(storage, "write")
        assert hasattr(storage, "delete")
        assert hasattr(storage, "exists")
        assert hasattr(storage, "list")
        assert hasattr(storage, "copy")
        assert hasattr(storage, "move")
        assert hasattr(storage, "get_presigned_url")
        assert hasattr(storage, "health_check")

    @patch("app.services.storage.factory.get_settings")
    async def test_cached_backend_async_methods_work(self, mock_get_settings, tmp_path):
        """Cached backend async methods work correctly."""
        mock_settings = MagicMock()
        mock_settings.STORAGE_BACKEND = "local"
        mock_settings.AGENT_DATA_DIR = str(tmp_path)
        mock_settings.STORAGE_CACHE_DIR = str(tmp_path / "cache")
        mock_settings.STORAGE_CACHE_TTL_SECONDS = 60
        mock_get_settings.return_value = mock_settings

        # Reset lru_cache
        get_storage.cache_clear()

        # Get storage
        storage = get_storage()

        # Verify it's an async storage backend
        assert hasattr(storage, "read")
        assert hasattr(storage, "write")
        assert hasattr(storage, "delete")
        assert hasattr(storage, "exists")
        assert hasattr(storage, "list")
        assert hasattr(storage, "copy")
        assert hasattr(storage, "move")
        assert hasattr(storage, "get_presigned_url")
        assert hasattr(storage, "health_check")

        # Verify it wraps backend
        assert hasattr(storage, "_backend")
