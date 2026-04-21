import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.models.agent import Agent, AgentPermission
from app.models.participant import Participant
from app.models.tool import AgentTool
from app.services.agent_manager import AgentManager


def _make_agent(**overrides) -> Agent:
    defaults = dict(
        name="Source Agent",
        role_description="Test role",
        bio="Test bio",
        avatar_url="https://example.com/avatar.png",
        welcome_message="Hello!",
        creator_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        agent_type="native",
        primary_model_id=uuid.uuid4(),
        fallback_model_id=uuid.uuid4(),
        autonomy_policy={"read_files": "L1"},
        max_tokens_per_day=50000,
        max_tokens_per_month=1000000,
        context_window_size=100,
        max_tool_rounds=50,
        heartbeat_enabled=True,
        heartbeat_interval_minutes=240,
        heartbeat_active_hours="09:00-18:00",
        timezone="Asia/Shanghai",
        status="running",
    )
    defaults.update(overrides)
    return Agent(**defaults)


class _FakeStorage:
    def __init__(self):
        self.files: dict[str, str] = {}

    async def exists(self, key: str) -> bool:
        return key in self.files

    async def read(self, key: str) -> str:
        return self.files.get(key, "")

    async def write(self, key: str, content: str) -> None:
        self.files[key] = content

    async def list(self, prefix: str) -> list:
        return [
            SimpleNamespace(path=k, is_dir=False)
            for k in self.files if k.startswith(prefix)
        ]


class _FakeDB:
    def __init__(self):
        self.added: list = []
        self.flush_count = 0

    def begin_nested(self):
        return _NestedTx()

    async def execute(self, statement=None, params=None):
        return _FakeResult([])

    async def flush(self):
        for obj in self.added:
            if hasattr(obj, 'id') and getattr(obj, 'id') is None:
                obj.id = uuid.uuid4()
        self.flush_count += 1

    async def commit(self):
        pass

    def add(self, obj):
        self.added.append(obj)


class _NestedTx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _FakeResult:
    def __init__(self, values):
        self._values = list(values)

    def scalar_one_or_none(self):
        return self._values[0] if self._values else None

    def scalars(self):
        return self

    def all(self):
        return list(self._values)


def _by_type(db: _FakeDB, cls: type) -> list:
    return [o for o in db.added if isinstance(o, cls)]


def _patches():
    return [
        patch("app.services.agent_manager.get_storage", return_value=_FakeStorage()),
        patch("app.services.agent_manager._collect_storage_keys", return_value=[]),
        patch.object(AgentManager, "initialize_agent_files", new_callable=AsyncMock),
        patch.object(AgentManager, "start_container", new_callable=AsyncMock),
        patch("app.services.quota_guard.check_agent_creation_quota", new_callable=AsyncMock),
    ]


@pytest.mark.asyncio
async def test_clone_copies_config_fields():
    from app.services.agent_manager import AgentManager

    source = _make_agent()
    cloner = SimpleNamespace(
        id=uuid.uuid4(), tenant_id=source.tenant_id,
        quota_agent_ttl_hours=72, quota_max_agents=5, role="member",
    )
    db = _FakeDB()
    storage = _FakeStorage()
    storage.files[f"{source.id}/soul.md"] = "# Personality\nI am a test agent."

    patches = _patches()
    patches[0] = patch("app.services.agent_manager.get_storage", return_value=storage)
    patches[1] = patch("app.services.agent_manager._collect_storage_keys", side_effect=lambda p: [
        k for k in storage.files if k.startswith(p)
    ])
    for p in patches:
        p.start()
    try:
        mgr = AgentManager.__new__(AgentManager)
        mgr.docker_client = None
        result = await mgr.clone_agent(db, source, cloner, "Cloned Agent")
    finally:
        for p in patches:
            p.stop()

    assert result.name == "Cloned Agent"
    assert result.role_description == source.role_description
    assert result.bio == source.bio
    assert result.avatar_url == source.avatar_url
    assert result.welcome_message == source.welcome_message
    assert result.agent_type == source.agent_type
    assert result.primary_model_id == source.primary_model_id
    assert result.fallback_model_id == source.fallback_model_id
    assert result.autonomy_policy == source.autonomy_policy
    assert result.max_tokens_per_day == source.max_tokens_per_day
    assert result.max_tokens_per_month == source.max_tokens_per_month
    assert result.heartbeat_enabled == source.heartbeat_enabled
    assert result.heartbeat_active_hours == source.heartbeat_active_hours
    assert result.timezone == source.timezone


