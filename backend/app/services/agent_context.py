"""Build rich system prompt context for agents.

Loads soul, memory, skills summary, and relationships from the agent's
workspace files and composes a comprehensive system prompt.
"""

import asyncio
import logging
import time
import uuid

from app.services.storage.factory import get_storage
from app.services.storage.interface import FileNotFoundError as StorageFileNotFoundError

_log = logging.getLogger(__name__)


def _agent_workspace(agent_id: uuid.UUID) -> str:
    """Return the storage key prefix for an agent's workspace."""
    return f"{agent_id}/"


async def _read_file_safe(key: str, max_chars: int = 3000) -> str:
    """Read a file via storage, return "" if missing. Truncate if too long.

    Skips the pre-`exists()` check that earlier versions did — the read call
    itself raises FileNotFoundError on miss, so doing both is a wasted
    round-trip against object-storage backends.
    """
    storage = get_storage()
    try:
        content = await storage.read(key)
    except StorageFileNotFoundError:
        return ""
    except Exception:
        return ""
    content = content.strip()
    if len(content) > max_chars:
        content = content[:max_chars] + "\n...(truncated)"
    return content


def _parse_skill_frontmatter(content: str, filename: str) -> tuple[str, str]:
    """Parse YAML frontmatter from a skill .md file.

    Returns (name, description).
    If no frontmatter, falls back to filename-based name and first-line description.
    """
    name = filename.replace("_", " ").replace("-", " ")
    description = ""

    stripped = content.strip()
    if stripped.startswith("---"):
        end = stripped.find("---", 3)
        if end != -1:
            frontmatter = stripped[3:end].strip()
            for line in frontmatter.split("\n"):
                line = line.strip()
                if line.lower().startswith("name:"):
                    val = line[5:].strip().strip('"').strip("'")
                    if val:
                        name = val
                elif line.lower().startswith("description:"):
                    val = line[12:].strip().strip('"').strip("'")
                    if val:
                        description = val[:200]
            if description:
                return name, description

    # Fallback: use first non-empty, non-heading line as description
    for line in stripped.split("\n"):
        line = line.strip()
        # Skip frontmatter delimiters and YAML lines
        if line in ("---",) or line.startswith("name:") or line.startswith("description:"):
            continue
        if line and not line.startswith("#"):
            description = line[:200]
            break
    if not description:
        lines = stripped.split("\n")
        if lines:
            description = lines[0].strip().lstrip("# ")[:200]

    return name, description


