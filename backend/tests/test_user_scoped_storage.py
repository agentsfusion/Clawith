import uuid
import pytest

from unittest.mock import patch, AsyncMock

from app.services.agent_tools import (
    resolve_storage_key,
    resolve_read_key,
    _is_user_scoped_path,
    USER_SCOPED_PREFIXES,
)


def test_is_user_scoped_memory():
    assert _is_user_scoped_path("memory/memory.md") is True
    assert _is_user_scoped_path("memory/reflections.md") is True
    assert _is_user_scoped_path("memory/") is False
    assert _is_user_scoped_path("memory") is False


def test_is_user_scoped_focus():
    assert _is_user_scoped_path("focus.md") is True
    assert _is_user_scoped_path("/focus.md") is True


def test_is_user_scoped_task_history():
    assert _is_user_scoped_path("task_history.md") is True


def test_is_not_user_scoped_soul():
    assert _is_user_scoped_path("soul.md") is False


def test_is_not_user_scoped_workspace():
    assert _is_user_scoped_path("workspace/report.md") is False
    assert _is_user_scoped_path("workspace/") is False


def test_is_not_user_scoped_relationships():
    assert _is_user_scoped_path("relationships.md") is False


def test_is_not_user_scoped_skills():
    assert _is_user_scoped_path("skills/myskill/SKILL.md") is False


def test_is_not_user_scoped_tasks_json():
    assert _is_user_scoped_path("tasks.json") is False


def test_resolve_storage_key_user_scoped():
    agent_id = uuid.uuid4()
    user_id = uuid.uuid4()
    key = resolve_storage_key(agent_id, user_id, "memory/memory.md")
    assert key == f"{agent_id}/users/{user_id}/memory/memory.md"


def test_resolve_storage_key_user_scoped_focus():
    agent_id = uuid.uuid4()
    user_id = uuid.uuid4()
    key = resolve_storage_key(agent_id, user_id, "focus.md")
    assert key == f"{agent_id}/users/{user_id}/focus.md"


def test_resolve_storage_key_global_soul():
    agent_id = uuid.uuid4()
    user_id = uuid.uuid4()
    key = resolve_storage_key(agent_id, user_id, "soul.md")
    assert key == f"{agent_id}/soul.md"


def test_resolve_storage_key_global_workspace():
    agent_id = uuid.uuid4()
    user_id = uuid.uuid4()
    key = resolve_storage_key(agent_id, user_id, "workspace/report.md")
    assert key == f"{agent_id}/workspace/report.md"


def test_resolve_storage_key_no_user_id():
    agent_id = uuid.uuid4()
    key = resolve_storage_key(agent_id, None, "memory/memory.md")
    assert key == f"{agent_id}/memory/memory.md"


def test_resolve_storage_key_no_user_id_focus():
    agent_id = uuid.uuid4()
    key = resolve_storage_key(agent_id, None, "focus.md")
    assert key == f"{agent_id}/focus.md"


def test_resolve_storage_key_strips_leading_slash():
    agent_id = uuid.uuid4()
    user_id = uuid.uuid4()
    key = resolve_storage_key(agent_id, user_id, "/focus.md")
    assert key == f"{agent_id}/users/{user_id}/focus.md"


@pytest.mark.asyncio
async def test_resolve_read_key_user_file_exists():
    agent_id = uuid.uuid4()
    user_id = uuid.uuid4()
    mock_storage = AsyncMock()
    mock_storage.exists = AsyncMock(return_value=True)
    with patch("app.services.agent_tools._storage", return_value=mock_storage):
        key = await resolve_read_key(agent_id, user_id, "memory/memory.md")
    assert key == f"{agent_id}/users/{user_id}/memory/memory.md"


@pytest.mark.asyncio
async def test_resolve_read_key_fallback_to_global():
    agent_id = uuid.uuid4()
    user_id = uuid.uuid4()
    mock_storage = AsyncMock()
    mock_storage.exists = AsyncMock(return_value=False)
    with patch("app.services.agent_tools._storage", return_value=mock_storage):
        key = await resolve_read_key(agent_id, user_id, "memory/memory.md")
    assert key == f"{agent_id}/memory/memory.md"


@pytest.mark.asyncio
async def test_resolve_read_key_fallback_neither_exists():
    agent_id = uuid.uuid4()
    user_id = uuid.uuid4()
    mock_storage = AsyncMock()
    mock_storage.exists = AsyncMock(return_value=False)
    with patch("app.services.agent_tools._storage", return_value=mock_storage):
        key = await resolve_read_key(agent_id, user_id, "memory/memory.md")
    assert key == f"{agent_id}/memory/memory.md"


@pytest.mark.asyncio
async def test_resolve_read_key_global_file_soul():
    agent_id = uuid.uuid4()
    user_id = uuid.uuid4()
    mock_storage = AsyncMock()
    with patch("app.services.agent_tools._storage", return_value=mock_storage):
        key = await resolve_read_key(agent_id, user_id, "soul.md")
    assert key == f"{agent_id}/soul.md"
    mock_storage.exists.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_read_key_no_user_id():
    agent_id = uuid.uuid4()
    mock_storage = AsyncMock()
    with patch("app.services.agent_tools._storage", return_value=mock_storage):
        key = await resolve_read_key(agent_id, None, "memory/memory.md")
    assert key == f"{agent_id}/memory/memory.md"
    mock_storage.exists.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_read_key_strips_leading_slash():
    agent_id = uuid.uuid4()
    user_id = uuid.uuid4()
    mock_storage = AsyncMock()
    mock_storage.exists = AsyncMock(return_value=True)
    with patch("app.services.agent_tools._storage", return_value=mock_storage):
        key = await resolve_read_key(agent_id, user_id, "/focus.md")
    assert key == f"{agent_id}/users/{user_id}/focus.md"