@pytest.mark.asyncio
async def test_clone_resets_runtime_and_sets_ownership():
    from app.services.agent_manager import AgentManager

    source = _make_agent()
    cloner = SimpleNamespace(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(),
        quota_agent_ttl_hours=72, quota_max_agents=5, role="member",
    )
    db = _FakeDB()

    for p in _patches():
        p.start()
    try:
        mgr = AgentManager.__new__(AgentManager)
        mgr.docker_client = None
        result = await mgr.clone_agent(db, source, cloner, "Cloned Agent")
    finally:
        for p in _patches():
            p.stop()

    assert result.source_agent_id == source.id
    assert result.creator_id == cloner.id
    assert result.tenant_id == cloner.tenant_id
    assert result.status == "creating"
    assert result.container_id is None
    assert result.api_key_hash is None
    assert result.tokens_used_today in (None, 0)
    assert result.tokens_used_month in (None, 0)
    assert result.tokens_used_total in (None, 0)
    assert result.llm_calls_today in (None, 0)
    assert result.is_expired in (None, False)


@pytest.mark.asyncio
async def test_clone_creates_participant_and_permission():
    from app.services.agent_manager import AgentManager

    source = _make_agent()
    cloner = SimpleNamespace(id=uuid.uuid4(), tenant_id=source.tenant_id, quota_agent_ttl_hours=48, quota_max_agents=5, role="member")
    db = _FakeDB()

    for p in _patches():
        p.start()
    try:
        mgr = AgentManager.__new__(AgentManager)
        mgr.docker_client = None
        result = await mgr.clone_agent(db, source, cloner, "Cloned Agent")
    finally:
        for p in _patches():
            p.stop()

    participants = _by_type(db, Participant)
    assert len(participants) == 1
    assert participants[0].type == "agent"
    assert participants[0].ref_id == result.id
    assert participants[0].display_name == "Cloned Agent"

    perms = _by_type(db, AgentPermission)
    assert len(perms) == 1
    assert perms[0].agent_id == result.id
    assert perms[0].scope_type == "company"
    assert perms[0].access_level == "use"


@pytest.mark.asyncio
async def test_clone_copies_tool_assignments():
    from app.services.agent_manager import AgentManager

    source = _make_agent()
    cloner = SimpleNamespace(id=uuid.uuid4(), tenant_id=source.tenant_id, quota_agent_ttl_hours=48, quota_max_agents=5, role="member")

    tool_1 = SimpleNamespace(tool_id=uuid.uuid4(), enabled=True, config={"k": "v"}, source="system", installed_by_agent_id=uuid.uuid4())
    tool_2 = SimpleNamespace(tool_id=uuid.uuid4(), enabled=False, config={}, source="user_installed", installed_by_agent_id=None)

    class ToolDB(_FakeDB):
        async def execute(self, statement=None, params=None):
            if "agent_tools" in str(statement).lower():
                return _FakeResult([tool_1, tool_2])
            return _FakeResult([])

    db = ToolDB()

    for p in _patches():
        p.start()
    try:
        mgr = AgentManager.__new__(AgentManager)
        mgr.docker_client = None
        result = await mgr.clone_agent(db, source, cloner, "Cloned Agent")
    finally:
        for p in _patches():
            p.stop()

    tools = _by_type(db, AgentTool)
    assert len(tools) == 2
    for t in tools:
        assert t.agent_id == result.id
        assert t.installed_by_agent_id is None
    assert tools[0].tool_id == tool_1.tool_id
    assert tools[0].enabled is True
    assert tools[1].tool_id == tool_2.tool_id


@pytest.mark.asyncio
async def test_clone_rejects_openclaw():
    from fastapi import HTTPException
    from app.services.agent_manager import AgentManager

    source = _make_agent(agent_type="openclaw")
    cloner = SimpleNamespace(id=uuid.uuid4(), tenant_id=source.tenant_id, quota_agent_ttl_hours=48, quota_max_agents=5, role="member")
    db = _FakeDB()

    mgr = AgentManager.__new__(AgentManager)
    mgr.docker_client = None
    with pytest.raises(HTTPException) as exc_info:
        await mgr.clone_agent(db, source, cloner, "Clone")
    assert exc_info.value.status_code == 400
    assert "openclaw" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_clone_source_agent_id_set():
    from app.services.agent_manager import AgentManager

    source = _make_agent()
    cloner = SimpleNamespace(id=uuid.uuid4(), tenant_id=source.tenant_id, quota_agent_ttl_hours=48, quota_max_agents=5, role="member")
    db = _FakeDB()

    for p in _patches():
        p.start()
    try:
        mgr = AgentManager.__new__(AgentManager)
        mgr.docker_client = None
        result = await mgr.clone_agent(db, source, cloner, "Cloned Agent")
    finally:
        for p in _patches():
            p.stop()

    assert result.source_agent_id == source.id