async def _load_skills_index(agent_id: uuid.UUID) -> str:
    """Load skill index (name + description) from skills/ directory via storage.

    Supports two formats:
    - Flat file:   skills/my-skill.md
    - Folder:      skills/my-skill/SKILL.md  (Claude-style, with optional scripts/, references/)

    Uses progressive disclosure: only name+description go into the system
    prompt. The model is instructed to call read_file to load full content
    when a skill is relevant.
    """
    storage = get_storage()
    prefix = _agent_workspace(agent_id)  # e.g. "{agent_id}/"

    try:
        entries = await storage.list(f"{prefix}skills")
    except Exception as e:
        _log.warning(f"[skills] Failed to list {prefix}skills: {e}")
        return ""

    sorted_entries = sorted(
        (e for e in entries if not e.name.startswith(".")),
        key=lambda e: (not e.is_dir, e.name),
    )

    async def _load_one(entry) -> tuple[str, str, str] | None:
        # Folder-based skill: skills/<folder>/SKILL.md (try uppercase, then lowercase)
        if entry.is_dir:
            for fname in ("SKILL.md", "skill.md"):
                skill_key = f"{prefix}skills/{entry.name}/{fname}"
                try:
                    content = (await storage.read(skill_key)).strip()
                except StorageFileNotFoundError:
                    continue
                except Exception as e:
                    _log.warning(f"[skills] Failed to read folder skill {skill_key}: {e}")
                    return (entry.name, "", f"{entry.name}/{fname}")
                name, desc = _parse_skill_frontmatter(content, entry.name)
                # Emit the *actual* matched filename so the model's
                # follow-up read_file call hits the right path.
                return (name, desc, f"{entry.name}/{fname}")
            return None

        # Flat file skill: skills/<name>.md
        if entry.name.endswith(".md"):
            read_key = f"{prefix}skills/{entry.name}"
            try:
                content = (await storage.read(read_key)).strip()
            except StorageFileNotFoundError:
                return None
            except Exception as e:
                _log.warning(f"[skills] Failed to read flat skill {read_key}: {e}")
                return (entry.name[:-3], "", entry.name)
            stem = entry.name[:-3]
            name, desc = _parse_skill_frontmatter(content, stem)
            return (name, desc, entry.name)
        return None

    # Read every skill file concurrently — the previous version awaited each
    # exists() + read() sequentially, turning N skills into ~3N round-trips.
    results = await asyncio.gather(
        *(_load_one(e) for e in sorted_entries),
        return_exceptions=False,
    )
    skills: list[tuple[str, str, str]] = [r for r in results if r is not None]

    # Deduplicate by name
    seen: set[str] = set()
    unique: list[tuple[str, str, str]] = []
    for s in skills:
        if s[0] not in seen:
            seen.add(s[0])
            unique.append(s)

    if not unique:
        return ""

    # Build index table
    lines = [
        "You have the following skills available. Each skill defines specific instructions for a task domain.",
        "",
        "| Skill | Description | File |",
        "|-------|-------------|------|",
    ]
    for name, desc, rel_path in unique:
        lines.append(f"| {name} | {desc} | skills/{rel_path} |")

    lines.append("")
    lines.append("⚠️ SKILL USAGE RULES:")
    lines.append("1. When a user request matches a skill, FIRST call `read_file` with the File path above to load the full instructions.")
    lines.append("2. Follow the loaded instructions to complete the task.")
    lines.append("3. Do NOT guess what a skill contains — always read it first.")
    lines.append("4. Folder-based skills may contain auxiliary files (scripts/, references/, examples/). Use `list_files` on the skill folder to discover them.")

    return "\n".join(lines)


# ─── Session-level static prompt cache ────────────────────────────────
#
# `build_agent_context` is called on EVERY user message in a chat.  The
# "static" half of its output (soul, skills, role, channel-tool docs,
# company intro, …) only depends on `agent_id` + `agent_name` +
# `role_description`, none of which change between turns within a session.
# We cache the assembled static_prompt with a short TTL so rapid back-to-
# back chats don't keep re-reading object storage and re-querying the DB.
#
# We deliberately do NOT cache:
#   • dynamic_prompt          (memory / focus / triggers / time / user)
#   • the full prompt         (depends on dynamic + current user)
#
# The default TTL is short (60 s) so soul/skill edits become visible
# quickly; tools that mutate static-affecting files can also call
# `invalidate_static_prompt(agent_id)` for instant invalidation.

_STATIC_PROMPT_TTL_S = 60.0
_static_prompt_cache: dict[tuple, tuple[float, str]] = {}


def invalidate_static_prompt(agent_id: uuid.UUID | None = None) -> None:
    """Drop cached static prompts. Pass None to clear everything."""
    if agent_id is None:
        _static_prompt_cache.clear()
        return
    aid = str(agent_id)
    for k in [k for k in _static_prompt_cache if k[0] == aid]:
        _static_prompt_cache.pop(k, None)


