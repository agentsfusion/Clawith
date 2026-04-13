"""Agent Script Runtime — parses .ascript and builds LLM system prompts.

Ported from ClawEvolver's script execution model. The runtime:
1. Parses an Agent Script into structured components
2. Maintains runtime state (current topic, variables)
3. Generates a specialised system prompt that makes the LLM behave
   according to the script's topics, actions, and reasoning blocks
4. Persists state via Replit Object Storage
"""

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ScriptAction:
    name: str
    description: str = ""
    target: str = ""
    inputs: dict[str, dict] = field(default_factory=dict)
    outputs: dict[str, dict] = field(default_factory=dict)
    available_when: str | None = None


@dataclass
class ScriptTopic:
    name: str
    description: str = ""
    actions: dict[str, ScriptAction] = field(default_factory=dict)
    reasoning_text: str = ""
    after_reasoning_text: str = ""


@dataclass
class ScriptVariable:
    name: str
    var_type: str = "string"
    default: Any = ""
    description: str = ""
    mutable: bool = True


@dataclass
class ParsedScript:
    config: dict = field(default_factory=dict)
    system_messages: dict = field(default_factory=dict)
    system_instructions: str = ""
    variables: dict[str, ScriptVariable] = field(default_factory=dict)
    start_agent: ScriptTopic | None = None
    topics: dict[str, ScriptTopic] = field(default_factory=dict)
    raw: str = ""


@dataclass
class ScriptState:
    current_topic: str = ""
    variables: dict[str, Any] = field(default_factory=dict)
    script_version_id: str = ""


def parse_script(script_text: str) -> ParsedScript:
    result = ParsedScript(raw=script_text)
    if not script_text or not script_text.strip():
        return result

    lines = script_text.split("\n")
    i = 0

    def _current_indent(line: str) -> int:
        return len(line) - len(line.lstrip())

    def _collect_block(start: int, base_indent: int) -> tuple[list[str], int]:
        block_lines = []
        j = start
        while j < len(lines):
            line = lines[j]
            if line.strip() == "":
                block_lines.append("")
                j += 1
                continue
            if _current_indent(line) <= base_indent and line.strip():
                break
            block_lines.append(line)
            j += 1
        return block_lines, j

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped or stripped.startswith("#"):
            i += 1
            continue

        if stripped.startswith("config:"):
            block, i = _collect_block(i + 1, _current_indent(line))
            result.config = _parse_yaml_like(block)
            continue

        if stripped.startswith("system:"):
            block, i = _collect_block(i + 1, _current_indent(line))
            sys_data = _parse_yaml_like(block)
            result.system_messages = sys_data.get("messages", {})
            result.system_instructions = sys_data.get("instructions", "")
            continue

        if stripped.startswith("variables:"):
            block, i = _collect_block(i + 1, _current_indent(line))
            result.variables = _parse_variables(block)
            continue

        m = re.match(r'start_agent\s+(\w+):', stripped)
        if m:
            block, i = _collect_block(i + 1, _current_indent(line))
            result.start_agent = _parse_topic(m.group(1), block)
            continue

        m = re.match(r'topic\s+(\w+):', stripped)
        if m:
            topic_name = m.group(1)
            block, i = _collect_block(i + 1, _current_indent(line))
            result.topics[topic_name] = _parse_topic(topic_name, block)
            continue

        if re.match(r'^(TOOLBOX|SKILLS|ROLE)\s*\{', stripped) or stripped in ("TOOLBOX {", "SKILLS {", "ROLE {"):
            brace_count = stripped.count("{") - stripped.count("}")
            i += 1
            while i < len(lines) and brace_count > 0:
                brace_count += lines[i].count("{") - lines[i].count("}")
                i += 1
            continue

        if re.match(r'^(BEHAVIOR|TOOLBOX|SKILLS|ROLE)\s*\{?', stripped):
            block, i = _collect_block(i + 1, _current_indent(line))
            continue

        i += 1

    return result


def _parse_yaml_like(block_lines: list[str]) -> dict:
    result = {}
    current_key = None
    current_sub: dict | None = None
    sub_indent: int | None = None

    for line in block_lines:
        stripped = line.strip()
        if not stripped:
            continue

        indent = len(line) - len(line.lstrip())

        if sub_indent is not None and indent <= sub_indent and current_sub is not None:
            current_key = None
            current_sub = None
            sub_indent = None

        m = re.match(r'^(\w[\w_-]*)\s*:\s*$', stripped)
        if m:
            current_key = m.group(1)
            current_sub = {}
            sub_indent = indent
            result[current_key] = current_sub
            continue

        m = re.match(r'^(\w[\w_-]*)\s*:\s*(.+)$', stripped)
        if m:
            key, val = m.group(1), m.group(2).strip().strip('"').strip("'")
            if current_sub is not None and sub_indent is not None and indent > sub_indent:
                current_sub[key] = val
            else:
                current_key = None
                current_sub = None
                sub_indent = None
                result[key] = val
            continue

    return result


