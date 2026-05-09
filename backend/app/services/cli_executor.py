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
        # cwd is the agent ROOT (not the workspace/ subdir). The system prompt
        # built by `build_agent_context` describes the layout from this root
        # (soul.md, memory/, skills/, workspace/ as siblings); launching the
        # CLI inside workspace/ caused the agent to write `memory/memory.md`
        # under workspace/ (invisible to the platform) and to nest produced
        # files as `workspace/workspace/...`.
        self.agent_dir = agent_dir
        self.cwd = agent_dir
        self.config = agent.cli_config or {}
        self._api_key = self._decrypt_key()
        # Map tool_use_id -> (tool_name, input_dict) so that the later
        # `tool_result` event (which only carries `tool_use_id`) can be
        # paired back to the original tool name and arguments for the UI.
        self._tool_calls_by_id: dict[str, dict] = {}

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
    def parse_event(self, event: dict) -> dict | list[dict] | None: ...

    async def run_stream(
        self,
        prompt: str,
        mcp_config: dict | None = None,
    ) -> AsyncGenerator[dict, None]:
        args = self.build_args(prompt, mcp_config)
        env = {**os.environ, **self.get_env()}

        logger.info(f"[CLI] Starting {self.get_binary()} with {len(args)} args, cwd={self.cwd}")

        # Make sure the documented layout exists so the agent can `ls .` and
        # immediately see the right structure.
        self.agent_dir.mkdir(parents=True, exist_ok=True)
        for sub in ("workspace", "memory", "skills"):
            (self.agent_dir / sub).mkdir(parents=True, exist_ok=True)

        # One-time best-effort cleanup of junk created by the previous (buggy)
        # cwd=workspace layout. We move misplaced memory back to its correct
        # home only when the target is empty (never destructive), and remove
        # the empty `workspace/workspace` nesting if present.
        try:
            ws = self.agent_dir / "workspace"
            stray_mem = ws / "memory" / "memory.md"
            real_mem = self.agent_dir / "memory" / "memory.md"
            target_empty = (
                not real_mem.exists()
                or (real_mem.is_file() and real_mem.stat().st_size == 0)
            )
            if stray_mem.exists() and target_empty:
                real_mem.parent.mkdir(parents=True, exist_ok=True)
                if real_mem.exists():
                    real_mem.unlink()
                stray_mem.rename(real_mem)
                logger.warning(
                    f"[CLI] Migrated stray memory from {stray_mem} → {real_mem}"
                )
            stray_mem_dir = ws / "memory"
            if stray_mem_dir.exists() and not any(stray_mem_dir.iterdir()):
                stray_mem_dir.rmdir()
            stray_ws_dir = ws / "workspace"
            if stray_ws_dir.exists() and not any(stray_ws_dir.iterdir()):
                stray_ws_dir.rmdir()
        except Exception as _e:
            logger.warning(f"[CLI] cleanup of stray workspace dirs skipped: {_e}")

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
                        if not parsed:
                            continue
                        if isinstance(parsed, list):
                            for p in parsed:
                                if p:
                                    yield p
                        else:
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

    def parse_event(self, event: dict) -> dict | list[dict] | None:
        t = event.get("type")
        if t == "assistant":
            # An assistant message can mix `text` and one-or-more `tool_use`
            # blocks. Emit all of them in stream order so the UI shows both
            # the explanation and every tool invocation, and so the
            # `_tool_calls_by_id` map stays consistent for later results.
            content_blocks = event.get("message", {}).get("content", [])
            out: list[dict] = []
            for b in content_blocks:
                if not isinstance(b, dict):
                    continue
                btype = b.get("type")
                if btype == "text":
                    txt = b.get("text", "")
                    if txt:
                        out.append({"type": "assistant", "content": txt})
                elif btype == "tool_use":
                    out.append(self._register_tool_use(b))
            return out or None
        elif t == "tool_use":
            return self._register_tool_use(event)
        elif t == "user":
            # Claude Code wraps tool_results in a "user" message whose content
            # is a list of tool_result blocks. Emit each so the UI updates
            # every prior `running` bubble to `done`.
            content_blocks = event.get("message", {}).get("content", [])
            out: list[dict] = []
            if isinstance(content_blocks, list):
                for b in content_blocks:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        out.append(self._build_tool_result_event(b))
            return out or None
        elif t == "tool_result":
            return self._build_tool_result_event(event)
        elif t == "result":
            return {"type": "result", "content": event.get("result", "")}
        return None

    def _register_tool_use(self, block: dict) -> dict:
        tool_id = block.get("id", "")
        tool_name = block.get("name", "unknown")
        tool_input = block.get("input", {})
        if tool_id:
            self._tool_calls_by_id[tool_id] = {
                "name": tool_name,
                "input": tool_input,
            }
        return {
            "type": "tool_use",
            "tool": tool_name,
            "input": tool_input,
            "tool_use_id": tool_id,
        }

    def _build_tool_result_event(self, block: dict) -> dict:
        tool_id = block.get("tool_use_id", "")
        prior = self._tool_calls_by_id.pop(tool_id, {}) if tool_id else {}
        raw_content = block.get("content", "")
        # Tool results may come as a string or as a list of content blocks
        # (e.g. [{"type":"text","text":"..."}]). Flatten to a string for UI.
        if isinstance(raw_content, list):
            parts = []
            for c in raw_content:
                if isinstance(c, dict):
                    parts.append(c.get("text", "") or c.get("content", "") or "")
                else:
                    parts.append(str(c))
            output_str = "".join(parts)
        else:
            output_str = str(raw_content)
        return {
            "type": "tool_result",
            "tool": prior.get("name", "unknown"),
            "input": prior.get("input", {}),
            "output": output_str[:1000],
            "tool_use_id": tool_id,
            "is_error": bool(block.get("is_error", False)),
        }