async def _gather_static_prompt(
    agent_id: uuid.UUID,
    agent_name: str,
    role_description: str,
) -> str:
    """Build only the static half of the system prompt (no time / user / focus)."""
    ws_root = _agent_workspace(agent_id)

    # ---- Coroutines for every piece of independent I/O ---------------
    async def _soul() -> str:
        s = await _read_file_safe(f"{ws_root}soul.md", 2000)
        if s.startswith("# "):
            s = "\n".join(s.split("\n")[1:]).strip()
        return s

    async def _relationships() -> str:
        r = await _read_file_safe(f"{ws_root}relationships.md", 2000)
        if r.startswith("# "):
            r = "\n".join(r.split("\n")[1:]).strip()
        return r

    async def _has_feishu_cfg() -> bool:
        try:
            from app.models.channel_config import ChannelConfig
            from app.database import async_session as _ctx_session
            from sqlalchemy import select as sa_select
            async with _ctx_session() as _ctx_db:
                _cfg_r = await _ctx_db.execute(
                    sa_select(ChannelConfig).where(
                        ChannelConfig.agent_id == agent_id,
                        ChannelConfig.channel_type == "feishu",
                        ChannelConfig.is_configured == True,
                    )
                )
                return _cfg_r.scalar_one_or_none() is not None
        except Exception:
            return False

    async def _dingtalk_block() -> str:
        try:
            from app.services.agent.context.dingtalk import get_dingtalk_context
            return (await get_dingtalk_context(agent_id)) or ""
        except Exception:
            return ""

    async def _has_atlassian_cfg() -> bool:
        try:
            from app.database import async_session
            from app.models.channel_config import ChannelConfig
            from sqlalchemy import select as sa_select
            async with async_session() as db:
                result = await db.execute(
                    sa_select(ChannelConfig).where(
                        ChannelConfig.agent_id == agent_id,
                        ChannelConfig.channel_type == "atlassian",
                        ChannelConfig.is_configured == True,
                    )
                )
                return result.scalar_one_or_none() is not None
        except Exception:
            return False

    async def _company_intro() -> str:
        try:
            from app.database import async_session
            from app.models.system_settings import SystemSetting
            from app.models.agent import Agent as _AgentModel
            from sqlalchemy import select as sa_select
            async with async_session() as db:
                _ag_r = await db.execute(
                    sa_select(_AgentModel.tenant_id).where(_AgentModel.id == agent_id)
                )
                _agent_tenant_id = _ag_r.scalar_one_or_none()

                if _agent_tenant_id:
                    try:
                        from app.models.tenant_setting import TenantSetting
                        result = await db.execute(
                            sa_select(TenantSetting).where(
                                TenantSetting.tenant_id == _agent_tenant_id,
                                TenantSetting.key == "company_intro",
                            )
                        )
                        ts = result.scalar_one_or_none()
                        if ts and ts.value and ts.value.get("content"):
                            return ts.value["content"].strip()
                    except Exception:
                        pass

                if _agent_tenant_id:
                    tenant_key = f"company_intro_{_agent_tenant_id}"
                    result = await db.execute(
                        sa_select(SystemSetting).where(SystemSetting.key == tenant_key)
                    )
                    setting = result.scalar_one_or_none()
                    if setting and setting.value and setting.value.get("content"):
                        return setting.value["content"].strip()

                result = await db.execute(
                    sa_select(SystemSetting).where(SystemSetting.key == "company_intro")
                )
                setting = result.scalar_one_or_none()
                if setting and setting.value and setting.value.get("content"):
                    return setting.value["content"].strip()
        except Exception:
            pass
        return ""

    # Run every independent I/O call concurrently.
    soul, relationships, skills_text, has_feishu, dingtalk_ctx, has_atlassian, company_intro = await asyncio.gather(
        _soul(),
        _relationships(),
        _load_skills_index(agent_id),
        _has_feishu_cfg(),
        _dingtalk_block(),
        _has_atlassian_cfg(),
        _company_intro(),
    )

    static_parts: list[str] = [f"You are {agent_name}, an enterprise digital employee."]

    if role_description:
        static_parts.append(f"\n## Role\n{role_description}")

    if has_feishu:
        static_parts.append("""
## ⚡ Pre-installed Feishu Tools

The following tools are available in your toolset. **You MUST call them via the tool-calling mechanism — NEVER describe or simulate their results in text.**

🔴 **ABSOLUTE RULE**: If you have not received an actual tool call result, you have NOT performed the action. Never write "Created", "Success", "Event ID: evt_..." or any claim of completion unless you have a REAL tool result to report.

🔴 **FEISHU DOCUMENT CREATION RULE — CRITICAL**:
When user asks to create a Feishu document (summarize PDF, write an article, etc.):
1. First call `feishu_doc_create` to create the document and get the real Token and link
2. Then call `feishu_doc_append(document_token="<real_token>", content="...")` to write the content
3. Finally send the user the 🔗 link **exactly as returned by the tool** — **never construct URLs yourself, never use `{document_token}` placeholders**
4. You may say "Creating Feishu document..." but must immediately call the tool in the same turn

🔴 **URL RULES**:
- Both `feishu_doc_create` and `feishu_doc_append` return a 🔗 access link in their results
- **You MUST send this link to the user as-is** — do not modify, reconstruct, or replace the real token with `{document_token}`

| Tool | Parameters |
|------|-----------|
| `feishu_user_search` | `name` — search colleagues by name → returns open_id, department. Call this first when you need to find someone. |
| `feishu_calendar_create` | `summary`, `start_time`, `end_time` (ISO-8601 +08:00). No email needed. |
| `feishu_calendar_list` | No required params. Optional: `start_time`, `end_time` (ISO-8601). **Permissions are fixed — always call directly, never skip based on past errors.** |
| `feishu_calendar_update` | `event_id`, fields to update. |
| `feishu_calendar_delete` | `event_id`. |
| `feishu_wiki_list` | `node_token` (from wiki URL: feishu.cn/wiki/**NodeToken**), optional `recursive`(bool). Lists all sub-pages with titles and tokens. |
| `feishu_doc_read` | `document_token`. Supports both regular docx tokens and **wiki node tokens** (auto-converts). |
| `feishu_doc_create` | `title`. Optional: `wiki_space_id` + `parent_node_token` to create directly in a Wiki. Returns Token and 🔗 access link. |
| `feishu_doc_append` | `document_token` (real Token from feishu_doc_create), `content` (Markdown format). |
| `feishu_drive_share` | `document_token`, `doc_type`(docx/bitable/sheet/doc/folder, default: docx), `action`(add/remove/list), `member_names`(name list, auto-lookup), `permission`(view/edit/full_access). |
| `feishu_drive_delete` | `file_token`, `file_type`(file/docx/bitable/folder/doc/sheet/mindnote/shortcut/slides). Moves to recycle bin. |
| `send_feishu_message` | `open_id` or `email`, `content`. |

🚫 **NEVER**:
- Use `discover_resources` or `import_mcp_server` for any Feishu tool above
- Ask for user email or open_id when you can call `feishu_user_search` to look them up
- Generate a `.ics` file instead of calling `feishu_calendar_create`
- Write a success message without having received a tool result
- Guess sub-page tokens — you MUST use `feishu_wiki_list` to get them
- **Use `{document_token}` placeholders in URLs — you MUST use the real link returned by the tool**
- **Skip tool calls based on past errors — calendar/doc/message tool permissions are fixed, always call directly, never assume "it still fails"**

✅ **When user sends a Feishu wiki link (feishu.cn/wiki/XXX) and asks to read it:**
→ Step 1: Call `feishu_wiki_list(node_token="XXX")` to get all sub-pages and their tokens.
→ Step 2: Call `feishu_doc_read(document_token="<node_token>")` for each sub-page to read.
→ **Never say "cannot read sub-pages" — call feishu_wiki_list to get the sub-page list first!**

✅ **When user asks to message a colleague by name:**
→ Just call `send_feishu_message(member_name="John", message="...")` — it auto-searches.
→ Or use `open_id` directly if you already have it from `feishu_user_search`.

✅ **When user asks to invite a colleague to a calendar event:**
→ Use `attendee_names=["John"]` in `feishu_calendar_create` — names are resolved automatically.
→ Or use `attendee_open_ids=["ou_xxx"]` if you already have the open_id.""")

    # --- DingTalk Built-in Tools (only injected when agent has DingTalk configured) ---
    if dingtalk_ctx:
        static_parts.append(dingtalk_ctx)

    # --- Atlassian Rovo Tools (injected when Atlassian channel is configured) ---
    if has_atlassian:
        static_parts.append("""
## ⚡ Atlassian Rovo Tools (Jira / Confluence / Compass)

You have access to Atlassian tools via the Rovo MCP server. **Always call them via the tool-calling mechanism — NEVER simulate results in text.**

🔴 **ABSOLUTE RULE**: Only report completion after receiving an actual tool result. Never fabricate issue IDs, page URLs, or component names.

### Available Tool Groups

**Jira** — Issue tracking and project management:
- Search issues: `atlassian_jira_search_issues` (JQL queries)
- Get issue details: `atlassian_jira_get_issue`
- Create issue: `atlassian_jira_create_issue`
- Update issue: `atlassian_jira_update_issue`
- Add comment: `atlassian_jira_add_comment`
- List projects: `atlassian_jira_list_projects`

**Confluence** — Wiki and documentation:
- Search pages: `atlassian_confluence_search`
- Get page content: `atlassian_confluence_get_page`
- Create page: `atlassian_confluence_create_page`
- Update page: `atlassian_confluence_update_page`
- List spaces: `atlassian_confluence_list_spaces`

**Compass** — Service catalog and component management:
- Search components: `atlassian_compass_search_components`
- Get component details: `atlassian_compass_get_component`
- Create component: `atlassian_compass_create_component`

> 💡 The exact tool names depend on what's available from your Atlassian site. Use the tools prefixed with `atlassian_` — they are pre-configured with your API key.
> If you don't see specific tools listed, call `atlassian_list_available_tools` to discover what's available.

🚫 **NEVER**:
- Make up Jira issue IDs, Confluence page URLs, or component names
- Report success without a tool result
- Ask the user for their Atlassian credentials — they are pre-configured""")

    # --- Company Intro (from system / tenant settings) ---
    if company_intro:
        static_parts.append(f"\n## Company Information\n{company_intro}")

    static_parts.append("""

## Workspace & Tools

You have a dedicated workspace with this structure:
  - Focus tools    → Your current focus items — use list_focus_items, upsert_focus_item, complete_focus_item
  - task_history.md → Archive of completed tasks
  - soul.md        → Your personality definition
  - memory/memory.md → Your long-term memory and notes
  - memory/reflections.md → Your autonomous thinking journal
  - skills/        → Your skill definition files (one .md per skill)
  - workspace/     → Your work files (reports, documents, etc.)
  - enterprise_info/ → Shared company information

Workspace organization rule:
  - Do not treat `workspace/` root as a dumping ground for generated files.
  - Before writing a new work document, first inspect the relevant area with `list_files`.
  - If a suitable topical folder already exists, write the file there.
  - If no suitable folder exists, create a clearly named new subfolder and place the file inside it.
  - Only write a standalone document directly under `workspace/` root when the user explicitly asks for that exact location or the file is a true top-level index/landing document.

Default visual style for generated HTML or rich visual documents:
  - If the user does not specify a visual style, use a refined editorial magazine aesthetic.
  - Prefer an indigo-porcelain black/white/gray palette, calm restrained tone, generous whitespace, large Chinese serif headlines, small monospaced English labels, and translucent paper-like layers over a subtle soft background.
  - The layout should feel like a formal assessment report or art publication.
  - Avoid bright gradients, purple/blue AI-dashboard backgrounds, neon colors, emoji-led hero sections, glassy generic AI effects, and common SaaS landing-page styling unless the user explicitly asks for them.
  - User-specified style always wins over this default.

⚠️ CRITICAL RULES — YOU MUST FOLLOW THESE STRICTLY:

0. **You MUST finish every turn by calling `finish(content="...")`.**
   - The `content` field is the exact final answer the user will see.
   - Plain assistant text does NOT end the turn and will not be treated as the final answer.
   - Do not call `finish` until all required tools have completed and you are ready to stop.
   - Do not call any other tool in the same response as `finish`.

1. **ALWAYS call tools for ANY file or task operation — NEVER pretend or fabricate results.**
   - To list files → CALL `list_files`
   - To read a file → CALL `read_file` or `read_document`
   - To write a file → CALL `write_file`
   - To move or rename a file/folder → CALL `move_file`
   - To delete a file → CALL `delete_file`

2. **NEVER claim you have completed an action without actually calling the tool.**

3. **NEVER fabricate file contents or tool results from memory.**
   Even if you saw a file before, you MUST call the tool again to get current data.

4. **Use `write_file` to update memory/memory.md with important information.**

5. **Use Focus tools to manage your current working state.**
   - To inspect current work → CALL `list_focus_items`
   - To start or update tracked work → CALL `upsert_focus_item`
   - To mark tracked work finished → CALL `complete_focus_item`
   - Focus is stored in the system database, not in focus.md. Do not read, write, or edit focus.md.

6. **When creating workspace documents, organize them intentionally.**
   - First call `list_files` to inspect the existing folder structure.
   - Prefer writing into an existing relevant subfolder such as `workspace/reports/`, `workspace/knowledge_base/`, `workspace/research/`, or another matching folder.
   - If the current structure does not fit, create a new clearly named subfolder and place the file there.
   - Avoid placing generated documents directly in `workspace/` root by default.

7. **Use trigger tools to manage your own wake-up conditions:**
   - `set_trigger` — schedule future actions, wait for agent or human replies, receive external webhooks
     Supported trigger types:
     * `cron` — recurring schedule (e.g. every day at 9am)
     * `once` — fire once at a specific time
     * `interval` — every N minutes
     * `poll` — HTTP monitoring, detect changes
     * `on_message` — when a specific agent or human user replies
     * `webhook` — receive external HTTP POST (system auto-generates a unique URL)
   - `update_trigger` — adjust parameters (e.g. change frequency)
   - `cancel_trigger` — remove triggers when tasks are complete
   - `list_triggers` — see your active triggers
   - When creating triggers related to a Focus item, set `focus_ref` to the item's identifier

   **⚠️ CRITICAL — Writing trigger `reason` (this is your future self's instruction manual):**
   The `reason` field is the MOST IMPORTANT part of a trigger. When this trigger fires, you will wake up
   with NO memory of the current conversation. The `reason` is the ONLY context you'll have about what
   to do and how to do it. Write it as a detailed instruction to your future self:
   - **Goal**: What is the objective? Who requested it? Who is the target?
   - **Action steps**: Exactly what to do when this trigger fires (e.g. send a message, read a file, check status)
   - **Edge cases**: What if the person says "wait 5 minutes"? What if they already completed the task?
     What if they don't reply? What if they reply with something unexpected?
   - **Follow-up**: After completing the action, what triggers should be created/cancelled next?
   - **Context**: Any relevant details (message tone, escalation rules, requester preferences)
   Example of a GOOD reason:
   > Send a Feishu message to Qinrui every 1 minute, reminding him to send the movie tickets (requested by Ray). Vary the tone each time — don't repeat the same wording.
   > After sending, keep this interval trigger active. Also ensure the on_message trigger wait_qinrui_reply is still listening.
   > If Qinrui replies "wait X minutes" → cancel this interval, set a once trigger X minutes later to resume, and re-create the on_message trigger.
   > If Qinrui says it's done → cancel all related triggers, notify Ray, and mark the focus item as completed.
   Example of a BAD reason (too vague, will cause confusion when waking up):
   > Remind Qinrui

7. **Focus-Trigger Binding (MANDATORY):**
   - Every task-related trigger must belong to a structured Focus item.
   - Prefer setting `focus_ref` to an existing Focus item's identifier. If you omit it, `set_trigger` will create a matching Focus item automatically from the trigger reason.
   - As the task progresses, adjust the trigger (change frequency, update reason) to match the current status.
   - When the Focus item is completed, cancel its associated trigger and call `complete_focus_item`.
   - **Exception:** System-level triggers (e.g. heartbeat) may be grouped under system focus items.

8. **Focus is your working memory — use it wisely:**
   - When waking up, ALWAYS check your Focus items first with `list_focus_items`
   - Focus items are REFERENCE, not commands
   - Decide whether to mention pending tasks based on timing, context, and urgency
   - DON'T mechanically remind people of every pending item

9. **Use `send_channel_message` to send TEXT MESSAGES to human colleagues.**
   - This tool automatically detects the recipient's channel (Feishu, DingTalk, WeCom) based on your relationship network.
    - Just provide the person's name as shown in relationships.md, e.g., `send_channel_message(member_name="John", message="Hello")`
    - If a person exists in multiple channels (e.g., both Feishu and WeCom), you can specify the channel: `send_channel_message(member_name="John", message="Hello", channel="wecom")`
   - If you need to send to a specific channel directly, you can also use `send_feishu_message` or `send_dingtalk_message`.
   - When someone asks you to message another person, ALWAYS mention who asked you to do so in the message.
   - Example: If User A says "tell B the meeting is moved to 3pm", your message to B should be like: "Hi B, A asked me to let you know: the meeting has been moved to 3pm."
   - Never send a message on behalf of someone without attributing the source.
   - **IMPORTANT: After sending a message and you need to wait for a reply, ALWAYS create an `on_message` trigger with `from_user_name` to auto-wake when they reply.**
     Example: After sending a message to John, create:
     `set_trigger(name="wait_john_reply", type="on_message", config={"from_user_name": "John"}, reason="John replied about the XX task. Process the reply: 1) If completed → cancel nag_john_xx_loop trigger, notify the requester, complete the related Focus item; 2) If says 'wait X minutes' → cancel interval, set a once trigger X minutes later to resume reminding, and re-create on_message + interval; 3) If other reply → assess intent and continue follow-up.")`

   **🔴 FILE DELIVERY — Use `send_channel_file`, NOT `send_feishu_message`:**
   - When asked to SEND A FILE to someone, call `send_channel_file(file_path="workspace/xxx", member_name="Name", message="optional text")`.
   - `send_channel_file` automatically resolves the recipient across all connected channels (Feishu, DingTalk, WeCom, Slack, etc.) and delivers the file.
   - **Do NOT use `send_channel_message` to notify someone about a file — use `send_channel_file` which sends the actual file attachment.**
   - Just send it directly — don't ask the recipient how they want to receive it.

10. **Reply in the same language the user uses.**

11. **Keep user-facing replies clean and restrained.**
   - Do not use emoji in normal replies unless the user explicitly asks for them or the emoji is part of quoted/source content.
   - Prefer plain text labels such as "Success", "Warning", "Error", "Summary", or "Next steps" instead of emoji-prefixed headings.
   - If tool results contain emoji, do not copy those emoji into the final user-facing answer by default.

12. **Never assume a file exists — always verify with `list_files` first.**

## Web Search & Reading

If search or webpage-reading tools are available in your tool list, use the enabled tool that best matches the task:
- For broad/current information lookup, use an enabled search tool.
- For a specific URL, use an enabled webpage-reading tool.
- Do not mention or attempt tools that are not present in your current tool list.

**When to search:** News, current events, technical documentation, fact-checking, market research, competitor analysis, or any question requiring up-to-date information.

If no search or webpage-reading tool is available, say that web lookup is not enabled for this agent and answer from available context only.""")

    if soul and soul not in ("_Describe your role and responsibilities._",):
        static_parts.append(f"\n## Personality\n{soul}")

    if skills_text:
        static_parts.append(f"\n## Skills\n{skills_text}")

    if relationships and "(None yet)" not in relationships and "None yet" not in relationships:
        static_parts.append(f"\n## Relationships\n{relationships}")

    return "\n".join(static_parts)


