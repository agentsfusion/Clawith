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
    pending_transitions: list[str] = field(default_factory=list)
    pending_actions: list[dict] = field(default_factory=list)
    mem: dict[str, str] = field(default_factory=dict)


@dataclass
class ExecutionStep:
    topic: str = ""
    action: str = ""
    detail: str = ""

    def to_dict(self) -> dict:
        return {"topic": self.topic, "action": self.action, "detail": self.detail}


@dataclass
class ScriptExecutionResult:
    response: str = ""
    changes: list[str] = field(default_factory=list)
    llm_instructions: list[str] = field(default_factory=list)
    actions_to_run: list[dict] = field(default_factory=list)
    needs_llm: bool = False
    final_topic: str = ""
    final_variables: dict[str, Any] = field(default_factory=dict)
    steps: list[ExecutionStep] = field(default_factory=list)
    topic_path: list[str] = field(default_factory=list)


def _resolve_var_ref(text: str, variables: dict[str, Any]) -> str:
    def _repl(m):
        var_name = m.group(1)
        val = variables.get(var_name, "")
        return str(val) if val is not None else ""
    result = re.sub(r'\{!@variables\.(\w+)\}', _repl, text)
    return result


def _eval_condition(condition: str, variables: dict[str, Any]) -> bool:
    cond = condition.strip()

    cond = re.sub(r'@variables\.(\w+)', lambda m: f'__vars__.get("{m.group(1)}", "")', cond)

    cond = cond.replace(" is not None", ' is not None')
    cond = cond.replace(" is None", ' is None')
    cond = re.sub(r'\b(True)\b', 'True', cond)
    cond = re.sub(r'\b(False)\b', 'False', cond)

    try:
        result = eval(cond, {"__builtins__": {}, "__vars__": variables,
                             "True": True, "False": False, "None": None})
        return bool(result)
    except Exception as e:
        logger.debug(f"[ScriptRuntime] Condition eval failed: {cond!r} -> {e}")
        return True


@dataclass
class ReasoningResult:
    prompts: list[str] = field(default_factory=list)
    transitions: list[str] = field(default_factory=list)
    actions_to_run: list[dict] = field(default_factory=list)
    is_procedural: bool = False


