"""Agent Script Runtime — clawevolver-compatible parser & executor.

Implements the Salesforce Agentforce-style Agent Script language used
by the clawevolver project. Whitespace-sensitive (2-space indent).

Architecture:
    source text
      ─► Lexer       (line-based, indent-aware, strips comments)
      ─► Parser      (recursive descent ─► typed AST)
      ─► Validator   (light semantic checks; tolerant)
      ─► Executor    (tree-walk; runs deterministic logic, collects
                       prompts/actions/transitions for the LLM layer)
      ─► PromptBuilder
      ─► ResponsePostProcessor  ([SET]/[TRANSITION]/[MEM])

Public API (stable, consumed by evolver_runtime.py):
    parse_script, init_state, execute_script_logic,
    build_execution_prompt, process_response_v2, evaluate_reasoning,
    load_state, save_state, get_script_for_agent, get_evolver_welcome
"""

from __future__ import annotations

import ast as _pyast
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════
# 1.  AST / data classes  (field names preserved for backwards-compat)
# ════════════════════════════════════════════════════════════════════════


@dataclass
class ScriptAction:
    """Action declaration inside a topic's `actions:` block."""
    name: str
    description: str = ""
    target: str = ""
    inputs: dict[str, dict] = field(default_factory=dict)
    outputs: dict[str, dict] = field(default_factory=dict)
    available_when: str | None = None


# ── Statement nodes (procedural blocks) ────────────────────────────────


@dataclass
class IfStmt:
    condition: str
    then_body: list = field(default_factory=list)
    elif_branches: list = field(default_factory=list)  # list[tuple[str, list]]
    else_body: list = field(default_factory=list)


@dataclass
class LetStmt:
    name: str
    expr: str


@dataclass
class SetVarStmt:
    name: str          # @variables.<name>
    expr: str          # may be `@outputs.<field>` for action chaining


@dataclass
class PromptStmt:
    """A `| natural language ...` line collected for the LLM."""
    template: str


@dataclass
class RunActionStmt:
    action_name: str
    params: dict[str, str] = field(default_factory=dict)          # name -> expr text
    output_mappings: dict[str, str] = field(default_factory=dict)  # var_name -> output field
    chained: list = field(default_factory=list)                    # list[RunActionStmt]


@dataclass
class TransitionStmt:
    topic_name: str


@dataclass
class StopStmt:
    pass


@dataclass
class ReasoningBlock:
    """`reasoning:` block. `mode` is 'procedural' for `:->` else 'llm'."""
    mode: str = "llm"                      # 'llm' | 'procedural'
    body: list = field(default_factory=list)   # list of Stmt nodes
    raw_actions: dict[str, ScriptAction] = field(default_factory=dict)
    raw_text: str = ""                     # preserved for debugging / fallback


@dataclass
class ScriptTopic:
    name: str
    description: str = ""
    actions: dict[str, ScriptAction] = field(default_factory=dict)
    reasoning: ReasoningBlock | None = None
    after_reasoning: list = field(default_factory=list)   # list[Stmt]
    # Backwards-compat shims (read by older callsites):
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
    user_input_handler: str = ""
    raw: str = ""
    parse_errors: list[str] = field(default_factory=list)


# ── Runtime state ──────────────────────────────────────────────────────


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


@dataclass
class ReasoningResult:
    """Legacy shape kept for older callers of `evaluate_reasoning()`."""
    prompts: list[str] = field(default_factory=list)
    transitions: list[str] = field(default_factory=list)
    actions_to_run: list[dict] = field(default_factory=list)
    is_procedural: bool = False
    stopped: bool = False
    local_vars: dict[str, Any] = field(default_factory=dict)


# ════════════════════════════════════════════════════════════════════════
# 2.  Safe expression evaluator
#
#     Supports the operators in the clawevolver Agent Script spec:
#       Comparison: == != < <= > >= is "is not"
#       Logical:    and or not
#       Arithmetic: + -
#       Null:       "is None" / "is not None"
#       Refs:       @variables.x, @outputs.x, local names, literals
#       Constants:  True / False / None  (also true/false for tolerance)
# ════════════════════════════════════════════════════════════════════════


_AT_REF_RE = re.compile(r'@(variables|outputs|utils|topic|actions)\.([A-Za-z_]\w*)')


def _preprocess_expr(expr: str) -> tuple[str, dict[str, tuple[str, str]]]:
    """Replace @ns.name with safe identifiers; return (text, sentinel_map).

    sentinel_map: sentinel_name -> (namespace, name)
    """
    mapping: dict[str, tuple[str, str]] = {}
    counter = [0]

    def _sub(m: re.Match) -> str:
        ns, name = m.group(1), m.group(2)
        sid = f"__ref_{counter[0]}__"
        counter[0] += 1
        mapping[sid] = (ns, name)
        return sid

    text = _AT_REF_RE.sub(_sub, expr)
    # Tolerate `true`/`false` as boolean literals
    text = re.sub(r'\btrue\b', 'True', text)
    text = re.sub(r'\bfalse\b', 'False', text)
    return text, mapping


def _eval_node(node, env: dict) -> Any:
    """Evaluate a Python AST node restricted to whitelisted operations."""
    if isinstance(node, _pyast.Expression):
        return _eval_node(node.body, env)
    if isinstance(node, _pyast.Constant):
        return node.value
    if isinstance(node, _pyast.Name):
        if node.id in env:
            return env[node.id]
        raise NameError(f"unknown name: {node.id}")
    if isinstance(node, _pyast.UnaryOp):
        if isinstance(node.op, _pyast.Not):
            return not _eval_node(node.operand, env)
        if isinstance(node.op, _pyast.USub):
            return -_eval_node(node.operand, env)
        if isinstance(node.op, _pyast.UAdd):
            return +_eval_node(node.operand, env)
        raise ValueError(f"unsupported unary op: {type(node.op).__name__}")
    if isinstance(node, _pyast.BoolOp):
        vals = [_eval_node(v, env) for v in node.values]
        if isinstance(node.op, _pyast.And):
            res = True
            for v in vals:
                res = res and v
                if not res:
                    return res
            return res
        if isinstance(node.op, _pyast.Or):
            res = False
            for v in vals:
                res = res or v
                if res:
                    return res
            return res
        raise ValueError(f"unsupported bool op: {type(node.op).__name__}")
    if isinstance(node, _pyast.BinOp):
        l, r = _eval_node(node.left, env), _eval_node(node.right, env)
        if isinstance(node.op, _pyast.Add):
            try:
                return l + r
            except TypeError:
                return f"{l}{r}"
        if isinstance(node.op, _pyast.Sub):
            return l - r
        if isinstance(node.op, _pyast.Mult):
            return l * r
        if isinstance(node.op, _pyast.Div):
            return l / r if r != 0 else 0
        raise ValueError(f"unsupported bin op: {type(node.op).__name__}")
    if isinstance(node, _pyast.Compare):
        left = _eval_node(node.left, env)
        for op, comp_node in zip(node.ops, node.comparators):
            right = _eval_node(comp_node, env)
            if isinstance(op, _pyast.Eq):
                ok = left == right
            elif isinstance(op, _pyast.NotEq):
                ok = left != right
            elif isinstance(op, _pyast.Lt):
                ok = _safe_cmp(left, right, '<')
            elif isinstance(op, _pyast.LtE):
                ok = _safe_cmp(left, right, '<=')
            elif isinstance(op, _pyast.Gt):
                ok = _safe_cmp(left, right, '>')
            elif isinstance(op, _pyast.GtE):
                ok = _safe_cmp(left, right, '>=')
            elif isinstance(op, _pyast.Is):
                ok = left is right
            elif isinstance(op, _pyast.IsNot):
                ok = left is not right
            elif isinstance(op, _pyast.In):
                ok = left in right
            elif isinstance(op, _pyast.NotIn):
                ok = left not in right
            else:
                raise ValueError(f"unsupported cmp: {type(op).__name__}")
            if not ok:
                return False
            left = right
        return True
    raise ValueError(f"unsupported node: {type(node).__name__}")