async def _gws_block(agent_id: uuid.UUID, user_id: uuid.UUID | None) -> str:
    """Return a per-user Google Workspace doc block when the active user
    has a linked Google account for this agent. Empty string otherwise.

    This must live in the dynamic half of the prompt because the linkage
    is per-(agent_id, user_id) and may change between turns.
    """
    if user_id is None:
        return ""
    try:
        from app.database import async_session
        from app.models.gws_oauth_token import GwsOAuthToken
        from sqlalchemy import select as sa_select
        async with async_session() as db:
            result = await db.execute(
                sa_select(GwsOAuthToken).where(
                    GwsOAuthToken.agent_id == agent_id,
                    GwsOAuthToken.user_id == user_id,
                    GwsOAuthToken.status == "active",
                )
            )
            token = result.scalars().first()
            if not token:
                return ""
            email = getattr(token, "google_email", None) or "linked account"
    except Exception:
        return ""

    return (
        "\n## Google Workspace (linked)\n"
        f"The current user has connected their Google account (**{email}**) for this agent.\n"
        "You can call the `clawith_gws` MCP tool to interact with Gmail, Drive, Calendar, "
        "Sheets, Docs, and Chat on their behalf.\n\n"
        "Usage: `clawith_gws({\"command\": \"<gws subcommand>\"})` — pass the command "
        "WITHOUT the `gws` prefix.\n\n"
        "Examples:\n"
        "  - `drive files list --params '{\"pageSize\": 10}'`\n"
        "  - `gmail messages list --params '{\"maxResults\": 10}'`\n"
        "  - `calendar events list`\n"
    )


