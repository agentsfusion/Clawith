"""Tests for auto-detection PUBLIC_BASE_URL feature in platform_service."""

import os

import pytest
from starlette.requests import Request

from app.services.platform_service import (
    PlatformService,
    _build_replit_url,
    _cached_public_base_url,
    _resolve_from_request,
)


@pytest.fixture(autouse=True)
def _reset_cache():
    """Reset module-level cache before and after every test."""
    import app.services.platform_service as mod

    mod._cached_public_base_url = None
    yield
    mod._cached_public_base_url = None


@pytest.fixture(autouse=True)
def _clean_replit_env(monkeypatch):
    """Remove all Replit-related env vars so tests start clean."""
    for key in ("REPL_ID", "REPL_SLUG", "REPL_OWNER", "REPL_DEPLOYMENT"):
        monkeypatch.delenv(key, raising=False)


def _make_request(
    headers: dict[str, str] | None = None,
    base_url: str = "http://testserver",
) -> Request:
    """Build a Starlette Request with optional headers and base_url."""
    scope: dict = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "headers": [],
        "server": None,
        "scheme": base_url.split("://")[0],
    }
    if headers:
        scope["headers"] = [
            (k.lower().encode(), v.encode()) for k, v in headers.items()
        ]
    host = base_url.split("://")[1].rstrip("/")
    if ":" in host:
        h, p = host.split(":", 1)
        scope["server"] = (h, int(p))
    else:
        scope["server"] = (host, 80)
    return Request(scope)


class TestBuildReplitUrl:
    def test_standard_replit_url(self, monkeypatch):
        monkeypatch.setenv("REPL_ID", "abc-123")
        monkeypatch.setenv("REPL_SLUG", "hello-world")
        monkeypatch.setenv("REPL_OWNER", "johndoe")
        assert _build_replit_url() == "https://hello-world--johndoe.repl.co"

    def test_deployed_replit_url(self, monkeypatch):
        monkeypatch.setenv("REPL_ID", "abc-123")
        monkeypatch.setenv("REPL_SLUG", "hello-world")
        monkeypatch.setenv("REPL_OWNER", "johndoe")
        monkeypatch.setenv("REPL_DEPLOYMENT", "1")
        assert _build_replit_url() == "https://hello-world.repl.co"

    def test_non_replit_returns_none(self):
        assert _build_replit_url() is None

    def test_missing_repl_slug_returns_none(self, monkeypatch):
        monkeypatch.setenv("REPL_ID", "abc-123")
        assert _build_replit_url() is None

    def test_slug_without_owner_falls_back_to_simple_url(self, monkeypatch):
        monkeypatch.setenv("REPL_ID", "abc-123")
        monkeypatch.setenv("REPL_SLUG", "myslug")
        assert _build_replit_url() == "https://myslug.repl.co"


class TestResolveFromRequest:
    def test_forwarded_host_and_proto(self):
        req = _make_request(
            headers={
                "x-forwarded-host": "app.example.com",
                "x-forwarded-proto": "https",
            }
        )
        assert _resolve_from_request(req) == "https://app.example.com"

    def test_forwarded_host_defaults_to_https(self):
        req = _make_request(
            headers={"x-forwarded-host": "app.example.com"},
        )
        assert _resolve_from_request(req) == "https://app.example.com"

    def test_no_forwarded_headers_falls_back_to_base_url(self):
        req = _make_request(base_url="http://localhost:8000")
        result = _resolve_from_request(req)
        assert result == "http://localhost:8000"


class TestGetPublicBaseUrl:
    @pytest.mark.asyncio
    async def test_env_var_wins_over_everything(self, monkeypatch):
        monkeypatch.setenv("PUBLIC_BASE_URL", "https://env.example.com")
        monkeypatch.setenv("REPL_ID", "abc-123")
        monkeypatch.setenv("REPL_SLUG", "hello-world")
        monkeypatch.setenv("REPL_OWNER", "johndoe")
        req = _make_request(
            headers={
                "x-forwarded-host": "header.example.com",
                "x-forwarded-proto": "https",
            }
        )
        svc = PlatformService()
        result = await svc.get_public_base_url(request=req)
        assert result == "https://env.example.com"

    @pytest.mark.asyncio
    async def test_replit_wins_when_no_env_var(self, monkeypatch):
        monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
        monkeypatch.setenv("REPL_ID", "abc-123")
        monkeypatch.setenv("REPL_SLUG", "hello-world")
        monkeypatch.setenv("REPL_OWNER", "johndoe")
        svc = PlatformService()
        result = await svc.get_public_base_url()
        assert result == "https://hello-world--johndoe.repl.co"

    @pytest.mark.asyncio
    async def test_cache_used_when_no_env_no_replit(self, monkeypatch):
        import app.services.platform_service as mod

        monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
        mod._cached_public_base_url = "https://cached.example.com"
        svc = PlatformService()
        result = await svc.get_public_base_url()
        assert result == "https://cached.example.com"

    @pytest.mark.asyncio
    async def test_request_used_when_no_env_no_replit_no_cache(self, monkeypatch):
        monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
        req = _make_request(
            headers={
                "x-forwarded-host": "req.example.com",
                "x-forwarded-proto": "https",
            }
        )
        svc = PlatformService()
        result = await svc.get_public_base_url(request=req)
        assert result == "https://req.example.com"

    @pytest.mark.asyncio
    async def test_hardcoded_fallback(self, monkeypatch):
        monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
        svc = PlatformService()
        result = await svc.get_public_base_url()
        assert result == "https://try.clawith.ai"

    @pytest.mark.asyncio
    async def test_cache_updated_after_request_detection(self, monkeypatch):
        import app.services.platform_service as mod

        monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
        req = _make_request(
            headers={
                "x-forwarded-host": "first.example.com",
                "x-forwarded-proto": "https",
            }
        )
        svc = PlatformService()
        await svc.get_public_base_url(request=req)
        assert mod._cached_public_base_url == "https://first.example.com"

        second_req = _make_request()
        result = await svc.get_public_base_url(request=second_req)
        assert result == "https://first.example.com"