def _safe_cmp(a, b, op: str) -> bool:
    """Compare with type coercion to avoid TypeErrors on mixed types."""
    try:
        if op == '<':
            return a < b
        if op == '<=':
            return a <= b
        if op == '>':
            return a > b
        if op == '>=':
            return a >= b
    except TypeError:
        try:
            af, bf = float(a), float(b)
            if op == '<':
                return af < bf
            if op == '<=':
                return af <= bf
            if op == '>':
                return af > bf
            if op == '>=':
                return af >= bf
        except (TypeError, ValueError):
            return False
    return False


def _build_env(variables: dict[str, Any],
               local_vars: dict[str, Any] | None,
               outputs: dict[str, Any] | None,
               sentinels: dict[str, tuple[str, str]]) -> dict:
    env: dict[str, Any] = {"True": True, "False": False, "None": None}
    if local_vars:
        env.update(local_vars)
    for sid, (ns, name) in sentinels.items():
        if ns == "variables":
            env[sid] = variables.get(name, "")
        elif ns == "outputs":
            env[sid] = (outputs or {}).get(name, "")
        else:
            # @topic / @actions / @utils — surface as the literal name
            env[sid] = name
    return env


def _evaluate(expr: str, variables: dict[str, Any],
              local_vars: dict[str, Any] | None = None,
              outputs: dict[str, Any] | None = None) -> Any:
    """Safely evaluate an Agent Script expression."""
    if expr is None:
        return None
    text = expr.strip()
    if not text:
        return ""
    text, sentinels = _preprocess_expr(text)
    try:
        tree = _pyast.parse(text, mode="eval")
    except SyntaxError:
        # Fall back: treat as a literal string with template interpolation
        return text
    env = _build_env(variables, local_vars, outputs, sentinels)
    try:
        return _eval_node(tree, env)
    except Exception as e:
        logger.debug(f"[ScriptRuntime] expr eval failed: {expr!r} -> {e}")
        return ""


def _evaluate_condition(expr: str, variables: dict[str, Any],
                        local_vars: dict[str, Any] | None = None) -> bool:
    """Same as _evaluate but coerces the result to bool with safe default."""
    if not expr or not expr.strip():
        return True
    try:
        result = _evaluate(expr, variables, local_vars)
        return bool(result)
    except Exception as e:
        logger.debug(f"[ScriptRuntime] cond eval failed: {expr!r} -> {e}")
        return False


# ── Template interpolation:  "Hello {!@variables.user_name}!" ──────────


def _resolve_template(text: str, variables: dict[str, Any],
                       local_vars: dict[str, Any] | None = None,
                       system_messages: dict[str, Any] | None = None) -> str:
    if not text or '{!' not in text:
        return text
    out: list[str] = []
    i = 0
    while i < len(text):
        if text[i:i + 2] == '{!':
            depth = 1
            j = i + 2
            while j < len(text) and depth > 0:
                if text[j] == '{':
                    depth += 1
                elif text[j] == '}':
                    depth -= 1
                j += 1
            if depth != 0:
                out.append(text[i:])
                break
            expr = text[i + 2:j - 1]
            # Special-case `system.messages.<key>` since it isn't an @-ref
            if expr.startswith("system.messages."):
                key = expr[len("system.messages."):].split(".")[0]
                val = (system_messages or {}).get(key, "")
                out.append(str(val))
            else:
                val = _evaluate(expr, variables, local_vars)
                out.append("" if val is None else str(val))
            i = j
        else:
            out.append(text[i])
            i += 1
    return ''.join(out)


# Public alias kept for older callsites
_resolve_var_ref = _resolve_template


# ════════════════════════════════════════════════════════════════════════
# 3.  Lexer / Parser
# ════════════════════════════════════════════════════════════════════════


def _strip_comment(line: str) -> str:
    """Strip `# ...` comments, keeping `#` inside double-quoted strings."""
    in_quote = False
    out = []
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == '"':
            in_quote = not in_quote
            out.append(ch)
        elif ch == '#' and not in_quote:
            break
        else:
            out.append(ch)
        i += 1
    return ''.join(out).rstrip()


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip())


def _unquote(s: str) -> str:
    s = s.strip()
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        return s[1:-1]
    return s


def parse_script(script_text: str) -> ParsedScript:
    """Parse Agent Script source into a typed AST."""
    result = ParsedScript(raw=script_text)
    if not script_text or not script_text.strip():
        return result

    # Pre-process: keep original lines for indent calc, but stripped for matching
    raw_lines = script_text.split("\n")
    # Strip comments line-by-line (preserves indentation)
    lines = []
    for ln in raw_lines:
        stripped_no_comment = _strip_comment(ln)
        # Preserve leading whitespace
        lead = ln[:len(ln) - len(ln.lstrip())]
        body = stripped_no_comment.lstrip()
        lines.append(lead + body if body else "")

    i = 0
    n = len(lines)

    def _collect_block(start: int, header_indent: int) -> tuple[list[str], int]:
        """Collect contiguous indented lines belonging to the block."""
        block: list[str] = []
        j = start
        while j < n:
            line = lines[j]
            if not line.strip():
                block.append("")
                j += 1
                continue
            if _indent_of(line) <= header_indent:
                break
            block.append(line)
            j += 1
        # Trim trailing blanks
        while block and not block[-1].strip():
            block.pop()
        return block, j

    while i < n:
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue

        head_indent = _indent_of(line)

        if stripped == "config:":
            block, i = _collect_block(i + 1, head_indent)
            result.config = _parse_yaml_like(block)
            continue

        if stripped == "system:":
            block, i = _collect_block(i + 1, head_indent)
            sys_data = _parse_yaml_like(block)
            result.system_messages = sys_data.get("messages", {}) if isinstance(
                sys_data.get("messages"), dict
            ) else {}
            instr = sys_data.get("instructions", "")
            result.system_instructions = instr if isinstance(instr, str) else ""
            continue

        if stripped == "variables:":
            block, i = _collect_block(i + 1, head_indent)
            result.variables = _parse_variables(block)
            continue

        m = re.match(r'^start_agent\s+(\w+)\s*:\s*$', stripped)
        if m:
            block, i = _collect_block(i + 1, head_indent)
            result.start_agent = _parse_topic(m.group(1), block)
            continue

        m = re.match(r'^topic\s+(\w+)\s*:\s*$', stripped)
        if m:
            block, i = _collect_block(i + 1, head_indent)
            result.topics[m.group(1)] = _parse_topic(m.group(1), block)
            continue

        if stripped.startswith("user_input_handler:"):
            block, i = _collect_block(i + 1, head_indent)
            result.user_input_handler = "\n".join(block)
            continue

        # Unknown / legacy section — skip its block
        if stripped.endswith(":"):
            _, i = _collect_block(i + 1, head_indent)
            continue
        i += 1

    return result


# ── YAML-ish parser for config / system blocks ─────────────────────────