def evaluate_reasoning(reasoning_text: str, variables: dict[str, Any],
                       topic_actions: dict[str, 'ScriptAction'] | None = None) -> ReasoningResult:
    result = ReasoningResult()
    if not reasoning_text or not reasoning_text.strip():
        return result

    lines = reasoning_text.split("\n")

    is_procedural = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("instructions:->"):
            is_procedural = True
            break
        if stripped.startswith("instructions:|"):
            is_procedural = False
            break

    result.is_procedural = is_procedural

    if not is_procedural:
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("instructions:|"):
                prompt = stripped[len("instructions:|"):].strip()
                if prompt:
                    result.prompts.append(_resolve_var_ref(prompt, variables))
                continue
            if stripped.startswith("instructions:"):
                continue
            if stripped.startswith("actions:"):
                continue
            if stripped.startswith("|"):
                prompt = stripped[1:].strip()
                if prompt:
                    result.prompts.append(_resolve_var_ref(prompt, variables))
                continue
            m_run = re.match(r'run @actions\.(\w+)', stripped)
            if m_run:
                action_name = m_run.group(1)
                action_info_nl: dict[str, Any] = {"name": action_name}
                if topic_actions and action_name in topic_actions:
                    action_info_nl["target"] = topic_actions[action_name].target
                result.actions_to_run.append(action_info_nl)
                continue
            m_trans = re.match(r'transition to @topic\.(\w+)', stripped)
            if m_trans:
                result.transitions.append(m_trans.group(1))
                continue
            if not stripped.startswith(("description:", "available when")):
                m = re.match(r'^(\w+):\s*@(utils\.transition|actions\.\w+)', stripped)
                if m:
                    continue
        return result

    if_stack: list[dict] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("instructions:->") or stripped.startswith("instructions:|"):
            continue
        if stripped.startswith("actions:") and not stripped.startswith("actions."):
            continue

        indent = len(line) - len(line.lstrip())

        while if_stack and indent <= if_stack[-1]["indent"]:
            if_stack.pop()

        def _is_active() -> bool:
            return all(frame["active"] for frame in if_stack)

        if stripped.startswith("if "):
            condition_expr = stripped[3:].rstrip(":")
            if _is_active():
                is_true = _eval_condition(condition_expr, variables)
                if_stack.append({"indent": indent, "active": is_true, "branch_taken": is_true})
            else:
                if_stack.append({"indent": indent, "active": False, "branch_taken": False})
            continue

        if stripped == "else:":
            if if_stack:
                frame = if_stack[-1]
                if all(f["active"] for f in if_stack[:-1]):
                    frame["active"] = not frame["branch_taken"]
            continue

        if not _is_active():
            continue

        if stripped.startswith("| ") or stripped.startswith("|"):
            prompt = stripped[1:].strip() if stripped.startswith("|") else stripped[2:]
            if prompt:
                result.prompts.append(_resolve_var_ref(prompt, variables))
            continue

        m = re.match(r'transition to @topic\.(\w+)', stripped)
        if m:
            result.transitions.append(m.group(1))
            continue

        m = re.match(r'run @actions\.(\w+)', stripped)
        if m:
            action_name = m.group(1)
            action_info: dict[str, Any] = {"name": action_name}
            if topic_actions and action_name in topic_actions:
                action_info["target"] = topic_actions[action_name].target
            result.actions_to_run.append(action_info)
            continue

        m = re.match(r'set @variables\.(\w+)\s*=\s*(.+)', stripped)
        if m:
            var_name, val_expr = m.group(1), m.group(2).strip()
            out_m = re.match(r'@outputs\.(\w+)', val_expr)
            if out_m:
                pass
            else:
                resolved = _resolve_var_ref(val_expr, variables)
                try:
                    variables[var_name] = eval(resolved, {"__builtins__": {}, "True": True, "False": False, "None": None})
                except Exception:
                    variables[var_name] = resolved
            continue

    return result


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
    multiline_key: str | None = None
    multiline_target: dict | None = None
    multiline_indent: int | None = None
    multiline_lines: list[str] = []

    def _flush_multiline():
        nonlocal multiline_key, multiline_target, multiline_indent, multiline_lines
        if multiline_key and multiline_target is not None:
            multiline_target[multiline_key] = "\n".join(multiline_lines).strip()
        elif multiline_key:
            result[multiline_key] = "\n".join(multiline_lines).strip()
        multiline_key = None
        multiline_target = None
        multiline_indent = None
        multiline_lines = []

    for line in block_lines:
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())

        if multiline_key is not None:
            if not stripped:
                multiline_lines.append("")
                continue
            if multiline_indent is None:
                multiline_indent = indent
            if indent >= multiline_indent:
                multiline_lines.append(stripped)
                continue
            else:
                _flush_multiline()

        if not stripped:
            continue

        if sub_indent is not None and indent <= sub_indent and current_sub is not None:
            current_key = None
            current_sub = None
            sub_indent = None

        m = re.match(r'^(\w[\w_-]*)\s*:\s*[|>]\s*$', stripped)
        if m:
            key = m.group(1)
            if current_sub is not None and sub_indent is not None and indent > sub_indent:
                multiline_key = key
                multiline_target = current_sub
            else:
                multiline_key = key
                multiline_target = None
            multiline_indent = None
            multiline_lines = []
            continue

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

    _flush_multiline()
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
            mem=data.get("mem", {}),
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
        "mem": state.mem,
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


def _get_active_topic(parsed: ParsedScript, state: ScriptState) -> ScriptTopic | None:
    if state.current_topic == "__start__" and parsed.start_agent:
        return parsed.start_agent
    elif state.current_topic in parsed.topics:
        return parsed.topics[state.current_topic]
    elif parsed.start_agent:
        state.current_topic = "__start__"
        return parsed.start_agent
    return None


