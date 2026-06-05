"""Replit-managed Object Storage backend (App Storage).

Replit Object Storage is a Google Cloud Storage bucket provisioned and managed
by Replit. Access is authenticated through the Replit sidecar (no HMAC keys),
using GCS "external account" / identity-pool credentials. This adapter talks to
the bucket via the ``google-cloud-storage`` library directly so it can use GCS
object *generations* for atomic conditional writes, which the higher-level
``replit-object-storage`` SDK does not expose.

Object generations give us a strong version token: ``write_bytes_if_match`` and
``delete_if_match`` use ``if_generation_match`` so concurrent writers are
detected atomically on the server side (no read-then-write race), matching the
guarantees the workspace collaboration layer relies on.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from app.services.storage_runtime.base import (
    ConditionalWriteResult,
    StorageBackend,
    StorageEntry,
    StorageVersion,
    WriteCondition,
)
from app.services.storage_runtime.utils import normalize_storage_key

# Replit sidecar endpoints (mirrors replit.object_storage._config). These are
# stable, environment-local URLs injected by the Replit runtime.
_SIDECAR_ENDPOINT = "http://127.0.0.1:1106"
_CREDENTIAL_URL = _SIDECAR_ENDPOINT + "/credential"
_DEFAULT_BUCKET_URL = _SIDECAR_ENDPOINT + "/object-storage/default-bucket"
_TOKEN_URL = _SIDECAR_ENDPOINT + "/token"

_REPLIT_ADC: dict[str, Any] = {
    "audience": "replit",
    "subject_token_type": "access_token",
    "token_url": _TOKEN_URL,
    "credential_source": {
        "url": _CREDENTIAL_URL,
        "format": {
            "type": "json",
            "subject_token_field_name": "access_token",
        },
    },
}


class ReplitObjectStorageBackend(StorageBackend):
    def __init__(
        self,
        *,
        bucket_id: str = "",
        prefix: str = "",
    ):
        self.bucket_id = bucket_id or None
        self.prefix = normalize_storage_key(prefix)
        self._bucket: Any | None = None

    def _object_key(self, key: str) -> str:
        normalized = normalize_storage_key(key)
        return f"{self.prefix}/{normalized}" if self.prefix else normalized

    def _bucket_or_raise(self):
        if self._bucket is None:
            try:
                from google.auth import identity_pool
                from google.cloud import storage
            except ImportError as exc:
                raise RuntimeError(
                    "google-cloud-storage (via replit-object-storage) is required "
                    "for the Replit object storage backend"
                ) from exc
            creds = identity_pool.Credentials(**_REPLIT_ADC)
            client = storage.Client(credentials=creds, project="")
            bucket_id = self.bucket_id or self._resolve_default_bucket_id()
            self._bucket = client.bucket(bucket_id)
        return self._bucket

    @staticmethod
    def _resolve_default_bucket_id() -> str:
        import requests

        response = requests.get(_DEFAULT_BUCKET_URL, timeout=10)
        response.raise_for_status()
        bucket_id = response.json().get("bucketId", "")
        if not bucket_id:
            raise RuntimeError(
                "No default Replit object storage bucket is configured. "
                "Provision App Storage or set REPLIT_OBJECT_STORAGE_BUCKET_ID."
            )
        return bucket_id

    def _blob(self, key: str):
        return self._bucket_or_raise().blob(self._object_key(key))

    async def exists(self, key: str) -> bool:
        blob = self._blob(key)
        return await asyncio.to_thread(blob.exists)

    async def is_file(self, key: str) -> bool:
        return await self.exists(key)

    async def is_dir(self, key: str) -> bool:
        prefix = self._object_key(key).rstrip("/") + "/"
        names = await asyncio.to_thread(self._list_names, prefix, 1)
        return bool(names)

    def _list_names(self, prefix: str, max_results: int | None) -> list[str]:
        bucket = self._bucket_or_raise()
        return [blob.name for blob in bucket.list_blobs(prefix=prefix, max_results=max_results)]

    async def list_dir(self, key: str) -> list[StorageEntry]:
        prefix = self._object_key(key).rstrip("/")
        if prefix:
            prefix += "/"
        return await asyncio.to_thread(self._list_dir_sync, prefix)

    def _list_dir_sync(self, prefix: str) -> list[StorageEntry]:
        bucket = self._bucket_or_raise()
        iterator = bucket.list_blobs(prefix=prefix, delimiter="/")
        blobs = list(iterator)
        entries: list[StorageEntry] = []
        for sub_prefix in (iterator.prefixes or []):
            raw = sub_prefix.rstrip("/")
            rel = _strip_prefix(raw, self.prefix)
            entries.append(StorageEntry(name=rel.split("/")[-1], key=rel, is_dir=True))
        for blob in blobs:
            raw = blob.name or ""
            if not raw or raw == prefix:
                continue
            rel = _strip_prefix(raw, self.prefix)
            entries.append(
                StorageEntry(
                    name=rel.split("/")[-1],
                    key=rel,
                    is_dir=False,
                    size=int(blob.size or 0),
                    modified_at=str(blob.updated or ""),
                    etag=str(blob.etag or ""),
                    version_id=str(blob.generation or ""),
                )
            )
        return sorted(entries, key=lambda entry: (not entry.is_dir, entry.name))

    async def read_bytes(self, key: str) -> bytes:
        blob = self._blob(key)
        try:
            return await asyncio.to_thread(blob.download_as_bytes)
        except Exception as exc:
            if _is_not_found(exc):
                raise FileNotFoundError(key) from exc
            raise

    async def write_bytes(self, key: str, data: bytes, content_type: str | None = None) -> None:
        blob = self._blob(key)
        resolved_ct = content_type or "application/octet-stream"
        await asyncio.to_thread(blob.upload_from_string, data, content_type=resolved_ct)

    async def delete(self, key: str) -> None:
        bucket = self._bucket_or_raise()
        blob = bucket.blob(self._object_key(key))
        await asyncio.to_thread(_delete_blob, blob)

    async def delete_tree(self, key: str) -> None:
        prefix = self._object_key(key).rstrip("/") + "/"
        await asyncio.to_thread(self._delete_tree_sync, prefix)

    def _delete_tree_sync(self, prefix: str) -> None:
        bucket = self._bucket_or_raise()
        for blob in bucket.list_blobs(prefix=prefix):
            _delete_blob(blob)

    async def stat(self, key: str) -> StorageEntry:
        version = await self.get_version(key)
        if not version.exists:
            raise FileNotFoundError(key)
        return StorageEntry(
            name=normalize_storage_key(key).split("/")[-1],
            key=normalize_storage_key(key),
            is_dir=version.is_dir,
            size=version.size,
            modified_at=version.modified_at,
            etag=version.etag,
            version_id=version.version_id,
            content_hash=version.content_hash,
        )

    async def get_version(self, key: str) -> StorageVersion:
        bucket = self._bucket_or_raise()
        blob = await asyncio.to_thread(bucket.get_blob, self._object_key(key))
        if blob is None:
            return StorageVersion(key=normalize_storage_key(key), exists=False, is_dir=False)
        content_hash = str(blob.md5_hash or "")
        return StorageVersion(
            key=normalize_storage_key(key),
            exists=True,
            is_dir=False,
            size=int(blob.size or 0),
            modified_at=str(blob.updated or ""),
            etag=str(blob.etag or ""),
            version_id=str(blob.generation or ""),
            content_hash=content_hash,
        )

    async def write_bytes_if_match(
        self,
        key: str,
        data: bytes,
        *,
        condition: WriteCondition | None = None,
        content_type: str | None = None,
    ) -> ConditionalWriteResult:
        generation = _generation_for_condition(condition)
        if condition is not None and generation is None and condition.version_token is not None:
            # Non-numeric token (e.g. produced by another backend): fall back to
            # the read-then-compare semantics provided by the base class.
            return await super().write_bytes_if_match(
                key, data, condition=condition, content_type=content_type
            )
        blob = self._blob(key)
        resolved_ct = content_type or "application/octet-stream"
        try:
            await asyncio.to_thread(
                blob.upload_from_string,
                data,
                content_type=resolved_ct,
                if_generation_match=generation,
            )
        except Exception as exc:
            if _is_precondition_failed(exc):
                return ConditionalWriteResult(
                    ok=False, conflict=True, current_version=await self.get_version(key)
                )
            raise
        return ConditionalWriteResult(ok=True, current_version=await self.get_version(key))

    async def delete_if_match(
        self,
        key: str,
        *,
        condition: WriteCondition | None = None,
    ) -> ConditionalWriteResult:
        if condition is not None and condition.require_absent:
            current = await self.get_version(key)
            if current.exists:
                return ConditionalWriteResult(ok=False, conflict=True, current_version=current)
            return ConditionalWriteResult(ok=True, current_version=current)
        generation = _generation_for_condition(condition)
        if condition is not None and generation is None and condition.version_token is not None:
            return await super().delete_if_match(key, condition=condition)
        bucket = self._bucket_or_raise()
        blob = bucket.blob(self._object_key(key))
        try:
            await asyncio.to_thread(blob.delete, if_generation_match=generation)
        except Exception as exc:
            if _is_not_found(exc):
                # Object is gone. With a version-token precondition (numeric
                # generation) this means the expected version no longer exists, so
                # it is a conflict — matching the base read-then-compare semantics.
                # Without a precondition, deletion is idempotent and succeeds.
                if generation is not None:
                    return ConditionalWriteResult(
                        ok=False, conflict=True, current_version=await self.get_version(key)
                    )
                return ConditionalWriteResult(ok=True, current_version=await self.get_version(key))
            if _is_precondition_failed(exc):
                return ConditionalWriteResult(
                    ok=False, conflict=True, current_version=await self.get_version(key)
                )
            raise
        return ConditionalWriteResult(ok=True, current_version=await self.get_version(key))

    async def local_path_for(self, key: str) -> Path | None:
        suffix = Path(normalize_storage_key(key)).suffix
        tmp = NamedTemporaryFile(delete=False, suffix=suffix)
        tmp.close()
        path = Path(tmp.name)
        await self.write_local_copy(key, path)
        return path

    async def write_local_copy(self, key: str, path: Path) -> None:
        data = await self.read_bytes(key)
        await asyncio.to_thread(path.write_bytes, data)

    async def presign_download_url(
        self, key: str, filename: str | None = None, inline: bool = False
    ) -> str | None:
        # Sidecar identity-pool credentials cannot sign URLs (no private key), so
        # downloads stream through the app via local_path_for instead.
        return None


def _strip_prefix(raw_key: str, prefix: str) -> str:
    if prefix and raw_key.startswith(prefix + "/"):
        return raw_key[len(prefix) + 1:]
    return raw_key


def _generation_for_condition(condition: WriteCondition | None) -> int | None:
    """Map a write condition to a GCS ``if_generation_match`` value.

    Returns 0 to require the object be absent, the parsed generation for a
    version token, or None when there is no generation precondition to apply.
    """
    if condition is None:
        return None
    if condition.require_absent:
        return 0
    if condition.version_token is None:
        return None
    try:
        return int(condition.version_token)
    except (TypeError, ValueError):
        return None


def _delete_blob(blob) -> None:
    try:
        blob.delete()
    except Exception as exc:
        if _is_not_found(exc):
            return
        raise


def _is_not_found(exc: Exception) -> bool:
    try:
        from google.cloud.exceptions import NotFound
    except Exception:
        return False
    return isinstance(exc, NotFound)


def _is_precondition_failed(exc: Exception) -> bool:
    try:
        from google.api_core.exceptions import PreconditionFailed
    except Exception:
        return False
    return isinstance(exc, PreconditionFailed)
