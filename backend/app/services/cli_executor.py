"""CLI Agent execution engine — manages CLI subprocess lifecycle and stream-json parsing."""

import asyncio
import json
import logging
import os
import shutil
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import AsyncGenerator

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class BaseCLIExecutor(ABC):

    def __init__(self, agent, agent_dir: Path):
        self.agent = agent
        self.cwd = agent_dir / "workspace"
        self.config = agent.cli_config or {}
        self._api_key = self._decrypt_key()

    def _decrypt_key(self) -> str:
        if not self.agent.cli_api_key_encrypted:
            return ""
        from app.core.security import decrypt_data
        return decrypt_data(self.agent.cli_api_key_encrypted, settings.SECRET_KEY)

    @abstractmethod
    def get_binary(self) -> str: ...

    @abstractmethod
    def build_args(self, prompt: str, mcp_config: dict | None) -> list[str]: ...

    @abstractmethod
    def get_env(self) -> dict: ...

    @abstractmethod
    def parse_event(self, event: dict) -> dict | None: ...

    async def run_stream(
        self,
        prompt: str,
        mcp_config: dict | None = None,
    ) -> AsyncGenerator[dict, None]:
        args = self.build_args(prompt, mcp_config)
        env = {**os.environ, **self.get_env()}

        logger.info(f"[CLI] Starting {self.get_binary()} with {len(args)} args, cwd={self.cwd}")

        self.cwd.mkdir(parents=True, exist_ok=True)

        proc = await asyncio.create_subprocess_exec(
            self.get_binary(), *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.cwd),
            env=env,
        )

        buffer = b""
        try:
            async for chunk in proc.stdout:
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                        parsed = self.parse_event(event)
                        if parsed:
                            yield parsed
                    except json.JSONDecodeError:
                        continue
        except asyncio.CancelledError:
            proc.kill()
            raise

        await proc.wait()
        if proc.returncode != 0:
            stderr = await proc.stderr.read()
            logger.error(f"[CLI] Process exited with code {proc.returncode}: {stderr.decode()[:500]}")
            yield {"type": "error", "content": f"CLI exited with code {proc.returncode}"}


class ClaudeCodeExecutor(BaseCLIExecutor):

    def get_binary(self) -> str:
        return shutil.which("claude") or "claude"

    def build_args(self, prompt: str, mcp_config: dict | None) -> list[str]:
        args = [
            "-p", prompt,
            "--output-format", "stream-json",
            "--verbose",
        ]

        if self.config.get("permission_mode") == "bypass":
            args.append("--dangerously-skip-permissions")

        if self.config.get("model"):
            args.extend(["--model", self.config["model"]])

        if self.config.get("max_turns"):
            args.extend(["--max-turns", str(self.config["max_turns"])])

        if mcp_config:
            mcp_file = tempfile.NamedTemporaryFile(
                mode='w', suffix='.json', prefix='clawith_mcp_', delete=False
            )
            json.dump(mcp_config, mcp_file)
            mcp_file.close()
            args.extend(["--mcp-config", mcp_file.name])

        return args

    def get_env(self) -> dict:
        return {"ANTHROPIC_API_KEY": self._api_key}

    def parse_event(self, event: dict) -> dict | None:
        t = event.get("type")
        if t == "assistant":
            content_blocks = event.get("message", {}).get("content", [])
            text_parts = []
            tool_uses = []
            for b in content_blocks:
                if b.get("type") == "text":
                    text_parts.append(b.get("text", ""))
                elif b.get("type") == "tool_use":
                    tool_uses.append(b)
            if text_parts:
                return {"type": "assistant", "content": "".join(text_parts)}
            if tool_uses:
                tu = tool_uses[0]
                return {
                    "type": "tool_use",
                    "tool": tu.get("name", "unknown"),
                    "input": tu.get("input", {}),
                }
        elif t == "tool_use":
            return {
                "type": "tool_use",
                "tool": event.get("name", "unknown"),
                "input": event.get("input", {}),
            }
        elif t == "tool_result":
            return {
                "type": "tool_result",
                "tool": event.get("tool_use_id", "unknown"),
                "output": str(event.get("content", ""))[:500],
            }
        elif t == "result":
            return {"type": "result", "content": event.get("result", "")}
        return None


class GeminiExecutor(BaseCLIExecutor):

    def get_binary(self) -> str:
        return shutil.which("gemini") or "gemini"

    def build_args(self, prompt: str, mcp_config: dict | None) -> list[str]:
        args = ["-p", prompt, "--output-format", "stream-json", "--sandbox=false"]
        if self.config.get("model"):
            args.extend(["--model", self.config["model"]])
        return args

    def get_env(self) -> dict:
        return {"GOOGLE_API_KEY": self._api_key}

    def parse_event(self, event: dict) -> dict | None:
        return {"type": "unknown", "raw": event}


def get_cli_executor(agent) -> BaseCLIExecutor:
    agent_dir = Path(settings.AGENT_DATA_DIR) / str(agent.id)
    if agent.cli_engine == "claude_code":
        return ClaudeCodeExecutor(agent, agent_dir)
    elif agent.cli_engine == "gemini_cli":
        return GeminiExecutor(agent, agent_dir)
    raise ValueError(f"Unknown CLI engine: {agent.cli_engine}")