@pytest.mark.asyncio
async def test_clone_does_not_copy_relationships_triggers_channels():
    from app.services.agent_manager import AgentManager
    from app.models.org import AgentAgentRelationship, AgentRelationship
    from app.models.trigger import AgentTrigger
    from app.models.channel_config import ChannelConfig

    source = _make_agent()
    cloner = SimpleNamespace(id=uuid.uuid4(), tenant_id=source.tenant_id, quota_agent_ttl_hours=48, quota_max_agents=5, role="member")
    db = _FakeDB()

    for p in _patches():
        p.start()
    try:
        mgr = AgentManager.__new__(AgentManager)
        mgr.docker_client = None
        await mgr.clone_agent(db, source, cloner, "Cloned Agent")
    finally:
        for p in _patches():
            p.stop()

    rels = [o for o in db.added if isinstance(o, (AgentAgentRelationship, AgentRelationship))]
    triggers = [o for o in db.added if isinstance(o, AgentTrigger)]
    channels = [o for o in db.added if isinstance(o, ChannelConfig)]

    assert len(rels) == 0
    assert len(triggers) == 0
    assert len(channels) == 0


@pytest.mark.asyncio
async def test_clone_expires_from_cloner_quota():
    from app.services.agent_manager import AgentManager
    from datetime import datetime, timezone

    source = _make_agent()
    cloner = SimpleNamespace(id=uuid.uuid4(), tenant_id=source.tenant_id, quota_agent_ttl_hours=72, quota_max_agents=5, role="member")
    db = _FakeDB()

    for p in _patches():
        p.start()
    try:
        mgr = AgentManager.__new__(AgentManager)
        mgr.docker_client = None
        result = await mgr.clone_agent(db, source, cloner, "Cloned Agent")
    finally:
        for p in _patches():
            p.stop()

    assert result.expires_at is not None
    delta = (result.expires_at - datetime.now(timezone.utc)).total_seconds() / 3600
    assert 71 <= delta <= 73


@pytest.mark.asyncio
async def test_clone_selective_copy_only_soul():
    source = _make_agent()
    cloner = SimpleNamespace(id=uuid.uuid4(), tenant_id=source.tenant_id, quota_agent_ttl_hours=48, quota_max_agents=5, role="member")
    db = _FakeDB()

    storage = _FakeStorage()
    storage.files[f"{source.id}/soul.md"] = "# Personality\nI am a test agent."
    storage.files[f"{source.id}/memory/memory.md"] = "# Memory\nSecret info"
    storage.files[f"{source.id}/skills/test.md"] = "# Skill\nDo stuff"

    patches = _patches()
    patches[0] = patch("app.services.agent_manager.get_storage", return_value=storage)
    patches[1] = patch("app.services.agent_manager._collect_storage_keys", side_effect=lambda p: [
        k for k in storage.files if k.startswith(p)
    ])
    for p in patches:
        p.start()
    try:
        mgr = AgentManager.__new__(AgentManager)
        mgr.docker_client = None
        result = await mgr.clone_agent(db, source, cloner, "Cloned Agent", copy_files=["soul.md"])
    finally:
        for p in patches:
            p.stop()

    dst_prefix = str(result.id)
    assert f"{dst_prefix}/soul.md" in storage.files
    assert storage.files[f"{dst_prefix}/soul.md"] == "# Personality\nI am a test agent."
    assert not any(k.startswith(f"{dst_prefix}/memory/") for k in storage.files)
    assert not any(k.startswith(f"{dst_prefix}/skills/") for k in storage.files)


@pytest.mark.asyncio
async def test_clone_rejects_memory_in_copy_files():
    """Memory is no longer a valid copy_files category - always initialized fresh."""
    from fastapi import HTTPException

    source = _make_agent()
    cloner = SimpleNamespace(id=uuid.uuid4(), tenant_id=source.tenant_id, quota_agent_ttl_hours=48, quota_max_agents=5, role="member")
    db = _FakeDB()

    mgr = AgentManager.__new__(AgentManager)
    mgr.docker_client = None
    with pytest.raises(HTTPException) as exc_info:
        await mgr.clone_agent(db, source, cloner, "Clone", copy_files=["memory", "workspace"])
    assert exc_info.value.status_code == 422
    assert "memory" in exc_info.value.detail