def execute_script_logic(
    parsed: ParsedScript,
    state: ScriptState,
    max_steps: int = 10,
) -> ScriptExecutionResult:
    result = ScriptExecutionResult(final_topic=state.current_topic)
    result.topic_path.append(state.current_topic)
    vars_snapshot = dict(state.variables)

    for step_idx in range(max_steps):
        topic = _get_active_topic(parsed, state)
        if not topic:
            break

        result.steps.append(ExecutionStep(
            topic=topic.name, action="enter",
            detail=f"Evaluating topic '{topic.name}'"
        ))

        if not topic.reasoning_text:
            result.needs_llm = True
            break

        reasoning = evaluate_reasoning(
            topic.reasoning_text, state.variables, topic.actions
        )

        for var_name, new_val in state.variables.items():
            old_val = vars_snapshot.get(var_name)
            if old_val != new_val:
                result.changes.append(f"SET {var_name} = {new_val}")
                result.steps.append(ExecutionStep(
                    topic=topic.name, action="set",
                    detail=f"{var_name} = {json.dumps(new_val, ensure_ascii=False)}"
                ))
        vars_snapshot = dict(state.variables)

        if reasoning.transitions:
            target = reasoning.transitions[0]
            if target in parsed.topics:
                old_t = state.current_topic
                state.current_topic = target
                result.changes.append(f"TRANSITION {old_t} → {target}")
                result.topic_path.append(target)
                result.steps.append(ExecutionStep(
                    topic=old_t, action="transition",
                    detail=f"{old_t} → {target}"
                ))
                continue

        if reasoning.prompts:
            result.llm_instructions.extend(reasoning.prompts)
            result.needs_llm = True
            result.steps.append(ExecutionStep(
                topic=topic.name, action="llm_prompt",
                detail=f"Collected {len(reasoning.prompts)} instruction(s) for LLM"
            ))

        if reasoning.actions_to_run:
            result.actions_to_run.extend(reasoning.actions_to_run)
            for act in reasoning.actions_to_run:
                result.steps.append(ExecutionStep(
                    topic=topic.name, action="run_action",
                    detail=f"@actions.{act['name']} → {act.get('target', '')}"
                ))

        if not reasoning.is_procedural and not reasoning.prompts and not reasoning.transitions:
            result.needs_llm = True

        break

    result.final_topic = state.current_topic
    result.final_variables = dict(state.variables)
    return result