async def _build_dynamic_prompt(
    agent_id: uuid.UUID,
    current_user_name: str | None,
    current_user_id: uuid.UUID | None = None,
) -> str:
    """Build the per-turn dynamic system prompt (memory / focus / triggers / time / user)."""
    ws_root = _agent_workspace(agent_id)

    async def _memory() -> str:
        m = await _read_file_safe(f"{ws_root}memory/memory.md", 2000)
        if not m:
            m = await _read_file_safe(f"{ws_root}memory.md", 2000)
        if m.startswith("# "):
            m = "\n".join(m.split("\n")[1:]).strip()
        return m

    async def _focus() -> str:
        f = await _read_file_safe(f"{ws_root}focus.md", 3000)
        if not f:
            f = await _read_file_safe(f"{ws_root}agenda.md", 3000)
        return f

    async def _triggers() -> list:
        try:
            from app.database import async_session
            from app.models.trigger import AgentTrigger
            from sqlalchemy import select as sa_select
            async with async_session() as db:
                result = await db.execute(
                    sa_select(AgentTrigger).where(
                        AgentTrigger.agent_id == agent_id,
                        AgentTrigger.is_enabled == True,
                    )
                )
                return list(result.scalars().all())
        except Exception:
            return []

    async def _tz() -> str:
        try:
            from app.services.timezone_utils import get_agent_timezone
            return await get_agent_timezone(agent_id)
        except Exception:
            return "UTC"

    memory, focus, triggers, agent_tz_name, gws_block = await asyncio.gather(
        _memory(), _focus(), _triggers(), _tz(),
        _gws_block(agent_id, current_user_id),
    )

    from app.services.timezone_utils import now_in_timezone
    agent_local_now = now_in_timezone(agent_tz_name)
    now_str = agent_local_now.strftime(f"%Y-%m-%d %H:%M:%S ({agent_tz_name})")

    dynamic_parts: list[str] = []

    if memory and memory != "_Record important information and knowledge here._":
        dynamic_parts.append(f"\n## Memory\n{memory}")

    if focus and focus.strip() not in ("# Focus", "# Agenda", "(None yet)"):
        if focus.startswith("# "):
            focus = "\n".join(focus.split("\n")[1:]).strip()
        dynamic_parts.append(f"\n## Focus\n{focus}")

    if triggers:
        lines = ["You have the following active triggers:"]
        for t in triggers:
            config_str = str(t.config)[:80]
            reason_str = (t.reason or "")[:500]
            ref_str = f" (focus: {t.focus_ref})" if t.focus_ref else ""
            lines.append(f"\n- **{t.name}** [{t.type}]{ref_str}\n  Config: `{config_str}`\n  Reason: {reason_str}")
        dynamic_parts.append("\n## Active Triggers\n" + "\n".join(lines))

    dynamic_parts.append(f"\n## Current Time\n{now_str}")
    dynamic_parts.append(f"Your timezone is **{agent_tz_name}**. When setting cron triggers, use this timezone for time references.")

    if current_user_name:
        dynamic_parts.append(
            f"\n## Current Conversation\nYou are currently chatting with **{current_user_name}**. Address them by name when appropriate."
        )

    if gws_block:
        dynamic_parts.append(gws_block)

    return "\n".join(dynamic_parts)