@pytest.mark.asyncio
async def test_clone_copy_files_default_includes_all_except_workspace():
    source = _make_agent()
    cloner = SimpleNamespace(id=uuid.uuid4(), tenant_id=source.tenant_id, quota_agent_ttl_hours=48, quota_max_agents=5, role="member")
    db = _FakeDB()

    storage = _FakeStorage()
    storage.files[f"{source.id}/soul.md"] = "# Soul"
    storage.files[f"{source.id}/memory/memory.md"] = "# Memory"
    storage.files[f"{source.id}/skills/test.md"] = "# Skill"
    storage.files[f"{source.id}/HEARTBEAT.md"] = "# Heartbeat"
    storage.files[f"{source.id}/workspace/doc.md"] = "# Workspace doc"

    patches = _patches()
    patches[0] = patch("app.services.agent_manager.get_storage", return_value=storage)
    patches[1] = patch("app.services.agent_manager._collect_storage_keys", side_effect=lambda p: [
        k for k in storage.files if k.startswith(p)
    ])
    for p in patches:
        p.start()
    try:
        mgr = AgentManager.__new__(AgentManager)
        mgr.docker_client = None
        result = await mgr.clone_agent(db, source, cloner, "Cloned Agent")
    finally:
        for p in patches:
            p.stop()

    dst_prefix = str(result.id)
    assert f"{dst_prefix}/soul.md" in storage.files
    assert not any(k.startswith(f"{dst_prefix}/memory/") for k in storage.files)
    assert f"{dst_prefix}/skills/test.md" in storage.files
    assert f"{dst_prefix}/HEARTBEAT.md" in storage.files
    assert not any(k.startswith(f"{dst_prefix}/workspace/") for k in storage.files)


@pytest.mark.asyncio
async def test_clone_rejects_invalid_copy_files():
    from fastapi import HTTPException

    source = _make_agent()
    cloner = SimpleNamespace(id=uuid.uuid4(), tenant_id=source.tenant_id, quota_agent_ttl_hours=48, quota_max_agents=5, role="member")
    db = _FakeDB()

    mgr = AgentManager.__new__(AgentManager)
    mgr.docker_client = None
    with pytest.raises(HTTPException) as exc_info:
        await mgr.clone_agent(db, source, cloner, "Clone", copy_files=["soul.md", "invalid_category"])
    assert exc_info.value.status_code == 422
    assert "invalid_category" in exc_info.value.detail


@pytest.mark.asyncio
async def test_clone_memory_always_fresh():
    """Cloned agent never gets source agent's memory files — initialize_agent_files provides fresh defaults."""
    from app.services.agent_manager import AgentManager

    source = _make_agent()
    cloner = SimpleNamespace(id=uuid.uuid4(), tenant_id=source.tenant_id, quota_agent_ttl_hours=48, quota_max_agents=5, role="member")
    db = _FakeDB()

    storage = _FakeStorage()
    storage.files[f"{source.id}/memory/memory.md"] = "# Memory\nAccumulated knowledge over months"
    storage.files[f"{source.id}/memory/reflections.md"] = "# Reflections\nDeep insights"
    storage.files[f"{source.id}/soul.md"] = "# Soul"
    storage.files[f"{source.id}/skills/test.md"] = "# Skill"
    storage.files[f"{source.id}/HEARTBEAT.md"] = "# HB"

    patches = _patches()
    patches[0] = patch("app.services.agent_manager.get_storage", return_value=storage)
    patches[1] = patch("app.services.agent_manager._collect_storage_keys", side_effect=lambda p: [
        k for k in storage.files if k.startswith(p)
    ])
    for p in patches:
        p.start()
    try:
        mgr = AgentManager.__new__(AgentManager)
        mgr.docker_client = None
        result = await mgr.clone_agent(db, source, cloner, "Cloned Agent")
    finally:
        for p in patches:
            p.stop()

    dst_prefix = str(result.id)
    assert storage.files.get(f"{dst_prefix}/memory/memory.md") != "# Memory\nAccumulated knowledge over months"
    assert storage.files.get(f"{dst_prefix}/memory/reflections.md") != "# Reflections\nDeep insights"
