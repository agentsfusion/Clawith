"""S3-compatible storage backend.

Works with AWS S3, Huawei OBS, MinIO, and any S3-compatible service
via configurable ``endpoint_url``.
"""

from __future__ import annotations

import boto3
import aioboto3

try:
    from botocore import config as _botocore_config

    import botocore.exceptions as _boto_exc
except ImportError:  # pragma: no cover
    _botocore_config = None  # type: ignore[assignment]
    _boto_exc = None  # type: ignore[assignment]

from .interface import (
    FileInfo,
    FileNotFoundError,
    StorageConnectionError,
    StorageError,
    StoragePermissionError,
)

_CHUNK_SIZE = 1000


class S3StorageBackend:
    """Async storage backend backed by any S3-compatible object store.

    Parameters
    ----------
    bucket:
        Bucket name.
    endpoint_url:
        Custom endpoint (e.g. ``https://obs.cn-north-4.myhuaweicloud.com``).
        Empty string means default AWS endpoint.
    region:
        AWS region (default ``us-east-1``).
    access_key:
        AWS access key ID.  Falls back to environment / IAM role if empty.
    secret_key:
        AWS secret access key.  Falls back to environment / IAM role if empty.
    force_path_style:
        Use path-style addressing (required for MinIO and many on-prem stores).
    """

    backend_name = "s3"

    def __init__(
        self,
        bucket: str,
        endpoint_url: str = "",
        region: str = "us-east-1",
        access_key: str = "",
        secret_key: str = "",
        force_path_style: bool = True,
    ) -> None:
        self._bucket = bucket
        self._region = region
        self._force_path_style = force_path_style

        s3_config = None
        if force_path_style and _botocore_config is not None:
            s3_config = _botocore_config.Config(s3={"addressing_style": "path"})

        self._client_kwargs: dict = {
            "region_name": region,
            "config": s3_config,
        }
        if endpoint_url:
            self._client_kwargs["endpoint_url"] = endpoint_url
        if access_key and secret_key:
            self._client_kwargs["aws_access_key_id"] = access_key
            self._client_kwargs["aws_secret_access_key"] = secret_key

        self._session = aioboto3.Session()

        # Sync client for presigned URL generation (aioboto3 doesn't support it).
        sync_kwargs: dict = {
            "region_name": region,
            "service_name": "s3",
        }
        if endpoint_url:
            sync_kwargs["endpoint_url"] = endpoint_url
        if access_key and secret_key:
            sync_kwargs["aws_access_key_id"] = access_key
            sync_kwargs["aws_secret_access_key"] = secret_key
        self._sync_client = boto3.client(**sync_kwargs)

    # ── Helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _normalize(key: str) -> str:
        """Strip leading ``/`` from *key*."""
        return key.lstrip("/")

    def _raise_for_client_error(self, err: Exception, key: str) -> None:
        """Convert a botocore ``ClientError`` to the appropriate storage error.

        Always raises — never returns normally.
        """
        if _boto_exc is not None and isinstance(err, _boto_exc.ClientError):
            response = err.response  # type: ignore[union-attr]
            code = response.get("Error", {}).get("Code", "")
            http_status = response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
            if code in ("NoSuchKey", "404") or http_status == 404:
                raise FileNotFoundError(key=key, backend_name=self.backend_name) from err
            if code in ("AccessDenied", "403") or http_status == 403:
                raise StoragePermissionError(key=key, backend_name=self.backend_name) from err
            raise StorageConnectionError(str(err), key=key, backend_name=self.backend_name) from err

        raise StorageConnectionError(str(err), key=key, backend_name=self.backend_name) from err

    # ── Read ───────────────────────────────────────────────────────────

    async def read(self, key: str) -> str:
        """Read a text file and return its content as a UTF-8 string."""
        normalized = self._normalize(key)
        try:
            async with self._session.client("s3", **self._client_kwargs) as s3:
                resp = await s3.get_object(Bucket=self._bucket, Key=normalized)
                body = await resp["Body"].read()
                return body.decode("utf-8")
        except Exception as exc:
            self._raise_for_client_error(exc, key)
            raise  # unreachable, satisfies type checker

    async def read_bytes(self, key: str) -> bytes:
        """Read a binary file and return raw bytes."""
        normalized = self._normalize(key)
        try:
            async with self._session.client("s3", **self._client_kwargs) as s3:
                resp = await s3.get_object(Bucket=self._bucket, Key=normalized)
                return await resp["Body"].read()
        except Exception as exc:
            self._raise_for_client_error(exc, key)
            raise  # unreachable, satisfies type checker

    # ── Write ──────────────────────────────────────────────────────────

    async def write(self, key: str, content: str) -> None:
        """Write text content to a file (create or overwrite)."""
        normalized = self._normalize(key)
        try:
            async with self._session.client("s3", **self._client_kwargs) as s3:
                await s3.put_object(Bucket=self._bucket, Key=normalized, Body=content.encode("utf-8"))
        except Exception as exc:
            self._raise_for_client_error(exc, key)

    async def write_bytes(self, key: str, content: bytes) -> None:
        """Write binary content to a file (create or overwrite)."""
        normalized = self._normalize(key)
        try:
            async with self._session.client("s3", **self._client_kwargs) as s3:
                await s3.put_object(Bucket=self._bucket, Key=normalized, Body=content)
        except Exception as exc:
            self._raise_for_client_error(exc, key)

    # ── Delete ─────────────────────────────────────────────────────────

    async def delete(self, key: str) -> None:
        """Delete a single file. No-op if the file does not exist."""
        normalized = self._normalize(key)
        try:
            async with self._session.client("s3", **self._client_kwargs) as s3:
                await s3.delete_object(Bucket=self._bucket, Key=normalized)
        except Exception as exc:
            self._raise_for_client_error(exc, key)

    async def delete_prefix(self, prefix: str) -> None:
        """Recursively delete all files under a prefix (directory)."""
        normalized = self._normalize(prefix)
        keys: list[str] = []
        try:
            async with self._session.client("s3", **self._client_kwargs) as s3:
                paginator = s3.get_paginator("list_objects_v2")
                async for page in paginator.paginate(Bucket=self._bucket, Prefix=normalized):
                    for obj in page.get("Contents", []):
                        keys.append(obj["Key"])

                for i in range(0, len(keys), _CHUNK_SIZE):
                    chunk = keys[i : i + _CHUNK_SIZE]
                    await s3.delete_objects(
                        Bucket=self._bucket,
                        Delete={"Objects": [{"Key": k} for k in chunk]},
                    )
        except Exception as exc:
            self._raise_for_client_error(exc, prefix)

    # ── Exists ─────────────────────────────────────────────────────────

    async def exists(self, key: str) -> bool:
        """Check if a file exists."""
        normalized = self._normalize(key)
        try:
            async with self._session.client("s3", **self._client_kwargs) as s3:
                await s3.head_object(Bucket=self._bucket, Key=normalized)
                return True
        except Exception as exc:
            if _boto_exc is not None and isinstance(exc, _boto_exc.ClientError):
                response = exc.response  # type: ignore[union-attr]
                code = response.get("Error", {}).get("Code", "")
                http_status = response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
                if code in ("404", "NoSuchKey") or http_status == 404:
                    return False
            self._raise_for_client_error(exc, key)
            return False  # unreachable, satisfies type checker

    # ── List ───────────────────────────────────────────────────────────

    async def list(self, prefix: str) -> list[FileInfo]:
        """List files and directories directly under a prefix (one level)."""
        normalized = self._normalize(prefix)
        entries: list[FileInfo] = []
        try:
            async with self._session.client("s3", **self._client_kwargs) as s3:
                resp = await s3.list_objects_v2(
                    Bucket=self._bucket,
                    Prefix=normalized,
                    Delimiter="/",
                )

                # Directories (CommonPrefixes)
                for cp in resp.get("CommonPrefixes", []):
                    raw = cp["Prefix"]  # e.g. "path/to/dir/"
                    name = raw.rstrip("/").rsplit("/", 1)[-1]
                    entries.append(
                        FileInfo(
                            name=name,
                            path=raw.rstrip("/"),
                            is_dir=True,
                            size=0,
                            modified_at="",
                        )
                    )

                # Files (Contents)
                for obj in resp.get("Contents", []):
                    raw_key: str = obj["Key"]
                    name = raw_key.rsplit("/", 1)[-1] if "/" in raw_key else raw_key
                    size: int = obj.get("Size", 0)
                    last_modified = obj.get("LastModified")
                    modified_at = last_modified.isoformat() if last_modified else ""
                    entries.append(
                        FileInfo(
                            name=name,
                            path=raw_key,
                            is_dir=False,
                            size=size,
                            modified_at=modified_at,
                        )
                    )
        except Exception as exc:
            self._raise_for_client_error(exc, prefix)

        return entries

    # ── Copy / Move ────────────────────────────────────────────────────

    async def copy(self, src: str, dst: str) -> None:
        """Copy a file from one key to another."""
        src_key = self._normalize(src)
        dst_key = self._normalize(dst)
        try:
            async with self._session.client("s3", **self._client_kwargs) as s3:
                await s3.copy_object(
                    Bucket=self._bucket,
                    CopySource={"Bucket": self._bucket, "Key": src_key},
                    Key=dst_key,
                )
        except Exception as exc:
            self._raise_for_client_error(exc, src)

    async def move(self, src: str, dst: str) -> None:
        """Move a file from one key to another."""
        await self.copy(src, dst)
        await self.delete(src)

    # ── Presigned URL ──────────────────────────────────────────────────

    async def get_presigned_url(self, key: str, expires_in: int = 3600) -> str:
        """Generate a presigned URL for temporary file access."""
        normalized = self._normalize(key)
        try:
            return self._sync_client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": normalized},
                ExpiresIn=expires_in,
            )
        except Exception as exc:
            raise StorageError(str(exc), key=key, backend_name=self.backend_name) from exc

    # ── Health ─────────────────────────────────────────────────────────

    async def health_check(self) -> bool:
        """Verify the storage backend is reachable. Returns True/False, never raises."""
        try:
            async with self._session.client("s3", **self._client_kwargs) as s3:
                await s3.head_bucket(Bucket=self._bucket)
                return True
        except Exception:
            return False