class GeminiExecutor(BaseCLIExecutor):

    def __init__(self, agent, agent_dir: Path):
        super().__init__(agent, agent_dir)
        # Gemini's `result` stream-json event carries no assistant text — the
        # final answer is delivered as one or more `message` events with
        # role="assistant" (some marked `delta:true` for streamed chunks).
        # Accumulate them so we can emit a single canonical `result` event
        # at the end, matching Claude Code's behavior.
        self._assistant_buffer: str = ""

    def get_binary(self) -> str:
        return shutil.which("gemini") or "gemini"

    def build_args(self, prompt: str, mcp_config: dict | None) -> list[str]:
        args = ["-p", prompt, "--output-format", "stream-json", "--sandbox=false"]
        if self.config.get("model"):
            args.extend(["--model", self.config["model"]])
        if self.config.get("permission_mode") == "bypass":
            args.append("--yolo")
        return args

    def get_env(self) -> dict:
        # Gemini CLI accepts either GEMINI_API_KEY or GOOGLE_API_KEY; set both
        # so users who configured one or the other in their agent both work.
        return {"GOOGLE_API_KEY": self._api_key, "GEMINI_API_KEY": self._api_key}

    def parse_event(self, event: dict) -> dict | list[dict] | None:
        # Gemini CLI stream-json schema (packages/core/src/output/types.ts):
        #   init         {type, timestamp, session_id, model}
        #   message      {type, timestamp, role, content, delta?}
        #   tool_use     {type, timestamp, tool_name, tool_id, parameters}
        #   tool_result  {type, timestamp, tool_id, status, output?, error?}
        #   error        {type, timestamp, severity, message}
        #   result       {type, timestamp, status, error?, stats?}
        t = event.get("type")
        if t == "message":
            role = event.get("role")
            content = event.get("content", "") or ""
            if role != "assistant" or not content:
                return None
            self._assistant_buffer += content
            # Surface each text fragment as an `assistant` event for symmetry
            # with the Claude path; the websocket layer drops these (the final
            # `result` event is what gets persisted as the chat reply).
            return {"type": "assistant", "content": content}
        elif t == "tool_use":
            tool_id = event.get("tool_id", "") or ""
            tool_name = event.get("tool_name", "unknown")
            tool_input = event.get("parameters", {}) or {}
            if tool_id:
                self._tool_calls_by_id[tool_id] = {
                    "name": tool_name,
                    "input": tool_input,
                }
            return {
                "type": "tool_use",
                "tool": tool_name,
                "input": tool_input,
                "tool_use_id": tool_id,
            }
        elif t == "tool_result":
            tool_id = event.get("tool_id", "") or ""
            prior = self._tool_calls_by_id.pop(tool_id, {}) if tool_id else {}
            status = event.get("status", "success")
            is_error = status != "success"
            output = event.get("output", "") or ""
            if not output:
                err = event.get("error")
                if isinstance(err, dict):
                    output = err.get("message", "") or str(err)
                elif err:
                    output = str(err)
            return {
                "type": "tool_result",
                "tool": prior.get("name", "unknown"),
                "input": prior.get("input", {}),
                "output": str(output)[:1000],
                "tool_use_id": tool_id,
                "is_error": is_error,
            }
        elif t == "result":
            text = self._assistant_buffer
            self._assistant_buffer = ""
            if not text:
                err = event.get("error")
                if isinstance(err, dict):
                    text = err.get("message", "") or ""
            return {"type": "result", "content": text}
        elif t == "error":
            return {"type": "error", "content": event.get("message", "Unknown CLI error")}
        # init / stats-only / unknown — nothing to surface to the UI.
        return None


def get_cli_executor(agent) -> BaseCLIExecutor:
    agent_dir = Path(settings.AGENT_DATA_DIR) / str(agent.id)
    if agent.cli_engine == "claude_code":
        return ClaudeCodeExecutor(agent, agent_dir)
    elif agent.cli_engine == "gemini_cli":
        return GeminiExecutor(agent, agent_dir)
    raise ValueError(f"Unknown CLI engine: {agent.cli_engine}")
