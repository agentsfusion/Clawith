"""Integration tests for user-scoped memory isolation.

These tests exercise the full storage stack (LocalStorageBackend + resolve_storage_key
+ resolve_read_key) to verify end-to-end user memory isolation — not just the pure
logic functions tested in test_user_scoped_storage.py.
"""

import uuid
import pytest
from unittest.mock import patch

from app.services.agent_tools import resolve_storage_key, resolve_read_key
from app.services.storage.local import LocalStorageBackend
from app.scripts.migrate_user_memory import _copy_if_exists


@pytest.mark.asyncio
async def test_two_users_isolated_memory(tmp_path):
    """Two different users interacting with same Agent have separate memory files."""
    storage = LocalStorageBackend(str(tmp_path))
    agent_id = uuid.uuid4()
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()

    # Write different memory for each user
    key_a = resolve_storage_key(agent_id, user_a, "memory/memory.md")
    key_b = resolve_storage_key(agent_id, user_b, "memory/memory.md")
    await storage.write(key_a, "User A memory")
    await storage.write(key_b, "User B memory")

    # Patch _storage to use our tmp_path storage
    with patch("app.services.agent_tools._storage", return_value=storage):
        read_a = await resolve_read_key(agent_id, user_a, "memory/memory.md")
        read_b = await resolve_read_key(agent_id, user_b, "memory/memory.md")

    assert await storage.read(read_a) == "User A memory"
    assert await storage.read(read_b) == "User B memory"
    assert read_a != read_b


@pytest.mark.asyncio
async def test_new_user_fallback_to_shared(tmp_path):
    """New user without user-scoped file falls back to shared global file."""
    storage = LocalStorageBackend(str(tmp_path))
    agent_id = uuid.uuid4()
    new_user = uuid.uuid4()

    # Write global memory
    await storage.write(f"{agent_id}/memory/memory.md", "shared content")

    # resolve_read_key for new user should fallback to global
    with patch("app.services.agent_tools._storage", return_value=storage):
        key = await resolve_read_key(agent_id, new_user, "memory/memory.md")

    assert key == f"{agent_id}/memory/memory.md"
    assert await storage.read(key) == "shared content"


@pytest.mark.asyncio
async def test_trigger_writes_to_creator_space(tmp_path):
    """Autonomous trigger sessions write to agent creator's user space."""
    storage = LocalStorageBackend(str(tmp_path))
    agent_id = uuid.uuid4()
    creator_id = uuid.uuid4()

    # Write global memory first
    await storage.write(f"{agent_id}/memory/memory.md", "old shared")

    # Trigger writes using creator_id (simulating what trigger_daemon does)
    write_key = resolve_storage_key(agent_id, creator_id, "memory/memory.md")
    await storage.write(write_key, "trigger updated memory")

    # Creator's space has new content
    assert await storage.read(write_key) == "trigger updated memory"
    # Global is untouched
    assert await storage.read(f"{agent_id}/memory/memory.md") == "old shared"


@pytest.mark.asyncio
async def test_file_api_user_specific_data(tmp_path):
    """Verify file API pattern resolves user-specific content for memory files but global for soul."""
    storage = LocalStorageBackend(str(tmp_path))
    agent_id = uuid.uuid4()
    user_id = uuid.uuid4()

    # Write user-specific memory
    user_mem_key = resolve_storage_key(agent_id, user_id, "memory/memory.md")
    await storage.write(user_mem_key, "user-specific memory")
    # Write global soul
    await storage.write(f"{agent_id}/soul.md", "global soul")

    with patch("app.services.agent_tools._storage", return_value=storage):
        mem_key = await resolve_read_key(agent_id, user_id, "memory/memory.md")
        soul_key = await resolve_read_key(agent_id, user_id, "soul.md")

    assert "users" in mem_key
    assert await storage.read(mem_key) == "user-specific memory"
    assert "users" not in soul_key
    assert soul_key == f"{agent_id}/soul.md"


@pytest.mark.asyncio
async def test_migration_preserves_shared_files(tmp_path):
    """Migration copies to user dirs but doesn't delete shared originals."""
    storage = LocalStorageBackend(str(tmp_path))
    await storage.write("agent-123/memory/memory.md", "shared content")
    copied = await _copy_if_exists(
        storage, "agent-123/memory/memory.md", "agent-123/users/user-456/memory/memory.md"
    )
    assert copied is True
    assert await storage.read("agent-123/memory/memory.md") == "shared content"
    assert await storage.read("agent-123/users/user-456/memory/memory.md") == "shared content"