def _parse_variables(block_lines: list[str]) -> dict[str, ScriptVariable]:
    variables = {}
    current_var: ScriptVariable | None = None

    for line in block_lines:
        stripped = line.strip()
        if not stripped:
            continue

        m = re.match(r'^(\w+)\s*:\s*(mutable\s+)?(\w+)\s*=\s*(.+)$', stripped)
        if m:
            name = m.group(1)
            mutable = bool(m.group(2))
            var_type = m.group(3)
            default_str = m.group(4).strip().strip('"').strip("'")
            default: Any = default_str
            if var_type == "boolean":
                default = default_str.lower() in ("true", "1", "yes")
            elif var_type == "number":
                try:
                    default = float(default_str) if "." in default_str else int(default_str)
                except ValueError:
                    default = 0
            elif default_str == '""' or default_str == "''":
                default = ""
            current_var = ScriptVariable(name=name, var_type=var_type, default=default, mutable=mutable)
            variables[name] = current_var
            continue

        if stripped.startswith("description:") and current_var:
            current_var.description = stripped.split(":", 1)[1].strip().strip('"').strip("'")
            continue

    return variables


def _parse_topic(name: str, block_lines: list[str]) -> ScriptTopic:
    topic = ScriptTopic(name=name)
    i = 0

    while i < len(block_lines):
        stripped = block_lines[i].strip()

        if stripped.startswith("description:"):
            topic.description = stripped.split(":", 1)[1].strip().strip('"').strip("'")
            i += 1
            continue

        if stripped == "actions:":
            i += 1
            action_lines = []
            while i < len(block_lines):
                if block_lines[i].strip() and not block_lines[i].startswith("    ") and not block_lines[i].startswith("\t"):
                    if not block_lines[i].strip().startswith(("description:", "target:", "inputs:", "outputs:", "available")):
                        break
                action_lines.append(block_lines[i])
                i += 1
            topic.actions = _parse_actions(action_lines)
            continue

        if stripped.startswith("reasoning:"):
            i += 1
            reasoning_lines = []
            base_indent = len(block_lines[i - 1]) - len(block_lines[i - 1].lstrip()) if i <= len(block_lines) else 2
            while i < len(block_lines):
                bl = block_lines[i]
                bl_stripped = bl.strip()
                if bl_stripped and not bl.startswith(" " * (base_indent + 1)) and bl_stripped not in ("", ) and not bl_stripped.startswith("instructions") and not bl_stripped.startswith("|") and not bl_stripped.startswith("if ") and not bl_stripped.startswith("run ") and not bl_stripped.startswith("set ") and not bl_stripped.startswith("transition") and not bl_stripped.startswith("actions:"):
                    if bl_stripped.startswith("after_reasoning:"):
                        break
                    if not bl.startswith("  "):
                        break
                reasoning_lines.append(bl)
                i += 1
            topic.reasoning_text = "\n".join(reasoning_lines)
            continue

        if stripped.startswith("after_reasoning:"):
            i += 1
            ar_lines = []
            while i < len(block_lines):
                ar_lines.append(block_lines[i])
                i += 1
            topic.after_reasoning_text = "\n".join(ar_lines)
            continue

        i += 1

    return topic


def _parse_actions(action_lines: list[str]) -> dict[str, ScriptAction]:
    actions = {}
    current: ScriptAction | None = None
    section = ""

    for line in action_lines:
        stripped = line.strip()
        if not stripped:
            continue

        m = re.match(r'^(\w[\w_-]*)\s*:\s*$', stripped)
        if m:
            name = m.group(1)
            if name not in ("inputs", "outputs", "description", "target"):
                current = ScriptAction(name=name)
                actions[name] = current
                section = ""
            elif current and name in ("inputs", "outputs"):
                section = name
            continue

        m = re.match(r'^(\w[\w_-]*)\s*:\s*(.+)$', stripped)
        if m and current:
            key, val = m.group(1), m.group(2).strip().strip('"').strip("'")
            if key == "description":
                if section == "":
                    current.description = val
            elif key == "target":
                current.target = val
            elif section == "inputs":
                current.inputs[key] = {"type": val, "description": ""}
            elif section == "outputs":
                current.outputs[key] = {"type": val, "description": ""}
            continue

        if stripped.startswith("available when") and current:
            current.available_when = stripped.replace("available when", "").strip()
            continue

        if stripped.startswith("description:") and section in ("inputs", "outputs") and current:
            desc_val = stripped.split(":", 1)[1].strip().strip('"').strip("'")
            target_dict = current.inputs if section == "inputs" else current.outputs
            if target_dict:
                last_key = list(target_dict.keys())[-1]
                target_dict[last_key]["description"] = desc_val

    return actions


