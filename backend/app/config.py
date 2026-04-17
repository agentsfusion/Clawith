"""Application configuration."""

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings

from app.services.sandbox.config import SandboxConfig, SandboxType


def _running_in_container() -> bool:
    """Best-effort container runtime detection."""
    if Path("/.dockerenv").exists() or Path("/run/.containerenv").exists():
        return True

    cgroup = Path("/proc/1/cgroup")
    if not cgroup.exists():
        return False

    try:
        content = cgroup.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False

    return any(token in content for token in ("docker", "containerd", "kubepods", "podman"))


def _running_on_replit() -> bool:
    """Detect Replit environment via REPL_ID env var."""
    import os
    return bool(os.environ.get("REPL_ID"))


def _default_agent_data_dir() -> str:
    """Use Docker path in containers, user-writable path on local hosts."""
    if _running_on_replit():
        import os
        return os.path.join(os.environ.get("REPL_HOME", "/home/runner/workspace"), ".data", "agents")
    if _running_in_container():
        return "/data/agents"
    return str(Path.home() / ".clawith" / "data" / "agents")


def _default_feishu_cache_dir() -> str:
    """Writable directory for Feishu contacts cache files."""
    if _running_on_replit():
        return "/tmp/clawith_feishu_cache"
    return "/data/workspaces"


def _default_agent_template_dir() -> str:
    """Locate the agent template directory for both Docker and source deployments.

    In a Docker container the backend source is copied to /app, so the template
    lives at /app/agent_template.  In a source deployment it sits next to the
    backend/ package root, i.e. <repo>/backend/agent_template.
    """
    if _running_on_replit() or not _running_in_container():
        source_path = Path(__file__).resolve().parent.parent / "agent_template"
        return str(source_path)
    return "/app/agent_template"


def _read_version() -> str:
    """Read version from local VERSION file, fallback to root."""
    for candidate in [Path(__file__).resolve().parent.parent / "VERSION",
                      Path(__file__).resolve().parent.parent.parent / "VERSION",
                      Path("/app/VERSION"), Path("/VERSION")]:
        try:
            return candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue
    return "0.0.0"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # App
    APP_NAME: str = "Clawith"
    APP_VERSION: str = _read_version()
    DEBUG: bool = False
    SECRET_KEY: str = "change-me-in-production"
    API_PREFIX: str = "/api"

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://clawith:clawith@localhost:5432/clawith"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # JWT
    JWT_SECRET_KEY: str = "change-me-jwt-secret"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24 hours
    PASSWORD_RESET_TOKEN_EXPIRE_MINUTES: int = 60
    EMAIL_VERIFICATION_TOKEN_EXPIRE_MINUTES: int = 60  # 1 hour
    EMAIL_VERIFICATION_REQUIRED: bool = False  # Require email verification for login

    # File Storage
    AGENT_DATA_DIR: str = _default_agent_data_dir()
    AGENT_TEMPLATE_DIR: str = _default_agent_template_dir()
    FEISHU_CACHE_DIR: str = _default_feishu_cache_dir()

    # Cloud Storage Backend
    STORAGE_BACKEND: str = "local"  # "local" | "s3" | "replit"
    STORAGE_BUCKET: str = ""
    STORAGE_ENDPOINT_URL: str = ""  # Custom endpoint for S3-compatible providers (e.g., Huawei OBS)
    STORAGE_REGION: str = "us-east-1"
    STORAGE_ACCESS_KEY: str = ""
    STORAGE_SECRET_KEY: str = ""
    STORAGE_CACHE_DIR: str = "/tmp/clawith_cache"  # Empty string disables caching
    STORAGE_CACHE_TTL_SECONDS: int = 60

    # Docker (for Agent containers)
    DOCKER_NETWORK: str = "clawith_network"
    OPENCLAW_IMAGE: str = "openclaw:local"
    OPENCLAW_GATEWAY_PORT: int = 18789

    # Feishu OAuth
    FEISHU_APP_ID: str = ""
    FEISHU_APP_SECRET: str = ""
    FEISHU_REDIRECT_URI: str = ""
    PUBLIC_BASE_URL: str = ""

    # CORS
    CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:5173"]

    # Google Workspace CLI
    GWS_OAUTH_CALLBACK_PATH: str = "/api/gws/auth/callback"
    GWS_OAUTH_REDIRECT_URI: str = ""

    # Lark CLI
    LARK_OAUTH_CALLBACK_PATH: str = "/api/lark/auth/callback"
    LARK_OAUTH_REDIRECT_URI: str = ""

    # Jina AI (Reader + Search APIs)
    JINA_API_KEY: str = ""

    # Exa AI (Search API)
    EXA_API_KEY: str = ""


    # Workspace Sync (file watcher for S3/OBS upload after execute_code)
    WORKSPACE_SYNC_IDLE_TIMEOUT: int = 300  # seconds; stop watcher after this idle period
    WORKSPACE_SYNC_DEBOUNCE_MS: int = 500   # milliseconds; debounce uploads per file path
    WORKSPACE_SYNC_MAX_WATCHERS: int = 100  # max concurrent per-agent watchers

    # Sandbox configuration
    SANDBOX_TYPE: SandboxType = SandboxType.SUBPROCESS
    SANDBOX_API_KEY: str = ""
    SANDBOX_API_URL: str = ""
    SANDBOX_CPU_LIMIT: str = "0.5"
    SANDBOX_MEMORY_LIMIT: str = "256m"
    SANDBOX_ALLOW_NETWORK: bool = False
    SANDBOX_DEFAULT_TIMEOUT: int = 30
    SANDBOX_MAX_TIMEOUT: int = 60

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def _normalize_database_url(cls, v: str) -> str:
        """Ensure PostgreSQL URLs use the asyncpg driver.

        Platforms like Replit provide ``postgresql://`` without a driver
        suffix, but SQLAlchemy's async engine requires
        ``postgresql+asyncpg://``.  This validator adds the suffix
        transparently at config-load time.  It also rewrites the
        ``sslmode`` query parameter to ``ssl`` because asyncpg does not
        accept ``sslmode``.
        """
        if not isinstance(v, str):
            return v
        if v.startswith("postgresql://"):
            scheme = v.split("://", 1)[0]
            if "+" not in scheme:
                v = v.replace("postgresql://", "postgresql+asyncpg://", 1)
        v = v.replace("sslmode=", "ssl=")
        return v

    model_config = {
        "env_file": [".env", "../.env"],
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
        "extra": "ignore",
    }


@lru_cache
def get_settings() -> Settings:
    """Get cached application settings."""
    return Settings()


def get_sandbox_config() -> SandboxConfig:
    """Create SandboxConfig from application settings."""
    settings = get_settings()
    return SandboxConfig(
        type=settings.SANDBOX_TYPE,
        enabled=True,
        api_key=settings.SANDBOX_API_KEY,
        api_url=settings.SANDBOX_API_URL,
        cpu_limit=settings.SANDBOX_CPU_LIMIT,
        memory_limit=settings.SANDBOX_MEMORY_LIMIT,
        allow_network=settings.SANDBOX_ALLOW_NETWORK,
        default_timeout=settings.SANDBOX_DEFAULT_TIMEOUT,
        max_timeout=settings.SANDBOX_MAX_TIMEOUT,
    )
