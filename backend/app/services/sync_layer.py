"""Sync layer — on-demand file sync between OBS and local filesystem.

Provides stateless helper functions for Agent tool functions that need
local file paths when ``STORAGE_BACKEND`` is not ``local``:

- ``ensure_local_file()``  — download a single file from OBS if missing locally
- ``sync_local_to_obs()``  — upload a locally-modified file back to OBS
- ``write_dual()``         — write content to both local filesystem and OBS
- ``sync_dir_to_obs()``   — batch upload a local directory tree to OBS

When ``STORAGE_BACKEND == "local"``, every function is a fast no-op / passthrough.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from loguru import logger

from app.config import get_settings
from app.services.storage.factory import get_storage


def _is_cloud_storage() -> bool:
    return get_settings().STORAGE_BACKEND.lower() != "local"


async def ensure_local_file(
    agent_id: str | uuid.UUID,
    ws: Path,
    rel_path: str,
    *,
    mode: str = "read",
) -> Path:
    """Ensure *rel_path* exists in the local workspace.

    If the file is already present locally the path is returned immediately.
    Otherwise, when ``STORAGE_BACKEND`` is not ``local``, the file is downloaded
    from cloud storage.  On ``local`` backend this is a pure passthrough.

    Args:
        agent_id: Agent UUID (string or uuid.UUID).
        ws: Workspace root directory (``WORKSPACE_ROOT / str(agent_id)``).
        rel_path: File path relative to *ws* (e.g. ``"workspace/report.pdf"``).
        mode: Reserved for future use (default ``"read"``).

    Returns:
        The resolved absolute ``Path`` to the local file.
    """
    local_path = (ws / rel_path).resolve()

    if not _is_cloud_storage():
        return local_path

    if local_path.exists():
        return local_path

    storage_key = f"{agent_id}/{rel_path}"
    try:
        storage = get_storage()
        data = await storage.read_bytes(storage_key)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(data)
        logger.debug(
            f"[SyncLayer] Downloaded {storage_key} -> {local_path} ({len(data)} bytes)"
        )
    except Exception as exc:
        logger.warning(
            f"[SyncLayer] Failed to download {storage_key}: {exc}"
        )

    return local_path


async def sync_local_to_obs(
    agent_id: str | uuid.UUID,
    local_path: Path,
    ws: Path,
) -> None:
    """Upload a locally-modified file back to cloud storage.

    No-op when ``STORAGE_BACKEND`` is ``local``.
    """
    if not _is_cloud_storage():
        return

    try:
        storage = get_storage()
        rel = str(local_path).removeprefix(str(ws.resolve()) + "/")
        if rel == str(local_path):
            try:
                rel = str(local_path.relative_to(ws))
            except ValueError:
                rel = str(local_path.relative_to(ws.resolve()))
        storage_key = f"{agent_id}/{rel}"
        data = local_path.read_bytes()
        await storage.write_bytes(storage_key, data)
        logger.debug(
            f"[SyncLayer] Uploaded {local_path} -> {storage_key} ({len(data)} bytes)"
        )
    except Exception as exc:
        logger.warning(
            f"[SyncLayer] Failed to upload {local_path}: {exc}"
        )


async def write_dual(
    agent_id: str | uuid.UUID,
    ws: Path,
    rel_path: str,
    content: str,
) -> None:
    """Write *content* to both the local filesystem **and** cloud storage.

    The local write always happens.  The cloud write is skipped when
    ``STORAGE_BACKEND`` is ``local``.
    """
    local_path = (ws / rel_path).resolve()
    local_path.parent.mkdir(parents=True, exist_ok=True)

    local_path.write_text(content, encoding="utf-8")

    if _is_cloud_storage():
        try:
            storage = get_storage()
            storage_key = f"{agent_id}/{rel_path}"
            await storage.write(storage_key, content)
            logger.debug(
                f"[SyncLayer] Dual-write {storage_key} ({len(content)} chars)"
            )
        except Exception as exc:
            logger.warning(
                f"[SyncLayer] Failed to dual-write {agent_id}/{rel_path}: {exc}"
            )


async def sync_dir_to_obs(
    agent_id: str | uuid.UUID,
    ws: Path,
    subdir: str = "workspace",
) -> dict[str, int]:
    """Recursively upload all files under *ws/subdir* to cloud storage.

    Files that already exist in cloud storage with the same byte size as the
    local copy are skipped.  Individual upload failures are logged but do not
    abort the remaining uploads.

    Returns a dict with counts: ``{"uploaded": n, "skipped": n, "failed": n}``.
    No-op (returns zeroed stats) when ``STORAGE_BACKEND`` is ``local``.
    """
    stats: dict[str, int] = {"uploaded": 0, "skipped": 0, "failed": 0}

    if not _is_cloud_storage():
        return stats

    local_root = (ws / subdir).resolve()
    if not local_root.is_dir():
        return stats

    storage = get_storage()
    prefix = f"{agent_id}/{subdir}/"

    try:
        remote_entries = await storage.list(prefix)
    except Exception:
        remote_entries = []

    remote_sizes: dict[str, int] = {}
    for entry in remote_entries:
        if not entry.is_dir:
            rel = entry.path.removeprefix(prefix)
            remote_sizes[rel] = entry.size

    for local_file in local_root.rglob("*"):
        if not local_file.is_file():
            continue
        rel = str(local_file.relative_to(local_root))
        local_size = local_file.stat().st_size

        if rel in remote_sizes and remote_sizes[rel] == local_size:
            stats["skipped"] += 1
            continue

        storage_key = f"{prefix}{rel}"
        try:
            data = local_file.read_bytes()
            await storage.write_bytes(storage_key, data)
            stats["uploaded"] += 1
            logger.debug(
                f"[SyncLayer] Uploaded {storage_key} ({len(data)} bytes)"
            )
        except Exception as exc:
            stats["failed"] += 1
            logger.warning(f"[SyncLayer] Failed to upload {storage_key}: {exc}")

    logger.info(
        f"[SyncLayer] sync_dir_to_obs({agent_id}/{subdir}): "
        f"uploaded={stats['uploaded']}, skipped={stats['skipped']}, "
        f"failed={stats['failed']}"
    )
    return stats