def init_state(parsed: ParsedScript, version_id: str = "") -> ScriptState:
    state = ScriptState(script_version_id=version_id)
    if parsed.start_agent:
        state.current_topic = "__start__"
    elif parsed.topics:
        state.current_topic = next(iter(parsed.topics))
    for name, var in parsed.variables.items():
        state.variables[name] = var.default
    return state


async def load_state(agent_id: uuid.UUID, session_id: str = "") -> ScriptState | None:
    from app.services.storage.factory import get_storage
    storage = get_storage()
    suffix = f"_{session_id}" if session_id else ""
    key = f"{agent_id}/script_state{suffix}.json"
    if not await storage.exists(key):
        return None
    try:
        raw = await storage.read(key)
        data = json.loads(raw)
        return ScriptState(
            current_topic=data.get("current_topic", ""),
            variables=data.get("variables", {}),
            script_version_id=data.get("script_version_id", ""),
        )
    except Exception as e:
        logger.warning(f"[ScriptRuntime] Failed to load state for {agent_id}/{session_id}: {e}")
        return None


async def save_state(agent_id: uuid.UUID, state: ScriptState, session_id: str = ""):
    from app.services.storage.factory import get_storage
    storage = get_storage()
    suffix = f"_{session_id}" if session_id else ""
    key = f"{agent_id}/script_state{suffix}.json"
    data = {
        "current_topic": state.current_topic,
        "variables": state.variables,
        "script_version_id": state.script_version_id,
    }
    await storage.write(key, json.dumps(data, ensure_ascii=False, indent=2))


def _render_variables_section(parsed: ParsedScript, state: ScriptState) -> str:
    if not parsed.variables:
        return ""
    lines = ["## Current State (Variables)"]
    for name, var in parsed.variables.items():
        current_val = state.variables.get(name, var.default)
        desc = f" — {var.description}" if var.description else ""
        mutable_tag = " [mutable]" if var.mutable else " [readonly]"
        lines.append(f"- `{name}` ({var.var_type}{mutable_tag}): `{json.dumps(current_val, ensure_ascii=False)}`{desc}")
    return "\n".join(lines)


def _render_actions_section(topic: ScriptTopic, state: ScriptState) -> str:
    if not topic.actions:
        return ""
    lines = ["## Available Actions"]
    for name, action in topic.actions.items():
        if action.available_when:
            lines.append(f"- **{name}**: {action.description}")
            lines.append(f"  - Condition: available when `{action.available_when}`")
        else:
            lines.append(f"- **{name}**: {action.description}")
        if action.target:
            target_type = "tool" if "tool://" in action.target else "skill" if "skill://" in action.target else "flow"
            target_name = action.target.split("://")[-1] if "://" in action.target else action.target
            lines.append(f"  - Target: {target_type} `{target_name}`")
        if action.inputs:
            input_parts = [f"`{k}` ({v.get('type', 'string')})" for k, v in action.inputs.items()]
            lines.append(f"  - Inputs: {', '.join(input_parts)}")
        if action.outputs:
            output_parts = [f"`{k}` ({v.get('type', 'string')})" for k, v in action.outputs.items()]
            lines.append(f"  - Outputs: {', '.join(output_parts)}")
    return "\n".join(lines)


def _render_topics_nav(parsed: ParsedScript, current: str) -> str:
    if not parsed.topics:
        return ""
    lines = ["## Available Topics"]
    for name, topic in parsed.topics.items():
        marker = " ← (current)" if name == current else ""
        lines.append(f"- `{name}`: {topic.description}{marker}")
    return "\n".join(lines)