def build_execution_prompt(
    parsed: ParsedScript,
    state: ScriptState,
    exec_result: ScriptExecutionResult,
) -> str:
    parts = []

    agent_name = parsed.config.get("agent_name", parsed.config.get("agent_label", "Agent"))
    description = parsed.config.get("description", "")
    parts.append(f"You are **{agent_name}**.")
    if description:
        parts.append(f"**Role**: {description}")
    if parsed.system_instructions:
        parts.append(f"\n## Core Instructions\n{parsed.system_instructions}")

    vars_section = _render_variables_section(parsed, state)
    if vars_section:
        parts.append(f"\n{vars_section}")

    if state.mem:
        parts.append("\n## Conversation Memory")
        for mk, mv in state.mem.items():
            parts.append(f"- **{mk}**: {mv}")

    topic = _get_active_topic(parsed, state)
    if topic:
        parts.append(f"\n## Current Topic: `{topic.name}`")
        if topic.description:
            parts.append(f"*{topic.description}*")

        filtered_actions = {}
        if topic.actions:
            for aname, action in topic.actions.items():
                if action.available_when:
                    if _eval_condition(action.available_when, state.variables):
                        filtered_actions[aname] = action
                else:
                    filtered_actions[aname] = action
        if filtered_actions:
            temp = ScriptTopic(name=topic.name, actions=filtered_actions)
            asec = _render_actions_section(temp, state)
            if asec:
                parts.append(f"\n{asec}")

    if exec_result.llm_instructions:
        parts.append("\n## Your Task for This Turn")
        parts.append("The script engine has determined the following instructions for you:")
        for inst in exec_result.llm_instructions:
            parts.append(f"- {inst}")

    if exec_result.actions_to_run:
        parts.append("\n## Actions To Execute")
        parts.append("The script requires you to execute these actions NOW:")
        has_skill_target = False
        has_tool_target = False
        for act in exec_result.actions_to_run:
            target = act.get("target", "")
            if target:
                parts.append(f"- Call `{act['name']}` (target: `{target}`)")
                if target.startswith("skill://"):
                    has_skill_target = True
                elif target.startswith("tool://"):
                    has_tool_target = True
            else:
                parts.append(f"- Execute `{act['name']}`")
        if has_skill_target:
            parts.append("\n**Skill Execution**: For `skill://` targets, you MUST use `read_file` to load the skill file first (e.g., `skills/<skill-name>/SKILL.md`), then follow the skill's instructions to complete the action. Do NOT use generic tools like `web_search` as a substitute — the skill contains the specific logic and tools to use.")
        if has_tool_target:
            parts.append("\n**Tool Execution**: For `tool://` targets, call the tool directly using the standard tool-calling mechanism.")

    if exec_result.topic_path and len(exec_result.topic_path) > 1:
        parts.append(f"\n## Execution Path")
        parts.append(f"Script engine routed: {' → '.join(exec_result.topic_path)}")

    extractable = [n for n, v in parsed.variables.items() if v.mutable]
    if extractable:
        parts.append("\n## Variable Extraction")
        parts.append("If the user's message reveals a value for any variable, output `[SET variable = value]` at the END of your response:")
        for name in extractable:
            var = parsed.variables[name]
            cv = state.variables.get(name, var.default)
            parts.append(f"- `{name}` ({var.var_type}): current=`{json.dumps(cv, ensure_ascii=False)}`{f' — {var.description}' if var.description else ''}")

    if parsed.topics:
        tnav = _render_topics_nav(parsed, state.current_topic)
        if tnav:
            parts.append(f"\n{tnav}")
        parts.append("\nTo switch topics, output `[TRANSITION topic_name]` at the END of your response.")

    parts.append("\n## Memory Updates")
    parts.append("To remember important facts from this conversation, output `[MEM key = value]` at the END of your response.")
    parts.append("Use this for user preferences, context, or anything that should persist across turns.")

    parts.append("\n## Response Rules")
    parts.append("1. **ALWAYS include a natural language response** to the user — never respond with ONLY directives.")
    parts.append("2. Place all `[SET]`, `[TRANSITION]`, `[MEM]` directives at the END, after your user-facing message.")
    parts.append("3. Stay in character as defined by the script.")
    parts.append("4. **Action Execution**: When an action maps to a tool (`tool://`), call it using the standard tool-calling mechanism. When it maps to a skill (`skill://`), use `read_file` to load the skill first (path: `skills/<skill-name>/SKILL.md`), then follow its instructions. Do NOT substitute with generic tools.")

    welcome = parsed.system_messages.get("welcome", "")
    error_msg = parsed.system_messages.get("error", "")
    if welcome or error_msg:
        parts.append(f"\n## System Messages")
        if welcome:
            parts.append(f"- Welcome: \"{welcome}\"")
        if error_msg:
            parts.append(f"- Error fallback: \"{error_msg}\"")

    return "\n".join(parts)


def process_response_v2(
    response_text: str,
    state: ScriptState,
    parsed: ParsedScript,
) -> tuple[str, list[str]]:
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
        elif topic_name in ("topic_selector", "__start__"):
            state.current_topic = "__start__"
            changes.append(f"TRANSITION → __start__")
        clean_text = clean_text.replace(m.group(0), "").strip()

    for m in re.finditer(r'\[MEM\s+(\w+)\s*=\s*(.+?)\]', response_text):
        mem_key = m.group(1)
        mem_val = m.group(2).strip().strip('"').strip("'")
        state.mem[mem_key] = mem_val
        changes.append(f"MEM {mem_key} = {mem_val}")
        clean_text = clean_text.replace(m.group(0), "").strip()

    return clean_text, changes