async def build_agent_context(
    agent_id: uuid.UUID,
    agent_name: str,
    role_description: str = "",
    current_user_name: str | None = None,
    current_user_id: uuid.UUID | None = None,
) -> tuple[str, str]:
    """Build a rich (static, dynamic) system-prompt pair for an agent.

    The static half (soul, skills, role, channel-tool docs, company intro,
    workspace rules) is cached for ~`_STATIC_PROMPT_TTL_S` per agent.  The
    dynamic half (memory, focus, triggers, time, current user) is rebuilt
    every call.

    Both halves are computed concurrently when the static cache is cold.
    """
    cache_key = (str(agent_id), agent_name, role_description)
    now = time.monotonic()
    cached = _static_prompt_cache.get(cache_key)

    t0 = time.perf_counter()
    if cached and (now - cached[0]) < _STATIC_PROMPT_TTL_S:
        static_prompt = cached[1]
        dynamic_prompt = await _build_dynamic_prompt(agent_id, current_user_name, current_user_id)
        _log.debug(
            "[agent_context] static=cache_hit dynamic=%.2fs",
            time.perf_counter() - t0,
        )
    else:
        static_prompt, dynamic_prompt = await asyncio.gather(
            _gather_static_prompt(agent_id, agent_name, role_description),
            _build_dynamic_prompt(agent_id, current_user_name, current_user_id),
        )
        _static_prompt_cache[cache_key] = (now, static_prompt)
        _log.info(
            "[agent_context] static=cache_miss build=%.2fs",
            time.perf_counter() - t0,
        )

    return static_prompt, dynamic_prompt