def _parse_yaml_like(block: list[str]) -> dict:
    """Parse a small subset of YAML used by config/system blocks."""
    result: dict[str, Any] = {}
    stack: list[tuple[int, dict]] = [(-1, result)]
    pending_multiline: dict | None = None

    for line in block:
        if not line.strip():
            continue
        indent = _indent_of(line)
        stripped = line.strip()

        # Multi-line scalar continuation (`|` or `>` block scalar)
        if pending_multiline and indent > pending_multiline["base_indent"]:
            pending_multiline["lines"].append(stripped)
            continue
        elif pending_multiline:
            pending_multiline["target"][pending_multiline["key"]] = "\n".join(
                pending_multiline["lines"]
            ).strip()
            pending_multiline = None

        # Pop deeper scopes
        while stack and indent <= stack[-1][0]:
            stack.pop()
        if not stack:
            stack = [(-1, result)]
        parent = stack[-1][1]

        # `key: |` or `key: >`  → multi-line scalar
        m = re.match(r'^([\w_-]+)\s*:\s*([|>])\s*$', stripped)
        if m:
            pending_multiline = {
                "target": parent, "key": m.group(1),
                "base_indent": indent, "lines": []
            }
            continue

        # `key:` (start of nested dict)
        m = re.match(r'^([\w_-]+)\s*:\s*$', stripped)
        if m:
            sub: dict = {}
            parent[m.group(1)] = sub
            stack.append((indent, sub))
            continue

        # `key: value`
        m = re.match(r'^([\w_-]+)\s*:\s*(.+)$', stripped)
        if m:
            parent[m.group(1)] = _unquote(m.group(2))
            continue

    if pending_multiline:
        pending_multiline["target"][pending_multiline["key"]] = "\n".join(
            pending_multiline["lines"]
        ).strip()

    return result


# ── Variables block ────────────────────────────────────────────────────


def _coerce_default(var_type: str, raw: str) -> Any:
    raw = raw.strip()
    s = _unquote(raw)
    if var_type in ("boolean", "bool"):
        return s.strip().lower() in ("true", "1", "yes")
    if var_type in ("number", "int", "float"):
        try:
            return float(s) if "." in s else int(s)
        except ValueError:
            return 0
    return s


def _parse_variables(block: list[str]) -> dict[str, ScriptVariable]:
    variables: dict[str, ScriptVariable] = {}
    current: ScriptVariable | None = None
    for line in block:
        stripped = line.strip()
        if not stripped:
            continue
        m = re.match(
            r'^(\w+)\s*:\s*(mutable\s+|readonly\s+)?(\w+)\s*=\s*(.+)$', stripped
        )
        if m:
            name = m.group(1)
            modifier = (m.group(2) or "").strip()
            mutable = (modifier != "readonly")
            var_type = m.group(3)
            default = _coerce_default(var_type, m.group(4))
            current = ScriptVariable(
                name=name, var_type=var_type, default=default, mutable=mutable
            )
            variables[name] = current
            continue
        if stripped.startswith("description:") and current:
            current.description = _unquote(stripped.split(":", 1)[1])
            continue
    return variables


# ── Topic / Reasoning / Actions ────────────────────────────────────────