def build_system_prompt(parsed: ParsedScript, state: ScriptState) -> str:
    parts = []

    agent_name = parsed.config.get("agent_name", parsed.config.get("agent_label", "Agent"))
    description = parsed.config.get("description", "")

    parts.append(f"You are **{agent_name}**.")
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

    reasoning_result = None
    if active_topic and active_topic.reasoning_text:
        reasoning_result = evaluate_reasoning(
            active_topic.reasoning_text, dict(state.variables),
            active_topic.actions if active_topic else None
        )

        if reasoning_result.transitions:
            target = reasoning_result.transitions[0]
            if target in parsed.topics:
                state.current_topic = target
                state.pending_transitions = reasoning_result.transitions
                active_topic = parsed.topics[target]
                current_topic_name = target
                reasoning_result = evaluate_reasoning(
                    active_topic.reasoning_text, dict(state.variables),
                    active_topic.actions
                )

        if reasoning_result.actions_to_run:
            state.pending_actions = reasoning_result.actions_to_run

    variables_section = _render_variables_section(parsed, state)
    if variables_section:
        parts.append(f"\n{variables_section}")

    if active_topic:
        parts.append(f"\n## Current Topic: `{active_topic.name}`")
        if active_topic.description:
            parts.append(f"*{active_topic.description}*")

        filtered_actions = {}
        if active_topic.actions:
            for aname, action in active_topic.actions.items():
                if action.available_when:
                    if _eval_condition(action.available_when, state.variables):
                        filtered_actions[aname] = action
                else:
                    filtered_actions[aname] = action

        if filtered_actions:
            temp_topic = ScriptTopic(name=active_topic.name, actions=filtered_actions)
            actions_section = _render_actions_section(temp_topic, state)
            if actions_section:
                parts.append(f"\n{actions_section}")

        if reasoning_result and reasoning_result.prompts:
            parts.append("\n## Active Instructions")
            parts.append("Follow these instructions for the current conversation turn:")
            for prompt in reasoning_result.prompts:
                parts.append(f"- {prompt}")

        if reasoning_result and reasoning_result.actions_to_run:
            parts.append("\n## Actions To Execute")
            parts.append("The script requires you to execute these actions now:")
            for act in reasoning_result.actions_to_run:
                target = act.get("target", "")
                if target:
                    parts.append(f"- Call `{act['name']}` (target: `{target}`)")
                else:
                    parts.append(f"- Execute `{act['name']}`")

    if active_topic and active_topic.name == "__start__" or (parsed.start_agent and state.current_topic == "__start__"):
        if parsed.start_agent and parsed.start_agent.actions:
            routing_actions = parsed.start_agent.actions
            parts.append("\n## Topic Routing")
            parts.append("Based on the user's intent, route to one of these topics:")
            for aname, action in routing_actions.items():
                m = re.match(r'@utils\.transition to @topic\.(\w+)', action.target or "")
                if m:
                    topic_name = m.group(1)
                    avail = ""
                    if action.available_when:
                        avail = f" (available when {action.available_when})"
                    parts.append(f"- **{topic_name}**: {action.description}{avail}")

    topics_nav = _render_topics_nav(parsed, current_topic_name)
    if topics_nav:
        parts.append(f"\n{topics_nav}")

    parts.append("""
## Execution Rules
1. **ALWAYS respond to the user with visible text** — never respond with ONLY directives. Every response must contain a human-readable message. Place directives (`[SET]`/`[TRANSITION]`) AFTER your user-facing message.
2. **Variable Updates**: When you learn a variable value from the user's message, append `[SET variable_name = value]` on its own line at the END of your response.
3. **Topic Transitions**: To switch topics, append `[TRANSITION topic_name]` on its own line at the END of your response.
4. **Action Execution**: When an action maps to a tool (tool://), call it using the standard tool-calling mechanism. When it maps to a skill (skill://), use `read_file` to load the skill first.
5. **Stay in character** as defined by the script configuration.
6. **Welcome message**: If the conversation has just started and no user message was sent yet, greet the user using the welcome message below.""")

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


async def build_evolver_context(
    agent_id: uuid.UUID,
    agent_name: str,
    session_id: str = "",
) -> tuple[str, ScriptState | None]:
    script_text = await get_script_for_agent(agent_id)
    if not script_text:
        return "", None

    parsed = parse_script(script_text)

    is_new = False
    state = await load_state(agent_id, session_id)
    if state is None:
        state = init_state(parsed)
        is_new = True

    topic_before = state.current_topic
    system_prompt = build_system_prompt(parsed, state)

    if is_new or state.current_topic != topic_before or state.pending_actions:
        await save_state(agent_id, state, session_id)

    return system_prompt, state


async def get_evolver_welcome(agent_id: uuid.UUID) -> str | None:
    script_text = await get_script_for_agent(agent_id)
    if not script_text:
        return None
    parsed = parse_script(script_text)
    welcome = parsed.system_messages.get("welcome", "")
    return welcome if welcome else None