def build_system_prompt(parsed: ParsedScript, state: ScriptState) -> str:
    parts = []

    agent_name = parsed.config.get("agent_name", parsed.config.get("agent_label", "Agent"))
    description = parsed.config.get("description", "")

    parts.append(f"You are **{agent_name}**, an AI agent operating under a structured Agent Script.")
    if description:
        parts.append(f"**Role**: {description}")
    if parsed.system_instructions:
        parts.append(f"\n## Core Instructions\n{parsed.system_instructions}")

    current_topic_name = state.current_topic
    active_topic: ScriptTopic | None = None
    if current_topic_name == "__start__" and parsed.start_agent:
        active_topic = parsed.start_agent
    elif current_topic_name in parsed.topics:
        active_topic = parsed.topics[current_topic_name]
    elif parsed.start_agent:
        active_topic = parsed.start_agent
        state.current_topic = "__start__"

    variables_section = _render_variables_section(parsed, state)
    if variables_section:
        parts.append(f"\n{variables_section}")

    if active_topic:
        parts.append(f"\n## Current Topic: `{active_topic.name}`")
        if active_topic.description:
            parts.append(f"*{active_topic.description}*")

        actions_section = _render_actions_section(active_topic, state)
        if actions_section:
            parts.append(f"\n{actions_section}")

        if active_topic.reasoning_text:
            cleaned = active_topic.reasoning_text.strip()
            if cleaned:
                parts.append(f"\n## Reasoning Instructions\nFollow these instructions to handle the current conversation:\n```\n{cleaned}\n```")

    topics_nav = _render_topics_nav(parsed, current_topic_name)
    if topics_nav:
        parts.append(f"\n{topics_nav}")

    parts.append("""
## Execution Rules
1. **Variable Updates**: When you determine a variable value from the conversation, respond with `[SET variable_name = value]` on its own line.
2. **Topic Transitions**: To switch topics, respond with `[TRANSITION topic_name]` on its own line.
3. **Action Execution**: When an action maps to a tool (tool://), call it using the standard tool-calling mechanism. When it maps to a skill (skill://), use `read_file` to load the skill first, then follow its instructions.
4. **Natural Language**: Lines with `|` in the script are prompts — follow them as instructions for what to say.
5. **Procedural Logic**: Lines with `->` are deterministic — follow them as if/else logic strictly.
6. **Always stay in character** as defined by the script's system instructions.
7. **Welcome message**: If this is the start of a conversation, greet with the configured welcome message.""")

    welcome = parsed.system_messages.get("welcome", "")
    error_msg = parsed.system_messages.get("error", "")
    if welcome or error_msg:
        parts.append(f"\n## System Messages")
        if welcome:
            parts.append(f"- Welcome: \"{welcome}\"")
        if error_msg:
            parts.append(f"- Error fallback: \"{error_msg}\"")

    return "\n".join(parts)


def process_response(response_text: str, state: ScriptState, parsed: ParsedScript) -> tuple[str, list[str]]:
    changes = []
    clean_text = response_text

    for m in re.finditer(r'\[SET\s+(\w+)\s*=\s*(.+?)\]', response_text):
        var_name = m.group(1)
        val_str = m.group(2).strip().strip('"').strip("'")
        if var_name in parsed.variables:
            var = parsed.variables[var_name]
            if var.var_type == "boolean":
                state.variables[var_name] = val_str.lower() in ("true", "1", "yes")
            elif var.var_type == "number":
                try:
                    state.variables[var_name] = float(val_str) if "." in val_str else int(val_str)
                except ValueError:
                    pass
            else:
                state.variables[var_name] = val_str
            changes.append(f"SET {var_name} = {val_str}")
        clean_text = clean_text.replace(m.group(0), "").strip()

    for m in re.finditer(r'\[TRANSITION\s+(\w+)\]', response_text):
        topic_name = m.group(1)
        if topic_name in parsed.topics:
            state.current_topic = topic_name
            changes.append(f"TRANSITION → {topic_name}")
        elif topic_name == "topic_selector" or topic_name == "__start__":
            state.current_topic = "__start__"
            changes.append(f"TRANSITION → __start__")
        clean_text = clean_text.replace(m.group(0), "").strip()

    return clean_text, changes


async def get_script_for_agent(agent_id: str | uuid.UUID) -> str | None:
    from app.database import async_session
    from app.models.evolver import AgentScriptVersion
    from sqlalchemy import select, desc

    async with async_session() as db:
        result = await db.execute(
            select(AgentScriptVersion)
            .where(AgentScriptVersion.agent_id == str(agent_id))
            .order_by(
                desc(AgentScriptVersion.folder == "evolved"),
                desc(AgentScriptVersion.version),
            )
            .limit(1)
        )
        sv = result.scalar_one_or_none()
        if sv:
            return sv.content
    return None


async def build_evolver_context(agent_id: uuid.UUID, agent_name: str) -> tuple[str, ScriptState | None]:
    script_text = await get_script_for_agent(agent_id)
    if not script_text:
        return "", None

    parsed = parse_script(script_text)

    state = await load_state(agent_id)
    if state is None:
        state = init_state(parsed)

    system_prompt = build_system_prompt(parsed, state)
    return system_prompt, state