def _parse_topic(name: str, block: list[str]) -> ScriptTopic:
    topic = ScriptTopic(name=name)
    i = 0
    n = len(block)

    def _section_block(start: int, header_indent: int) -> tuple[list[str], int]:
        out = []
        j = start
        while j < n:
            ln = block[j]
            if not ln.strip():
                out.append("")
                j += 1
                continue
            if _indent_of(ln) <= header_indent:
                break
            out.append(ln)
            j += 1
        while out and not out[-1].strip():
            out.pop()
        return out, j

    while i < n:
        line = block[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        head_indent = _indent_of(line)

        if stripped.startswith("description:"):
            topic.description = _unquote(stripped.split(":", 1)[1])
            i += 1
            continue
        if stripped == "actions:":
            sub, i = _section_block(i + 1, head_indent)
            topic.actions = _parse_actions(sub)
            continue
        if stripped == "reasoning:":
            sub, i = _section_block(i + 1, head_indent)
            topic.reasoning = _parse_reasoning(sub)
            topic.reasoning_text = "\n".join(sub)
            # If the reasoning block declared its own actions, merge into topic
            if topic.reasoning and topic.reasoning.raw_actions:
                for an, ad in topic.reasoning.raw_actions.items():
                    topic.actions.setdefault(an, ad)
            continue
        if stripped == "after_reasoning:":
            sub, i = _section_block(i + 1, head_indent)
            topic.after_reasoning = _parse_stmts(sub)
            topic.after_reasoning_text = "\n".join(sub)
            continue
        i += 1

    return topic


def _parse_actions(block: list[str]) -> dict[str, ScriptAction]:
    """Parse a topic's `actions:` block (action declarations w/ inputs/outputs)."""
    actions: dict[str, ScriptAction] = {}
    current: ScriptAction | None = None
    section: str = ""           # "" | "inputs" | "outputs"
    section_indent = -1
    last_io_key: str | None = None
    base_indent = -1
    action_indent = -1

    for line in block:
        stripped = line.strip()
        if not stripped:
            continue
        indent = _indent_of(line)
        if base_indent < 0:
            base_indent = indent

        # Leaving the inputs/outputs sub-section?
        if section and indent <= section_indent:
            section, section_indent, last_io_key = "", -1, None

        # Top-level action declaration:  "name:"  or  "name: @actions.X"
        if indent == base_indent:
            m = re.match(r'^(\w[\w_-]*)\s*:\s*(.*)$', stripped)
            if m:
                aname, rest = m.group(1), m.group(2).strip()
                current = ScriptAction(name=aname)
                actions[aname] = current
                action_indent = indent
                section = ""
                # Inline transition shorthand: `go_to_x: @utils.transition to @topic.X`
                if rest.startswith("@utils.transition") or rest.startswith("transition to"):
                    current.target = rest
                continue

        if not current:
            continue

        # Sub-keys at one level deeper
        if indent > action_indent:
            m = re.match(r'^([\w_-]+)\s*:\s*(.*)$', stripped)
            if m:
                key, val = m.group(1), m.group(2).strip()
                if key == "description" and not section:
                    current.description = _unquote(val)
                    continue
                if key == "target":
                    current.target = _unquote(val)
                    continue
                if key == "inputs" and not val:
                    section, section_indent = "inputs", indent
                    continue
                if key == "outputs" and not val:
                    section, section_indent = "outputs", indent
                    continue
                if section in ("inputs", "outputs"):
                    target_dict = current.inputs if section == "inputs" else current.outputs
                    if val:
                        target_dict[key] = {"type": _unquote(val), "description": ""}
                        last_io_key = key
                    elif key == "description" and last_io_key:
                        target_dict[last_io_key]["description"] = _unquote(val)
                    else:
                        # nested key without value → declare as plain field
                        target_dict[key] = {"type": "string", "description": ""}
                        last_io_key = key
                    continue

            if stripped.startswith("available when"):
                current.available_when = stripped[len("available when"):].strip()
                continue
            if stripped.startswith("description:") and section in ("inputs", "outputs") and last_io_key:
                target_dict = current.inputs if section == "inputs" else current.outputs
                target_dict[last_io_key]["description"] = _unquote(
                    stripped.split(":", 1)[1]
                )
                continue
    return actions


# ── Reasoning block — produces a typed AST of statements ───────────────


def _parse_reasoning(block: list[str]) -> ReasoningBlock:
    """Parse a `reasoning:` block.

    Distinguishes:
      `instructions:|`  → mode='llm', body is the raw prompt text wrapped
                           in a single PromptStmt
      `instructions:->` → mode='procedural', body is parsed AST
      (no `instructions:` header) → infer from leading lines
    """
    rb = ReasoningBlock()
    if not block:
        return rb
    rb.raw_text = "\n".join(block)

    i = 0
    n = len(block)
    instructions_lines: list[str] = []
    actions_block: list[str] = []
    instructions_indent = -1
    saw_explicit_mode = False

    while i < n:
        line = block[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        indent = _indent_of(line)

        if stripped == "instructions:|" or stripped.startswith("instructions: |"):
            rb.mode = "llm"
            saw_explicit_mode = True
            i += 1
            continue
        if stripped == "instructions:->" or stripped.startswith("instructions: ->"):
            rb.mode = "procedural"
            saw_explicit_mode = True
            i += 1
            continue
        if stripped == "instructions:":
            saw_explicit_mode = True
            instructions_indent = indent
            i += 1
            continue
        if stripped == "actions:":
            i += 1
            base = indent
            while i < n:
                ln = block[i]
                if not ln.strip():
                    actions_block.append("")
                    i += 1
                    continue
                if _indent_of(ln) <= base:
                    break
                actions_block.append(ln)
                i += 1
            continue

        instructions_lines.append(line)
        i += 1

    if actions_block:
        rb.raw_actions = _parse_actions(actions_block)

    # Mode inference if not explicit
    if not saw_explicit_mode:
        for ln in instructions_lines:
            s = ln.strip()
            if re.match(r'^(if |elif |else:|let |set |run |transition |stop$|return$|try:|except)', s):
                rb.mode = "procedural"
                break
        else:
            rb.mode = "llm"

    if rb.mode == "llm":
        # Wrap the entire instructions blob into one PromptStmt
        prompt_text = "\n".join(s.strip() for s in instructions_lines if s.strip())
        if prompt_text:
            rb.body = [PromptStmt(template=prompt_text)]
    else:
        rb.body = _parse_stmts(instructions_lines)

    return rb


# ── Statement parser (procedural reasoning / after_reasoning) ──────────


_RUN_RE = re.compile(r'^run\s+@actions\.(\w+)\s*$')
_TRANSITION_RE = re.compile(
    r'^(?:@utils\.)?transition\s+to\s+@topic\.(\w+)\s*$'
)
_INLINE_TRANSITION_RE = re.compile(
    r'^(?:\w+:\s*)?@utils\.transition\s+to\s+@topic\.(\w+)\s*$'
)
_LET_RE = re.compile(r'^let\s+(\w+)\s*=\s*(.+)$')
_SET_RE = re.compile(r'^set\s+@variables\.(\w+)\s*=\s*(.+)$')
_WITH_RE = re.compile(r'^with\s+(\w+)\s*=\s*(.+)$')
_IF_RE = re.compile(r'^if\s+(.+):\s*$')
_ELIF_RE = re.compile(r'^elif\s+(.+):\s*$')
_ELSE_RE = re.compile(r'^else\s*:\s*$')


def _parse_stmts(lines: list[str]) -> list:
    """Indent-aware statement parser → list of Stmt nodes."""
    # Normalise: drop blank and comment lines for indentation tracking
    filtered: list[tuple[int, str]] = []
    for raw in lines:
        if not raw.strip():
            continue
        filtered.append((_indent_of(raw), raw.strip()))
    if not filtered:
        return []

    base = min(ind for ind, _ in filtered)
    pos = [0]  # pointer into filtered

    def _parse_block(min_indent: int) -> list:
        out: list = []
        last_run: RunActionStmt | None = None
        while pos[0] < len(filtered):
            indent, text = filtered[pos[0]]
            if indent < min_indent:
                return out

            # if / elif / else
            if (m := _IF_RE.match(text)):
                pos[0] += 1
                node = IfStmt(condition=m.group(1).strip())
                node.then_body = _parse_block(indent + 1)
                while pos[0] < len(filtered):
                    indent2, text2 = filtered[pos[0]]
                    if indent2 != indent:
                        break
                    if (em := _ELIF_RE.match(text2)):
                        pos[0] += 1
                        body = _parse_block(indent + 1)
                        node.elif_branches.append((em.group(1).strip(), body))
                        continue
                    if _ELSE_RE.match(text2):
                        pos[0] += 1
                        node.else_body = _parse_block(indent + 1)
                        break
                    break
                out.append(node)
                last_run = None
                continue

            if text == "stop" or text == "return":
                pos[0] += 1
                out.append(StopStmt())
                last_run = None
                continue

            if text == "try:":
                pos[0] += 1
                body = _parse_block(indent + 1)
                # Skip any `except ...:` blocks
                while pos[0] < len(filtered):
                    indent2, text2 = filtered[pos[0]]
                    if indent2 == indent and re.match(r'^except\b', text2):
                        pos[0] += 1
                        _parse_block(indent + 1)  # discard
                        continue
                    break
                out.extend(body)
                last_run = None
                continue

            if (m := _LET_RE.match(text)):
                pos[0] += 1
                out.append(LetStmt(name=m.group(1), expr=m.group(2).strip()))
                last_run = None
                continue

            if (m := _SET_RE.match(text)):
                pos[0] += 1
                vname, vexpr = m.group(1), m.group(2).strip()
                # `set @variables.x = @outputs.y` chains onto the previous run
                out_m = re.match(r'^@outputs\.(\w+)$', vexpr)
                if out_m and last_run is not None:
                    last_run.output_mappings[vname] = out_m.group(1)
                else:
                    out.append(SetVarStmt(name=vname, expr=vexpr))
                continue

            if (m := _WITH_RE.match(text)):
                pos[0] += 1
                if last_run is not None:
                    last_run.params[m.group(1)] = m.group(2).strip()
                continue

            if (m := _RUN_RE.match(text)):
                pos[0] += 1
                node = RunActionStmt(action_name=m.group(1))
                # Greedily consume `with ...` and `set @variables.x = @outputs.y`
                # at deeper indent (action arguments)
                while pos[0] < len(filtered):
                    ind2, text2 = filtered[pos[0]]
                    if ind2 <= indent:
                        break
                    if (wm := _WITH_RE.match(text2)):
                        pos[0] += 1
                        node.params[wm.group(1)] = wm.group(2).strip()
                        continue
                    if (sm := _SET_RE.match(text2)):
                        pos[0] += 1
                        out_m = re.match(r'^@outputs\.(\w+)$', sm.group(2).strip())
                        if out_m:
                            node.output_mappings[sm.group(1)] = out_m.group(1)
                        continue
                    if (rm := _RUN_RE.match(text2)):
                        pos[0] += 1
                        chained = RunActionStmt(action_name=rm.group(1))
                        node.chained.append(chained)
                        # consume chained's params
                        while pos[0] < len(filtered):
                            ind3, text3 = filtered[pos[0]]
                            if ind3 <= ind2:
                                break
                            if (wm2 := _WITH_RE.match(text3)):
                                pos[0] += 1
                                chained.params[wm2.group(1)] = wm2.group(2).strip()
                                continue
                            if (sm2 := _SET_RE.match(text3)):
                                pos[0] += 1
                                om2 = re.match(r'^@outputs\.(\w+)$', sm2.group(2).strip())
                                if om2:
                                    chained.output_mappings[sm2.group(1)] = om2.group(1)
                                continue
                            break
                        continue
                    break
                out.append(node)
                last_run = node
                continue

            if (m := _TRANSITION_RE.match(text)):
                pos[0] += 1
                out.append(TransitionStmt(topic_name=m.group(1)))
                last_run = None
                continue

            if text.startswith("|"):
                pos[0] += 1
                prompt = text[1:].lstrip()
                out.append(PromptStmt(template=prompt))
                last_run = None
                continue

            # Inline action mapping inside a routing topic:
            #   `go_to_x: @utils.transition to @topic.X`
            if (m := _INLINE_TRANSITION_RE.match(text)):
                pos[0] += 1
                out.append(TransitionStmt(topic_name=m.group(1)))
                last_run = None
                continue

            # Unknown line — skip
            pos[0] += 1
        return out

    return _parse_block(base)


# ════════════════════════════════════════════════════════════════════════
# 4.  Executor (tree-walk)
# ════════════════════════════════════════════════════════════════════════


@dataclass
class _ExecCtx:
    """Mutable bag passed through statement execution."""
    parsed: ParsedScript
    state: ScriptState
    topic: ScriptTopic
    local_vars: dict[str, Any] = field(default_factory=dict)
    prompts: list[str] = field(default_factory=list)
    actions: list[dict] = field(default_factory=list)
    transitions: list[str] = field(default_factory=list)
    changes: list[str] = field(default_factory=list)
    stopped: bool = False
    last_action: dict | None = None
    # Direct-execution context (None ⇒ skip tool/skill calls, collect for LLM)
    agent_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    session_id: str = ""


async def _exec_stmts(stmts: list, ctx: _ExecCtx) -> None:
    for stmt in stmts:
        if ctx.stopped:
            return
        await _exec_stmt(stmt, ctx)


async def _exec_stmt(stmt, ctx: _ExecCtx) -> None:
    if isinstance(stmt, IfStmt):
        if _evaluate_condition(stmt.condition, ctx.state.variables, ctx.local_vars):
            await _exec_stmts(stmt.then_body, ctx)
            return
        for cond, body in stmt.elif_branches:
            if _evaluate_condition(cond, ctx.state.variables, ctx.local_vars):
                await _exec_stmts(body, ctx)
                return
        if stmt.else_body:
            await _exec_stmts(stmt.else_body, ctx)
        return

    if isinstance(stmt, LetStmt):
        ctx.local_vars[stmt.name] = _evaluate(
            stmt.expr, ctx.state.variables, ctx.local_vars
        )
        return

    if isinstance(stmt, SetVarStmt):
        new_val = _evaluate(stmt.expr, ctx.state.variables, ctx.local_vars)
        old = ctx.state.variables.get(stmt.name)
        ctx.state.variables[stmt.name] = new_val
        if old != new_val:
            ctx.changes.append(f"SET {stmt.name} = {new_val!r}")
        return

    if isinstance(stmt, PromptStmt):
        text = _resolve_template(
            stmt.template, ctx.state.variables, ctx.local_vars,
            ctx.parsed.system_messages,
        )
        if text.strip():
            ctx.prompts.append(text)
        return

    if isinstance(stmt, RunActionStmt):
        await _exec_run_action(stmt, ctx)
        return

    if isinstance(stmt, TransitionStmt):
        ctx.transitions.append(stmt.topic_name)
        return

    if isinstance(stmt, StopStmt):
        ctx.stopped = True
        return


def _parse_tool_result(raw: str) -> dict | str:
    """Try to JSON-decode a tool result. Returns dict on success, raw str on failure."""
    if not raw:
        return ""
    s = raw.strip()
    if s.startswith("{") or s.startswith("["):
        try:
            parsed = json.loads(s)
            return parsed if isinstance(parsed, dict) else {"_result": parsed}
        except json.JSONDecodeError:
            pass
    return s


def _apply_output_mappings(
    info: dict, raw_output: dict | str, ctx: _ExecCtx
) -> dict[str, Any]:
    """Map action outputs into state variables per `set @variables.x = @outputs.y`.

    Returns the resolved output dict (for the prompt builder).
    """
    out_map: dict[str, str] = info.get("output_mappings") or {}
    resolved: dict[str, Any] = {}

    if isinstance(raw_output, dict):
        resolved = dict(raw_output)
        for var_name, out_field in out_map.items():
            if out_field in raw_output:
                val = raw_output[out_field]
                old = ctx.state.variables.get(var_name)
                ctx.state.variables[var_name] = val
                if old != val:
                    ctx.changes.append(
                        f"SET {var_name} = {val!r} (← @outputs.{out_field})"
                    )
    else:
        # Plain string result — store under first declared output mapping
        resolved = {"_raw": raw_output}
        if out_map:
            first_var = next(iter(out_map))
            old = ctx.state.variables.get(first_var)
            ctx.state.variables[first_var] = raw_output
            if old != raw_output:
                ctx.changes.append(f"SET {first_var} = {raw_output!r} (← @outputs)")
    return resolved


async def _exec_skill_via_gateway(
    skill_name: str,
    params: dict[str, Any],
    agent_id: uuid.UUID,
    session_id: str,
    timeout_s: float = 30.0,
    poll_interval_s: float = 0.5,
) -> Any:
    """Enqueue a broadcast `skill_exec` job and wait for an openclaw worker
    to report a result.

    Returns the worker-reported result (string or dict) on success, or
    `{"__error__": "...", "__timeout__": bool}` on failure.
    """
    import asyncio as _asyncio

    from app.database import async_session
    from app.models.gateway_message import GatewayMessage

    payload = json.dumps(
        {"skill": skill_name, "params": params, "requesting_agent_id": str(agent_id)},
        ensure_ascii=False,
    )

    async with async_session() as db:
        msg = GatewayMessage(
            kind="skill_exec",
            agent_id=None,
            sender_agent_id=agent_id,
            conversation_id=session_id or None,
            content=payload,
            status="pending",
        )
        db.add(msg)
        await db.commit()
        await db.refresh(msg)
        msg_id = msg.id

    logger.info(
        f"[ScriptRuntime] Enqueued skill_exec job id={msg_id} "
        f"skill={skill_name} agent={agent_id}"
    )

    deadline = _asyncio.get_event_loop().time() + timeout_s
    from sqlalchemy import select as _select

    while _asyncio.get_event_loop().time() < deadline:
        await _asyncio.sleep(poll_interval_s)
        async with async_session() as db:
            r = await db.execute(
                _select(GatewayMessage).where(GatewayMessage.id == msg_id)
            )
            row = r.scalar_one_or_none()
            if row is None:
                return {"__error__": "skill job vanished from queue"}
            if row.status == "completed":
                raw = row.result or ""
                # Worker reports JSON envelope: {"ok": true, "outputs": {...}}
                # or {"ok": false, "error": "..."}.
                try:
                    env = json.loads(raw) if raw else {}
                except Exception:
                    return raw  # treat as plain string result
                if isinstance(env, dict):
                    if env.get("ok") is False:
                        return {
                            "__error__": str(
                                env.get("error") or "skill execution failed"
                            )
                        }
                    if "outputs" in env:
                        return env["outputs"]
                return raw

    # Timeout — leave row pending so a late worker can still complete it,
    # but return an error to the runtime so the action surfaces as failed.
    return {
        "__error__": (
            f"skill://{skill_name} timed out after {timeout_s:.0f}s — "
            f"no openclaw worker available to execute it"
        ),
        "__timeout__": True,
    }


async def _exec_run_action(stmt: RunActionStmt, ctx: _ExecCtx) -> None:
    """Execute a `run @actions.X` statement.

    For `tool://` targets → invoke directly via execute_tool, parse result,
        apply output_mappings to state, mark `executed=True`.
    For `skill://` targets → enqueue a broadcast skill_exec job on the
        gateway message bus and synchronously wait for an openclaw worker
        to claim, run, and report the result. Output is parsed and bound
        to `@outputs` exactly like a tool call. Marks `executed=True` on
        success or `executed=False` with an error on timeout/failure.
    For other targets (flow://, apex://, plain) or missing exec context →
        collect as pending action for the LLM to invoke via tool calling.
    """
    decl = ctx.topic.actions.get(stmt.action_name)
    info: dict[str, Any] = {"name": stmt.action_name, "params": {}, "executed": False}
    if decl and decl.target:
        info["target"] = decl.target
    if decl and decl.description:
        info["description"] = decl.description
    for p_name, p_expr in stmt.params.items():
        info["params"][p_name] = _evaluate(
            p_expr, ctx.state.variables, ctx.local_vars
        )
    if stmt.output_mappings:
        info["output_mappings"] = dict(stmt.output_mappings)

    target = (decl.target if decl else "") or ""

    # ── Direct execution paths ──
    if ctx.agent_id is not None and target.startswith("tool://"):
        tool_name = target[len("tool://"):].strip()
        try:
            from app.services.agent_tools import execute_tool, is_tool_enabled_for_agent
            # Enforce per-agent tool allowlist — direct (non-LLM) execution
            # must not bypass what `get_agent_tools_for_llm` would expose.
            if not await is_tool_enabled_for_agent(ctx.agent_id, tool_name):
                msg = (
                    f"tool '{tool_name}' is not enabled for this agent; "
                    f"enable it in agent settings or remove `tool://{tool_name}` "
                    f"from action `{stmt.action_name}`."
                )
                logger.warning(
                    f"[ScriptRuntime][MissingTool] agent={ctx.agent_id} "
                    f"action={stmt.action_name!r} tool={tool_name!r} "
                    f"blocked: not in allowlist or unknown"
                )
                info["error"] = msg
                info["executed"] = False
                info["missing_tool"] = True
                info["tool_name"] = tool_name
                ctx.actions.append(info)
                ctx.last_action = info
                for chained in stmt.chained:
                    await _exec_run_action(chained, ctx)
                return
            raw = await execute_tool(
                tool_name=tool_name,
                arguments=info["params"],
                agent_id=ctx.agent_id,
                user_id=ctx.user_id or ctx.agent_id,
                session_id=ctx.session_id or "",
            )
            parsed_out = _parse_tool_result(raw if isinstance(raw, str) else str(raw))
            info["outputs"] = _apply_output_mappings(info, parsed_out, ctx)
            info["executed"] = True
            info["raw_output"] = raw if isinstance(raw, str) else str(raw)
            logger.info(
                f"[ScriptRuntime] Direct-executed tool://{tool_name} for "
                f"agent={ctx.agent_id} session={ctx.session_id or '-'}"
            )
        except Exception as e:
            logger.exception(
                f"[ScriptRuntime] tool://{tool_name} execution failed: {e}"
            )
            info["error"] = str(e)
            info["executed"] = False
    elif ctx.agent_id is not None and target.startswith("skill://"):
        skill_name = target[len("skill://"):].strip()
        try:
            from pathlib import Path

            from app.config import get_settings
            ws_root = Path(get_settings().AGENT_DATA_DIR) / str(ctx.agent_id)
            skill_path = ws_root / "skills" / skill_name / "SKILL.md"
            if not skill_path.exists():
                info["error"] = f"skill not found: skills/{skill_name}/SKILL.md"
                info["missing_skill"] = True
                info["skill_name"] = skill_name
                info["executed"] = False
                logger.warning(
                    f"[ScriptRuntime] skill://{skill_name} not found at {skill_path}"
                )
            else:
                # Enqueue broadcast skill_exec job on the gateway bus and wait
                # synchronously for an openclaw worker to report back.
                raw = await _exec_skill_via_gateway(
                    skill_name=skill_name,
                    params=info["params"],
                    agent_id=ctx.agent_id,
                    session_id=ctx.session_id or "",
                )
                if isinstance(raw, dict) and raw.get("__error__"):
                    info["error"] = raw["__error__"]
                    info["executed"] = False
                    if raw.get("__timeout__"):
                        info["skill_timeout"] = True
                        info["skill_name"] = skill_name
                    else:
                        info["skill_failed"] = True
                        info["skill_name"] = skill_name
                else:
                    # Worker may have returned a structured dict or a string.
                    # Pass dicts through directly so per-field output_mappings
                    # work; only stringified results need the JSON-or-text
                    # parser fallback.
                    if isinstance(raw, dict):
                        parsed_out = raw
                        raw_str = json.dumps(raw, ensure_ascii=False, default=str)
                    else:
                        raw_str = raw if isinstance(raw, str) else str(raw)
                        parsed_out = _parse_tool_result(raw_str)
                    info["outputs"] = _apply_output_mappings(info, parsed_out, ctx)
                    info["executed"] = True
                    info["raw_output"] = raw_str
                    logger.info(
                        f"[ScriptRuntime] Direct-executed skill://{skill_name} "
                        f"for agent={ctx.agent_id} session={ctx.session_id or '-'}"
                    )
        except Exception as e:
            logger.exception(f"[ScriptRuntime] skill://{skill_name} failed: {e}")
            info["error"] = str(e)
            info["executed"] = False

    ctx.actions.append(info)
    ctx.last_action = info
    for chained in stmt.chained:
        await _exec_run_action(chained, ctx)


def _get_active_topic(parsed: ParsedScript, state: ScriptState) -> ScriptTopic | None:
    if state.current_topic == "__start__" and parsed.start_agent:
        return parsed.start_agent
    if state.current_topic in parsed.topics:
        return parsed.topics[state.current_topic]
    if parsed.start_agent:
        state.current_topic = "__start__"
        return parsed.start_agent
    if parsed.topics:
        first = next(iter(parsed.topics))
        state.current_topic = first
        return parsed.topics[first]
    return None


async def execute_script_logic(
    parsed: ParsedScript,
    state: ScriptState,
    max_steps: int = 10,
    agent_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    session_id: str = "",
) -> ScriptExecutionResult:
    """Walk the script's reasoning AST. Procedural ops run here; LLM-bound
    prompts and action calls are collected for the prompt builder.

    When `agent_id` is supplied, actions whose target is `tool://...` or
    `skill://...` are invoked directly; otherwise they're collected for the
    LLM to invoke via standard tool-calling.
    """
    result = ScriptExecutionResult(final_topic=state.current_topic)
    result.topic_path.append(state.current_topic)

    for _ in range(max_steps):
        topic = _get_active_topic(parsed, state)
        if not topic:
            break

        result.steps.append(ExecutionStep(
            topic=topic.name, action="enter",
            detail=f"Evaluating topic '{topic.name}'",
        ))

        if not topic.reasoning or not topic.reasoning.body:
            result.needs_llm = True
            break

        ctx = _ExecCtx(
            parsed=parsed, state=state, topic=topic,
            agent_id=agent_id, user_id=user_id, session_id=session_id,
        )
        await _exec_stmts(topic.reasoning.body, ctx)

        # Roll up the per-statement effects into the result
        for ch in ctx.changes:
            result.changes.append(ch)
            result.steps.append(ExecutionStep(
                topic=topic.name, action="set", detail=ch,
            ))
        for lk, lv in ctx.local_vars.items():
            result.steps.append(ExecutionStep(
                topic=topic.name, action="let",
                detail=f"let {lk} = {json.dumps(lv, ensure_ascii=False, default=str)}",
            ))
        for prompt in ctx.prompts:
            result.llm_instructions.append(prompt)
        if ctx.prompts:
            result.steps.append(ExecutionStep(
                topic=topic.name, action="llm_prompt",
                detail=f"Collected {len(ctx.prompts)} prompt instruction(s)",
            ))
        for act in ctx.actions:
            result.actions_to_run.append(act)
            result.steps.append(ExecutionStep(
                topic=topic.name, action="run_action",
                detail=f"@actions.{act['name']} → {act.get('target', '')}",
            ))

        # Process transitions: take the last one (final assignment wins)
        if ctx.transitions:
            target = ctx.transitions[-1]
            if target in parsed.topics:
                old = state.current_topic
                state.current_topic = target
                result.changes.append(f"TRANSITION {old} → {target}")
                result.topic_path.append(target)
                result.steps.append(ExecutionStep(
                    topic=old, action="transition",
                    detail=f"{old} → {target}",
                ))
                # If the routing topic transitioned us elsewhere, re-execute
                # the new topic in the next iteration.
                if not ctx.stopped:
                    continue

        if ctx.stopped:
            result.steps.append(ExecutionStep(
                topic=topic.name, action="stop", detail="stop",
            ))
            if ctx.actions:
                result.needs_llm = True
            elif ctx.prompts:
                result.response = "\n\n".join(ctx.prompts)
                result.needs_llm = False
            else:
                result.needs_llm = False
        else:
            # Decide whether we still need an LLM round
            if ctx.actions or ctx.prompts:
                result.needs_llm = True
            elif topic.reasoning.mode == "llm":
                result.needs_llm = True
            else:
                # Pure procedural with no prompts/actions → no LLM call needed
                result.needs_llm = False
        break

    result.final_topic = state.current_topic
    result.final_variables = dict(state.variables)
    return result


# ── Backwards-compat: the old `evaluate_reasoning` API ─────────────────


def evaluate_reasoning(reasoning_text: str, variables: dict[str, Any],
                       topic_actions: dict[str, ScriptAction] | None = None,
                       system_messages: dict[str, Any] | None = None) -> ReasoningResult:
    """Legacy entry-point. Re-parses the snippet and runs it stand-alone."""
    rr = ReasoningResult()
    if not reasoning_text or not reasoning_text.strip():
        return rr
    block = reasoning_text.split("\n")
    rb = _parse_reasoning(block)
    rr.is_procedural = (rb.mode == "procedural")

    fake_topic = ScriptTopic(name="__inline__", actions=topic_actions or {})
    fake_state = ScriptState(variables=dict(variables))
    fake_parsed = ParsedScript(system_messages=system_messages or {})
    ctx = _ExecCtx(parsed=fake_parsed, state=fake_state, topic=fake_topic)
    # Legacy path: no agent_id ⇒ no tool/skill execution ⇒ pure CPU work,
    # but the executor is async. Drive it from sync context safely:
    #   • no running loop → asyncio.run
    #   • running loop    → spawn a worker thread with its own loop, join.
    import asyncio as _asyncio

    def _run_in_thread(coro_factory):
        import threading
        result_box: dict[str, Any] = {}
        def _worker():
            try:
                result_box["v"] = _asyncio.run(coro_factory())
            except BaseException as ex:
                result_box["e"] = ex
        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        t.join()
        if "e" in result_box:
            raise result_box["e"]
        return result_box.get("v")

    try:
        _loop = _asyncio.get_running_loop()
        _run_in_thread(lambda: _exec_stmts(rb.body, ctx))
    except RuntimeError:
        # No running loop in this thread.
        _asyncio.run(_exec_stmts(rb.body, ctx))

    # Propagate variable mutations back to the caller's dict
    variables.update(fake_state.variables)
    rr.prompts = ctx.prompts
    rr.transitions = ctx.transitions
    rr.actions_to_run = ctx.actions
    rr.local_vars = ctx.local_vars
    rr.stopped = ctx.stopped
    return rr


# ════════════════════════════════════════════════════════════════════════
# 5.  State persistence  (Replit Object Storage via storage factory)
# ════════════════════════════════════════════════════════════════════════


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
        logger.warning(f"[ScriptRuntime] load_state {agent_id}/{session_id}: {e}")
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


# ════════════════════════════════════════════════════════════════════════
# 6.  Prompt builder
# ════════════════════════════════════════════════════════════════════════


def _render_variables_section(parsed: ParsedScript, state: ScriptState) -> str:
    if not parsed.variables:
        return ""
    lines = ["## Current State (Variables)"]
    for name, var in parsed.variables.items():
        cv = state.variables.get(name, var.default)
        desc = f" — {var.description}" if var.description else ""
        tag = " [mutable]" if var.mutable else " [readonly]"
        lines.append(
            f"- `{name}` ({var.var_type}{tag}): "
            f"`{json.dumps(cv, ensure_ascii=False, default=str)}`{desc}"
        )
    return "\n".join(lines)


def _render_actions_section(topic: ScriptTopic, state: ScriptState) -> str:
    if not topic.actions:
        return ""
    lines = ["## Available Actions"]
    for name, action in topic.actions.items():
        lines.append(f"- **{name}**: {action.description}")
        if action.available_when:
            lines.append(f"  - Condition: available when `{action.available_when}`")
        if action.target:
            target_type = (
                "tool" if "tool://" in action.target
                else "skill" if "skill://" in action.target
                else "flow" if "flow://" in action.target
                else "apex" if "apex://" in action.target
                else "target"
            )
            tname = action.target.split("://")[-1] if "://" in action.target else action.target
            lines.append(f"  - Target: {target_type} `{tname}`")
        if action.inputs:
            parts = [f"`{k}` ({v.get('type', 'string')})" for k, v in action.inputs.items()]
            lines.append(f"  - Inputs: {', '.join(parts)}")
        if action.outputs:
            parts = [f"`{k}` ({v.get('type', 'string')})" for k, v in action.outputs.items()]
            lines.append(f"  - Outputs: {', '.join(parts)}")
    return "\n".join(lines)


def _render_topics_nav(parsed: ParsedScript, current: str) -> str:
    if not parsed.topics:
        return ""
    lines = ["## Available Topics"]
    for name, topic in parsed.topics.items():
        marker = " ← (current)" if name == current else ""
        lines.append(f"- `{name}`: {topic.description}{marker}")
    return "\n".join(lines)


def build_execution_prompt(
    parsed: ParsedScript,
    state: ScriptState,
    exec_result: ScriptExecutionResult,
) -> str:
    parts: list[str] = []

    agent_name = parsed.config.get("agent_name", parsed.config.get("agent_label", "Agent"))
    description = parsed.config.get("description", "")
    parts.append(f"You are **{agent_name}**.")
    if description:
        parts.append(f"**Role**: {description}")
    if parsed.system_instructions:
        parts.append(f"\n## Core Instructions\n{parsed.system_instructions}")

    vs = _render_variables_section(parsed, state)
    if vs:
        parts.append(f"\n{vs}")

    if state.mem:
        parts.append("\n## Conversation Memory")
        for mk, mv in state.mem.items():
            parts.append(f"- **{mk}**: {mv}")

    topic = _get_active_topic(parsed, state)
    if topic:
        parts.append(f"\n## Current Topic: `{topic.name}`")
        if topic.description:
            parts.append(f"*{topic.description}*")

        # Filter actions by `available when`
        filtered: dict[str, ScriptAction] = {}
        for an, ad in topic.actions.items():
            if ad.available_when and not _evaluate_condition(
                ad.available_when, state.variables
            ):
                continue
            filtered[an] = ad
        if filtered:
            shim = ScriptTopic(name=topic.name, actions=filtered)
            asec = _render_actions_section(shim, state)
            if asec:
                parts.append(f"\n{asec}")

    if exec_result.llm_instructions:
        parts.append("\n## Your Task for This Turn")
        parts.append("The script engine has determined the following instructions for you:")
        for inst in exec_result.llm_instructions:
            parts.append(f"- {inst}")

    if exec_result.actions_to_run:
        # Split into already-executed (results in hand) vs pending (LLM must call)
        executed = [a for a in exec_result.actions_to_run if a.get("executed")]
        pending = [a for a in exec_result.actions_to_run if not a.get("executed")]

        if executed:
            parts.append("\n## Actions Already Executed (results below)")
            parts.append(
                "The script engine already ran these actions. Use the results in "
                "your response — do NOT call them again:"
            )
            for act in executed:
                target = act.get("target", "")
                params = act.get("params", {})
                params_desc = (
                    " with " + ", ".join(
                        f"{k}={json.dumps(v, ensure_ascii=False, default=str)}"
                        for k, v in params.items()
                    )
                ) if params else ""
                outs = act.get("outputs", {})
                outs_desc = (
                    "\n    Output: "
                    + json.dumps(outs, ensure_ascii=False, default=str)
                ) if outs else ""
                parts.append(
                    f"- ✓ `{act['name']}` (`{target}`){params_desc}{outs_desc}"
                )

        if pending:
            parts.append("\n## Actions To Execute")
            parts.append("The script requires you to execute these actions NOW:")
            has_skill = has_tool = False
            for act in pending:
                target = act.get("target", "")
                params = act.get("params", {})
                params_desc = (
                    " with parameters: " + ", ".join(
                        f"{k}={json.dumps(v, ensure_ascii=False, default=str)}"
                        for k, v in params.items()
                    )
                ) if params else ""
                out_map = act.get("output_mappings", {})
                out_desc = (
                    " → store outputs: " + ", ".join(
                        f"@variables.{vn} = @outputs.{on}"
                        for vn, on in out_map.items()
                    )
                ) if out_map else ""
                err = act.get("error")
                err_desc = f"  [previous attempt error: {err}]" if err else ""
                if target:
                    parts.append(
                        f"- Call `{act['name']}` (target: `{target}`)"
                        f"{params_desc}{out_desc}{err_desc}"
                    )
                    if target.startswith("skill://"):
                        has_skill = True
                    elif target.startswith("tool://"):
                        has_tool = True
                else:
                    parts.append(
                        f"- Execute `{act['name']}`{params_desc}{out_desc}{err_desc}"
                    )
            if has_skill:
                parts.append(
                    "\n**Note**: `skill://` targets are executed by the "
                    "platform — do not try to read or follow SKILL.md "
                    "yourself. If you see a skill action listed here, "
                    "the system attempted to run it but it failed or "
                    "timed out (see the error in the action above). "
                    "Report the failure honestly; do not substitute "
                    "another tool to fake the result."
                )
            if has_tool:
                parts.append(
                    "\n**Tool Execution**: For `tool://` targets, call the tool "
                    "directly using the standard tool-calling mechanism."
                )

    if exec_result.topic_path and len(exec_result.topic_path) > 1:
        parts.append(
            f"\n## Execution Path\nScript engine routed: "
            f"{' → '.join(exec_result.topic_path)}"
        )

    extractable = [n for n, v in parsed.variables.items() if v.mutable]
    if extractable:
        parts.append("\n## Variable Extraction")
        parts.append(
            "If the user's message reveals a value for any variable, output "
            "`[SET variable = value]` at the END of your response:"
        )
        for name in extractable:
            var = parsed.variables[name]
            cv = state.variables.get(name, var.default)
            desc = f" — {var.description}" if var.description else ""
            parts.append(
                f"- `{name}` ({var.var_type}): current="
                f"`{json.dumps(cv, ensure_ascii=False, default=str)}`{desc}"
            )

    if parsed.topics:
        nav = _render_topics_nav(parsed, state.current_topic)
        if nav:
            parts.append(f"\n{nav}")
        parts.append(
            "\nTo switch topics, output `[TRANSITION topic_name]` at the END of your response."
        )

    parts.append("\n## Memory Updates")
    parts.append(
        "To remember important facts from this conversation, output "
        "`[MEM key = value]` at the END of your response."
    )

    parts.append("\n## Response Rules")
    parts.append(
        "1. **ALWAYS include a natural language response** to the user — "
        "never respond with ONLY directives."
    )
    parts.append(
        "2. Place all `[SET]`, `[TRANSITION]`, `[MEM]` directives at the END."
    )
    parts.append("3. Stay in character as defined by the script.")
    parts.append(
        "4. **Action Execution**: When an action maps to a tool (`tool://`), "
        "call it via standard tool-calling. `skill://` targets are executed "
        "by the platform automatically — never read or follow SKILL.md "
        "yourself, and never substitute another tool when a skill fails."
    )
    parts.append(
        "5. **Missing Capability**: If you cannot fulfill the user's request "
        "because a required tool, API, integration, or piece of data is "
        "unavailable (e.g. no weather API is wired up, no API key configured, "
        "skill references a tool that does not exist), do NOT apologize "
        "generically. Instead, state explicitly and concisely: (a) what the "
        "user asked for, (b) which capability is missing, and (c) what would "
        "need to be configured (e.g. 'a weather API tool needs to be enabled "
        "for this agent'). Be factual, never invent data."
    )

    welcome = parsed.system_messages.get("welcome", "")
    error_msg = parsed.system_messages.get("error", "")
    if welcome or error_msg:
        parts.append("\n## System Messages")
        if welcome:
            parts.append(f"- Welcome: \"{welcome}\"")
        if error_msg:
            parts.append(f"- Error fallback: \"{error_msg}\"")

    return "\n".join(parts)


# ════════════════════════════════════════════════════════════════════════
# 7.  Response post-processor — extracts [SET] / [TRANSITION] / [MEM]
# ════════════════════════════════════════════════════════════════════════


_DIRECTIVE_SET_RE = re.compile(r'\[SET\s+(\w+)\s*=\s*(.+?)\]')
_DIRECTIVE_TRANS_RE = re.compile(r'\[TRANSITION\s+(\w+)\]')
_DIRECTIVE_MEM_RE = re.compile(r'\[MEM\s+(\w+)\s*=\s*(.+?)\]')


def process_response_v2(
    response_text: str,
    state: ScriptState,
    parsed: ParsedScript,
) -> tuple[str, list[str]]:
    """Strip and apply LLM-emitted directives. Returns (clean_text, changes)."""
    changes: list[str] = []
    clean = response_text

    for m in _DIRECTIVE_SET_RE.finditer(response_text):
        var_name = m.group(1)
        raw_val = _unquote(m.group(2).strip())
        if var_name in parsed.variables:
            var = parsed.variables[var_name]
            if var.var_type in ("boolean", "bool"):
                state.variables[var_name] = raw_val.strip().lower() in ("true", "1", "yes")
            elif var.var_type in ("number", "int", "float"):
                try:
                    state.variables[var_name] = (
                        float(raw_val) if "." in raw_val else int(raw_val)
                    )
                except ValueError:
                    pass
            else:
                state.variables[var_name] = raw_val
            changes.append(f"SET {var_name} = {raw_val}")
        clean = clean.replace(m.group(0), "").strip()

    for m in _DIRECTIVE_TRANS_RE.finditer(response_text):
        topic_name = m.group(1)
        if topic_name in parsed.topics:
            state.current_topic = topic_name
            changes.append(f"TRANSITION → {topic_name}")
        elif topic_name in ("topic_selector", "__start__", "start_agent"):
            state.current_topic = "__start__"
            changes.append("TRANSITION → __start__")
        clean = clean.replace(m.group(0), "").strip()

    for m in _DIRECTIVE_MEM_RE.finditer(response_text):
        mk = m.group(1)
        mv = _unquote(m.group(2).strip())
        state.mem[mk] = mv
        changes.append(f"MEM {mk} = {mv}")
        clean = clean.replace(m.group(0), "").strip()

    return clean, changes


# ════════════════════════════════════════════════════════════════════════
# 8.  DB lookup helpers
# ════════════════════════════════════════════════════════════════════════


async def get_script_for_agent(agent_id: str | uuid.UUID) -> str | None:
    from sqlalchemy import desc, select

    from app.database import async_session
    from app.models.evolver import AgentScriptVersion

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


async def get_evolver_welcome(agent_id: uuid.UUID) -> str | None:
    script_text = await get_script_for_agent(agent_id)
    if not script_text:
        return None
    parsed = parse_script(script_text)
    welcome = parsed.system_messages.get("welcome", "")
    return welcome or None
