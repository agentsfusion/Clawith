"""Tests for the non-Docker (in-process native) agent runtime.

On hosts without a usable Docker daemon (e.g. Replit), native agents must still
reach a usable "running" state via the in-process runtime, while remote OpenClaw
agents stay "idle" until they check in through the gateway.
"""

import uuid
from types import SimpleNamespace

import pytest

from app.services.agent_manager import AgentManager


def _make_manager_without_docker() -> AgentManager:
    mgr = AgentManager.__new__(AgentManager)
    mgr.docker_client = None
    return mgr


def _make_agent(agent_type: str = "native") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        name="Tester",
        agent_type=agent_type,
        status="creating",
        container_id="stale",
        container_port=12345,
        last_active_at=None,
        primary_model_id=None,
    )


@pytest.mark.asyncio
async def test_start_container_runs_native_agent_without_docker():
    mgr = _make_manager_without_docker()
    agent = _make_agent("native")

    result = await mgr.start_container(db=None, agent=agent)

    assert result is None
    assert agent.status == "running"
    assert agent.container_id is None
    assert agent.container_port is None
    assert agent.last_active_at is not None


@pytest.mark.asyncio
async def test_start_container_keeps_openclaw_idle_without_docker():
    mgr = _make_manager_without_docker()
    agent = _make_agent("openclaw")

    result = await mgr.start_container(db=None, agent=agent)

    assert result is None
    assert agent.status == "idle"
    assert agent.container_id is None
    assert agent.container_port is None


def test_get_container_status_reports_running_native_runtime():
    mgr = _make_manager_without_docker()
    agent = _make_agent("native")
    agent.status = "running"
    agent.container_id = None

    status = mgr.get_container_status(agent)

    assert status["running"] is True
    assert status["status"] == "running"
    assert status["runtime"] == "in-process"


def test_get_container_status_idle_native_runtime_not_running():
    mgr = _make_manager_without_docker()
    agent = _make_agent("openclaw")
    agent.status = "idle"
    agent.container_id = None

    status = mgr.get_container_status(agent)

    assert status["running"] is False
    assert status["status"] == "idle"


@pytest.mark.asyncio
async def test_stop_container_marks_native_agent_stopped():
    mgr = _make_manager_without_docker()
    agent = _make_agent("native")
    agent.status = "running"
    agent.container_id = None

    ok = await mgr.stop_container(agent)

    assert ok is True
    assert agent.status == "stopped"
