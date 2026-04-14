import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.scripts.migrate_user_memory import (
    _copy_if_exists,
    dry_run,
    migrate_all,
    migrate_creator_only,
    rollback,
)


def _make_agent(agent_id: uuid.UUID, name: str = "TestAgent", creator_id: uuid.UUID | None = None):
    agent = MagicMock()
    agent.id = agent_id
    agent.name = name
    agent.creator_id = creator_id
    return agent


@pytest.mark.asyncio
class TestCopyIfExists:
    async def test_copies_when_src_exists_dst_missing(self):
        storage = AsyncMock()
        storage.exists = AsyncMock(side_effect=lambda key: "src" in key)
        storage.read = AsyncMock(return_value="content")

        result = await _copy_if_exists(storage, "src_key", "dst_key")

        assert result is True
        storage.read.assert_awaited_once_with("src_key")
        storage.write.assert_awaited_once_with("dst_key", "content")

    async def test_dst_already_exists_no_copy(self):
        storage = AsyncMock()
        storage.exists = AsyncMock(return_value=True)

        result = await _copy_if_exists(storage, "src_key", "dst_key")

        assert result is False
        storage.read.assert_not_awaited()
        storage.write.assert_not_awaited()

    async def test_src_missing_no_copy(self):
        storage = AsyncMock()
        storage.exists = AsyncMock(side_effect=lambda key: "src" not in key)

        result = await _copy_if_exists(storage, "src_key", "dst_key")

        assert result is False
        storage.read.assert_not_awaited()
        storage.write.assert_not_awaited()


@pytest.mark.asyncio
class TestMigrateCreatorOnly:
    @patch("app.scripts.migrate_user_memory.get_storage")
    @patch("app.scripts.migrate_user_memory.get_all_agents", new_callable=AsyncMock)
    async def test_migrate_creator_only(self, mock_get_all, mock_get_storage):
        agent_id = uuid.uuid4()
        creator_id = uuid.uuid4()
        agent = _make_agent(agent_id, "MyAgent", creator_id=creator_id)
        mock_get_all.return_value = [agent]

        storage = AsyncMock()
        storage.exists = AsyncMock(side_effect=lambda key: "/users/" not in key)
        storage.read = AsyncMock(return_value="focus-content")
        mock_get_storage.return_value = storage

        await migrate_creator_only()

        assert storage.write.await_count >= 1
        storage.write.assert_any_await(
            f"{agent_id}/users/{creator_id}/focus.md", "focus-content"
        )


@pytest.mark.asyncio
class TestMigrateAll:
    @patch("app.scripts.migrate_user_memory.get_agent_user_pairs", new_callable=AsyncMock)
    @patch("app.scripts.migrate_user_memory.get_all_agents", new_callable=AsyncMock)
    @patch("app.scripts.migrate_user_memory.get_storage")
    async def test_migrate_all(self, mock_get_storage, mock_get_all, mock_get_pairs):
        agent_id = uuid.uuid4()
        user1 = uuid.uuid4()
        user2 = uuid.uuid4()
        agent = _make_agent(agent_id, "Agent1")
        mock_get_all.return_value = [agent]
        mock_get_pairs.return_value = [(agent_id, user1), (agent_id, user2)]

        storage = AsyncMock()
        storage.exists = AsyncMock(side_effect=lambda key: "/users/" not in key)
        storage.read = AsyncMock(return_value="data")
        mock_get_storage.return_value = storage

        await migrate_all()

        assert storage.write.await_count >= 1


@pytest.mark.asyncio
class TestRollback:
    @patch("app.scripts.migrate_user_memory.get_all_agents", new_callable=AsyncMock)
    @patch("app.scripts.migrate_user_memory.get_storage")
    async def test_rollback(self, mock_get_storage, mock_get_all):
        agent_id = uuid.uuid4()
        agent = _make_agent(agent_id, "Agent1")
        mock_get_all.return_value = [agent]

        storage = AsyncMock()
        storage.list = AsyncMock(return_value=["item1", "item2"])
        mock_get_storage.return_value = storage

        await rollback()

        storage.delete_prefix.assert_awaited_once_with(f"{agent_id}/users/")


@pytest.mark.asyncio
class TestDryRun:
    @patch("app.scripts.migrate_user_memory.get_agent_user_pairs", new_callable=AsyncMock)
    @patch("app.scripts.migrate_user_memory.get_all_agents", new_callable=AsyncMock)
    @patch("app.scripts.migrate_user_memory.get_storage")
    async def test_dry_run_no_writes(self, mock_get_storage, mock_get_all, mock_get_pairs):
        agent_id = uuid.uuid4()
        user_id = uuid.uuid4()
        agent = _make_agent(agent_id, "Agent1")
        mock_get_all.return_value = [agent]
        mock_get_pairs.return_value = [(agent_id, user_id)]

        storage = AsyncMock()
        storage.exists = AsyncMock(return_value=True)
        mock_get_storage.return_value = storage

        await dry_run()

        storage.write.assert_not_awaited()
