"""Seed builtin skills into the global skill registry."""

from loguru import logger
from sqlalchemy import select
from app.database import async_session
from app.models.skill import Skill, SkillFile


BUILTIN_SKILLS = [
    {
        "name": "Web Research",
        "description": "Systematic web searching and information synthesis. Use when: needing factual data from the web, evaluating sources, or cross-referencing claims. NOT for: simple trivia or local file search.",
        "category": "research",
        "icon": "🔍",
        "folder_name": "web-research",
        "files": [
            {
                "path": "SKILL.md",
                "content": """---
name: Web Research
description: Systematic web searching, source evaluation, and information synthesis
---

# Web Research

## Overview
Use this skill when you need to find, evaluate, and synthesize information from the web.

**Keywords**: web search, information retrieval, source evaluation, fact-checking, research

## Process

### 1. Define Search Strategy
- Identify key search terms and variations
- Consider different angles and perspectives
- Plan multiple search queries

### 2. Evaluate Sources
- Check source credibility and recency
- Cross-reference claims across multiple sources
- Note publication dates and author expertise

### 3. Synthesize Findings
- Organize information by theme or relevance
- Highlight key findings and consensus views
- Note conflicting information and gaps

## Output Format
- Start with a brief summary of findings
- Provide detailed sections with source citations
- End with confidence assessment and limitations
""",
            },
            {
                "path": "scripts/search_helper.py",
                "content": (
                    "#!/usr/bin/env python3\n"
                    '"""Helper utilities for structured web search."""\n\n'
                    "from datetime import datetime\n\n\n"
                    "def format_search_results(results: list[dict]) -> str:\n"
                    '    """Format raw search results into a structured report."""\n'
                    "    output = []\n"
                    "    for i, r in enumerate(results, 1):\n"
                    "        title = r.get('title', 'Untitled')\n"
                    "        url = r.get('url', '#')\n"
                    "        snippet = r.get('snippet', 'No description')\n"
                    "        output.append(f'{i}. [{title}]({url})')\n"
                    "        output.append(f'   {snippet}')\n"
                    "        output.append('')\n"
                    "    return '\\n'.join(output)\n\n\n"
                    "def assess_source_credibility(url: str) -> dict:\n"
                    '    """Basic heuristics for source credibility."""\n'
                    "    trusted = ['.edu', '.gov', '.org', 'arxiv.org', 'nature.com']\n"
                    "    score = 0.5\n"
                    "    for d in trusted:\n"
                    "        if d in url:\n"
                    "            score = 0.8\n"
                    "            break\n"
                    "    return {'url': url, 'credibility_score': score,\n"
                    "            'assessed_at': datetime.now().isoformat()}\n"
                ),
            },
        ],
    },
    {
        "name": "Data Analysis",
        "description": "Data interpretation and structured reporting. Use when: analyzing CSV/dataset files, finding trends, or generating statistical summaries. NOT for: writing code to build data models.",
        "category": "analysis",
        "icon": "📊",
        "folder_name": "data-analysis",
        "files": [
            {
                "path": "SKILL.md",
                "content": """---
name: Data Analysis
description: Data interpretation, pattern recognition, and structured reporting
---

# Data Analysis

## Overview
Use this skill for analyzing data, identifying patterns, and creating structured reports.

**Keywords**: data analysis, statistics, trends, visualization, reporting

## Process

### 1. Data Understanding
- Identify data types, ranges, and distributions
- Check for missing values and anomalies
- Understand the business context

### 2. Analysis Methods
- Descriptive statistics (mean, median, distribution)
- Trend analysis (time-series patterns)
- Comparative analysis (benchmarking, A/B)
- Correlation and relationship discovery

### 3. Reporting
- Lead with key insights and actionable findings
- Use tables and structured formats for clarity
- Include methodology notes for reproducibility

## Output Format
- Executive summary with top 3 findings
- Detailed analysis with supporting data
- Recommendations based on findings
""",
            },
            {
                "path": "scripts/analyze_csv.py",
                "content": (
                    "#!/usr/bin/env python3\n"
                    '"""Utility for quick CSV data analysis."""\n\n'
                    "import csv\nimport statistics\nfrom collections import Counter\n\n\n"
                    "def analyze_column(data: list[dict], column: str) -> dict:\n"
                    '    """Analyze a single column from CSV data."""\n'
                    "    values = [row.get(column) for row in data if row.get(column) is not None]\n"
                    "    if not values:\n"
                    '        return {"column": column, "count": 0, "error": "No data"}\n\n'
                    '    result = {"column": column, "count": len(values), "unique": len(set(values))}\n\n'
                    "    # Try numeric analysis\n"
                    "    try:\n"
                    "        nums = [float(v) for v in values]\n"
                    "        result.update({\n"
                    '            "type": "numeric",\n'
                    '            "min": min(nums), "max": max(nums),\n'
                    '            "mean": round(statistics.mean(nums), 2),\n'
                    '            "median": round(statistics.median(nums), 2),\n'
                    "        })\n"
                    "    except (ValueError, TypeError):\n"
                    "        freq = Counter(values).most_common(5)\n"
                    '        result.update({"type": "categorical", "top_values": freq})\n\n'
                    "    return result\n\n\n"
                    "def quick_summary(filepath: str) -> str:\n"
                    '    """Generate a quick summary of a CSV file."""\n'
                    "    with open(filepath, 'r') as f:\n"
                    "        reader = csv.DictReader(f)\n"
                    "        data = list(reader)\n"
                    "    columns = data[0].keys() if data else []\n"
                    "    return f'Rows: {len(data)}, Columns: {len(columns)}'\n"
                ),
            },
            {
                "path": "examples/sample_report.md",
                "content": """# Sample Analysis Report

## Executive Summary
Analysis of Q4 2024 sales data reveals a 12% increase in total revenue,
driven primarily by the Enterprise segment (+23%).

## Key Findings
1. **Revenue Growth**: Total revenue increased from $2.1M to $2.35M
2. **Top Segment**: Enterprise accounts grew 23% QoQ
3. **Churn**: SMB churn rate decreased from 5.2% to 4.1%

## Detailed Analysis

| Metric | Q3 2024 | Q4 2024 | Change |
|--------|---------|---------|--------|
| Total Revenue | $2.1M | $2.35M | +12% |
| Enterprise | $1.2M | $1.47M | +23% |
| SMB | $0.9M | $0.88M | -2% |
| Churn Rate | 5.2% | 4.1% | -1.1pp |

## Recommendations
1. Increase investment in Enterprise sales team
2. Investigate SMB revenue decline
3. Continue churn reduction initiatives
""",
            },
        ],
    },
    {
        "name": "Content Writing",
        "description": "Professional content creation and tone adaptation. Use when: drafting articles, emails, or marketing copy with specific stylistic requirements. NOT for: casual chat responses.",
        "category": "creation",
        "icon": "✍️",
        "folder_name": "content-writing",
        "files": [
            {
                "path": "SKILL.md",
                "content": """---
name: Content Writing
description: Professional content creation, editing, and tone adaptation
---

# Content Writing

## Overview
Use this skill for creating, editing, and polishing written content across formats.

**Keywords**: writing, editing, copywriting, tone, style, proofreading

## Content Types
- **Articles & Blog Posts**: Informative, engaging long-form content
- **Business Communications**: Emails, memos, reports
- **Marketing Copy**: Headlines, descriptions, calls-to-action
- **Documentation**: Technical docs, guides, FAQs

## Guidelines

### Structure
- Hook readers with a compelling opening
- Use clear headings and logical flow
- Keep paragraphs short (3-5 sentences)
- End with a clear conclusion or call-to-action

### Tone Adaptation
- **Formal**: Business reports, official communications
- **Professional**: Client-facing content, documentation
- **Conversational**: Blog posts, social media
- **Technical**: Developer docs, specifications

### Quality Checklist
- [ ] Clear main message
- [ ] Consistent tone throughout
- [ ] No grammatical errors
- [ ] Appropriate length for format
""",
            },
        ],
    },
    {
        "name": "Competitive Analysis",
        "description": "Competitor research and comparison frameworks. Use when: asked to compare companies, products, or perform SWOT/feature matrix analysis. NOT for: general academic research.",
        "category": "research",
        "icon": "⚔️",
        "folder_name": "competitive-analysis",
        "files": [
            {
                "path": "SKILL.md",
                "content": """---
name: Competitive Analysis
description: Market competitor research, comparison frameworks, and strategic insights
---

# Competitive Analysis

## Overview
Use this skill for analyzing competitors, market positioning, and strategic opportunities.

**Keywords**: competitors, market analysis, SWOT, positioning, benchmarking

## Frameworks

### SWOT Analysis
| | Helpful | Harmful |
|---|---|---|
| **Internal** | Strengths | Weaknesses |
| **External** | Opportunities | Threats |

### Feature Comparison Matrix
Compare products across key dimensions:
- Core features and capabilities
- Pricing and packaging
- Target audience
- Market positioning
- Technology stack

### Porter's Five Forces
1. Competitive rivalry intensity
2. Bargaining power of suppliers
3. Bargaining power of buyers
4. Threat of new entrants
5. Threat of substitutes

## Output Format
- Competitor overview table
- Detailed per-competitor analysis
- Strategic recommendations
- Key differentiators summary
""",
            },
        ],
    },
    {
        "name": "Meeting Notes",
        "description": "Meeting summarization and follow-up tracking. Use when: given meeting transcripts or rough notes to extract structured action items and key decisions. NOT for: generic document summarization.",
        "category": "productivity",
        "icon": "📝",
        "folder_name": "meeting-notes",
        "files": [
            {
                "path": "SKILL.md",
                "content": """---
name: Meeting Notes
description: Meeting summarization, action item extraction, and follow-up tracking
---

# Meeting Notes

## Overview
Use this skill for processing meeting content into structured summaries with clear action items.

**Keywords**: meetings, notes, action items, decisions, follow-up

## Template

### Meeting Summary
```
Meeting: [Title]
Date: [Date]
Participants: [Names]
Duration: [Time]
```

### Key Decisions
- Numbered list of decisions made

### Action Items
| # | Action | Owner | Due Date | Status |
|---|--------|-------|----------|--------|
| 1 | [Task] | [Name] | [Date] | ⬜ Pending |

### Discussion Points
Brief summary of main topics discussed

### Next Steps
- Follow-up meeting date
- Items deferred to next meeting
""",
            },
        ],
    },
    {
        "name": "Complex Task Executor",
        "description": "Structured methodology for decomposing, planning, and executing complex multi-step tasks with progress tracking",
        "category": "productivity",
        "icon": "🎯",
        "folder_name": "complex-task-executor",
        "is_default": True,
        "files": [
            {
                "path": "SKILL.md",
                "content": """---
name: Complex Task Executor
description: Structured methodology for decomposing, planning, and executing complex multi-step tasks with progress tracking
---

# Complex Task Executor

## When to Use This Skill

Use this skill when a task meets ANY of the following criteria:
- Requires more than 3 distinct steps to complete
- Involves multiple tools or information sources
- Has dependencies between steps (step B needs output from step A)
- Requires research before execution
- Could benefit from a documented plan others can review
- The user explicitly asks for a thorough or systematic approach

**DO NOT use this for simple tasks** like answering a question, reading a single file, or performing one tool call.

## Workflow

### Phase 1: Task Analysis (THINK before acting)

Before creating any files, analyze the task:

1. **Understand the goal**: What is the final deliverable? What does "done" look like?
2. **Assess complexity**: How many steps? What tools are needed?
3. **Identify dependencies**: Which steps depend on others?
4. **Identify risks**: What could go wrong? What information is missing?
5. **Estimate scope**: Is the task feasible with available tools/skills?

### Phase 2: Create Task Plan

Create a task folder and plan file in the workspace:

```
workspace/<task-name>/plan.md
```

The plan.md MUST follow this exact format:

```markdown
# Task: <Clear title>

## Objective
<One-sentence description of the desired outcome>

## Steps

- [ ] 1. <First step — verb-noun format>
  - Details: <What specifically to do>
  - Output: <What this step produces>
- [ ] 2. <Second step>
  - Details: <...>
  - Depends on: Step 1
- [ ] 3. <Third step>
  - Details: <...>

## Status
- Created: <timestamp>
- Current Step: Not started
- Progress: 0/<total>

## Notes
<Any assumptions, risks, or open questions>
```

Rules for writing the plan:
- Each step should be completable in 1-3 tool calls
- Use verb-noun format: "Research competitors", "Draft report", "Validate data"
- Mark dependencies explicitly
- Include expected outputs for each step

### Phase 3: Execute Step-by-Step

For EACH step in the plan:

1. **Read the plan** — Call `read_file` on `workspace/<task>/plan.md` to check current state
2. **Mark as in-progress** — Update the checkbox from `[ ]` to `[/]` and update the "Current Step" field
3. **Execute the step** — Do the actual work (tool calls, analysis, writing)
4. **Record output** — Save results to `workspace/<task>/` (e.g., intermediate files, data)
5. **Mark as complete** — Update the checkbox from `[/]` to `[x]` and update "Progress" counter
6. **Proceed to next step** — Move to the next uncompleted step

### Phase 4: Completion

When all steps are done:
1. Update plan.md status to "✅ Completed"
2. Create a `workspace/<task>/summary.md` with:
   - What was accomplished
   - Key results and deliverables
   - Any follow-up items
3. Present the final result to the user

## Adaptive Replanning

If during execution you discover:
- A step is impossible → Mark it `[!]` with a reason, add alternative steps
- New steps are needed → Add them to the plan with `[+]` prefix
- A step produced unexpected results → Add a note and adjust subsequent steps
- The plan needs major changes → Create a new section "## Revised Plan" and follow it

Always update plan.md BEFORE changing course, so the plan stays the source of truth.

## Error Handling

- If a tool call fails, retry once. If it fails again, mark the step as blocked and note the error.
- Never silently skip a step. Always update the plan to reflect what happened.
- If you're stuck, tell the user what's blocking and ask for guidance.

## Example Scenarios

### Example 1: "Research our top 3 competitors and write a comparison report"

Plan would be:
```
- [ ] 1. Identify the user's company/product context
- [ ] 2. Research Competitor A — website, pricing, features
- [ ] 3. Research Competitor B — website, pricing, features
- [ ] 4. Research Competitor C — website, pricing, features
- [ ] 5. Create comparison matrix
- [ ] 6. Write analysis and recommendations
- [ ] 7. Compile final report
```

### Example 2: "Analyze our Q4 sales data and prepare a board presentation"

Plan would be:
```
- [ ] 1. Read and understand the sales data files
- [ ] 2. Calculate key metrics (revenue, growth, trends)
- [ ] 3. Identify top insights and anomalies
- [ ] 4. Create data summary tables
- [ ] 5. Draft presentation outline
- [ ] 6. Write each presentation section
- [ ] 7. Add executive summary
- [ ] 8. Review and polish final document
```

## Key Principles

1. **Plan is the source of truth** — Always update it before moving on
2. **One step at a time** — Don't skip ahead or batch too many steps
3. **Show your work** — Save intermediate results to the task folder
4. **Communicate progress** — The user can read plan.md at any time to see status
5. **Be adaptive** — Plans change; that's OK if you update the plan first
""",
            },
            {
                "path": "examples/plan_template.md",
                "content": """# Task: [Title]

## Objective
[One-sentence description of the desired outcome]

## Steps

- [ ] 1. [First step]
  - Details: [What specifically to do]
  - Output: [What this step produces]
- [ ] 2. [Second step]
  - Details: [...]
  - Depends on: Step 1
- [ ] 3. [Third step]
  - Details: [...]

## Status
- Created: [timestamp]
- Current Step: Not started
- Progress: 0/3

## Notes
- [Any assumptions, risks, or open questions]
""",
            },
        ],
    },
    # ─── Billing Notifier ─────────────────────────
    {
        "name": "Billing Notifier",
        "description": "Send billing & payment reminders to customers across the full lifecycle (pre-bill notice → payment due → overdue follow-up) via Gmail and Twilio SMS. Pulls customer data from chat input, uploaded files in workspace/upload/, or Google Sheets.",
        "category": "productivity",
        "icon": "💸",
        "folder_name": "billing-notifier",
        "files": [
            {
                "path": "SKILL.md",
                "content": """---
name: Billing Notifier
description: Stage-aware billing & payment reminders to customers via Gmail and Twilio SMS
keywords: billing, invoice, payment reminder, overdue, dunning, accounts receivable, AR
---

# Billing Notifier

## Overview
Use this skill when the user wants to **notify customers about bills, upcoming due dates, or overdue payments**. The skill manages the full reminder lifecycle in distinct stages and delivers messages through **email (Gmail)** and **SMS (Twilio)**.

**When to use**
- "Send this month's invoices to my customers."
- "Remind everyone whose payment is due in 3 days."
- "Chase the customers who are past due."
- "Set up a 30/15/3/0/-7/-14 day reminder cadence."

**When NOT to use**
- For collecting payments (this skill only notifies; it does not process transactions).
- For one-off marketing emails (use a marketing/email skill instead).
- For internal team notifications (this skill targets external customers).

---

## Required Skills & Tools

This skill orchestrates other skills/tools. Confirm they are enabled for the agent before running:

| Channel / Source | Required | URI |
|---|---|---|
| Send email | yes | `skill://gws-gmail-send` |
| Send SMS | yes | `skill://twilio-api` |
| Read customers from Google Sheets | optional | `skill://gws-sheets-read` |
| Read uploaded customer files | optional | tool/skill that lists & reads `workspace/upload/` |

If a required dependency is missing, **stop and report it to the user** — do not attempt to fabricate the call.

---

## Stages

Every customer's reminder is classified into one stage based on `days_until_due` (positive = days remaining, negative = days overdue):

| Stage | `days_until_due` range | Default channels | Tone |
|---|---|---|---|
| `pre_bill` | `> 7` (bill issued, due far away) | email | Informational, friendly |
| `upcoming_due` | `1` to `7` | email + SMS | Polite reminder |
| `due_today` | `0` | email + SMS | Clear call to action |
| `overdue_grace` | `-1` to `-7` | email + SMS | Firm but courteous |
| `overdue_late` | `-8` to `-30` | email + SMS | Escalated; mention late fee policy if provided |
| `overdue_severe` | `< -30` | email + SMS | Final notice; warn about service suspension / collections |

Always compute the stage from **today's date** (in the customer's timezone if provided, else the user's local timezone) and the `due_date` field — never trust a pre-computed stage in the input.

---

## Inputs

The agent must collect, in priority order:

### 1. Customer source (one of)

**(a) Manual chat input** — user pastes a small list inline. Expect a JSON array or a markdown table with columns: `name, email, phone, amount, currency, invoice_id, due_date, [timezone, language, notes]`.

**(b) Uploaded file** in `workspace/upload/` — typically `.csv`, `.xlsx`, or `.json`. Use the file-listing/reading tools available to the agent to enumerate `workspace/upload/` and pick the file the user references. Required columns: same as above. Echo back the detected columns and a 2-row preview before sending anything.

**(c) Google Sheets** — user provides a sheet ID/URL and (optional) range. Use `skill://gws-sheets-read`. Required columns: same as above. The first row is the header.

### 2. Sender identity

- `from_email` (required when emailing): default to the user's connected Gmail account.
- `from_name` / `company_name`: the business name shown in the message body.
- `reply_to`: optional.
- `from_phone` (required when sending SMS): the Twilio number registered for this tenant.

### 3. Reminder policy (optional, with defaults)

- `cadence`: which stages to send for this run. Default = the auto-detected stage of each customer.
- `dry_run`: boolean. When true, render every message and show a preview table to the user but **do not call gws-gmail-send or twilio-api**.
- `late_fee`: optional string (e.g. "1.5% per month"). When set, included in `overdue_late` and `overdue_severe` templates.
- `payment_link`: optional URL. When set, included in every message.

---

## Process

1. **Confirm dependencies.** Verify `skill://gws-gmail-send` and `skill://twilio-api` are available. If only one is, ask the user whether to proceed with the available channel only.
2. **Load customers** from the chosen source. Validate: each row needs at minimum `name`, `due_date`, `amount`, plus `email` (for email channel) and/or `phone` in E.164 format (for SMS).
3. **Normalize**: parse `due_date` to a date, parse `amount` + `currency`, classify `phone` as E.164 (reject otherwise and report which rows were skipped), default `language` to `en` unless the row says otherwise.
4. **Compute stage** for each customer using the table above.
5. **Render preview**: produce a per-customer plan — name, email/phone, computed stage, channels to use, message subject/preview. Show this to the user **before** sending.
6. **Confirm with the user**. Only proceed to step 7 after explicit user approval (e.g. "send", "go ahead"). If `dry_run=true`, stop here.
7. **Send messages**, one customer at a time:
   - Email via `skill://gws-gmail-send` (subject + body from the template; use HTML body when available).
   - SMS via `skill://twilio-api` (single short message; truncate to 320 chars; never split into multiple SMS without telling the user).
   - Wait for each call's result and capture `message_id` (email) or `sid` (SMS).
8. **Report**: produce a final summary table of `customer | channel | status | reference_id | error?`. Include counts: sent / skipped / failed.

---

## Message Templates

Templates are guidelines; adjust wording to the customer's language (`zh`, `en`, `es`, etc.) and the user's brand voice. Always include: customer name, invoice id (if any), amount, currency, due date, and a payment link if provided.

### `pre_bill` — Email
> Subject: New invoice {{invoice_id}} from {{company_name}}
>
> Hi {{name}},
>
> Your invoice **{{invoice_id}}** for **{{amount}} {{currency}}** has been issued and is due on **{{due_date_long}}**. {{#payment_link}}You can review and pay it any time here: {{payment_link}}{{/payment_link}}
>
> Thanks,
> {{company_name}}

### `upcoming_due` — Email
> Subject: Reminder: invoice {{invoice_id}} due in {{days_until_due}} day(s)
>
> Hi {{name}},
>
> A friendly reminder that invoice **{{invoice_id}}** for **{{amount}} {{currency}}** is due on **{{due_date_long}}** ({{days_until_due}} day(s) from today). {{#payment_link}}Pay here: {{payment_link}}{{/payment_link}}

### `upcoming_due` — SMS
> {{company_name}}: Invoice {{invoice_id}} ({{amount}} {{currency}}) is due {{due_date_short}}. {{#payment_link}}Pay: {{payment_link}}{{/payment_link}}

### `due_today` — Email
> Subject: Invoice {{invoice_id}} is due today
>
> Hi {{name}},
>
> This is a reminder that invoice **{{invoice_id}}** for **{{amount}} {{currency}}** is due **today**. Please make payment by end of day to avoid late fees. {{#payment_link}}Pay: {{payment_link}}{{/payment_link}}

### `due_today` — SMS
> {{company_name}}: Invoice {{invoice_id}} ({{amount}} {{currency}}) is due TODAY. {{#payment_link}}Pay: {{payment_link}}{{/payment_link}}

### `overdue_grace` — Email
> Subject: Past due: invoice {{invoice_id}}
>
> Hi {{name}},
>
> Our records show invoice **{{invoice_id}}** for **{{amount}} {{currency}}** was due on **{{due_date_long}}** and is currently **{{days_overdue}} day(s) past due**. Please send payment as soon as possible. {{#payment_link}}Pay here: {{payment_link}}{{/payment_link}}
>
> If you've already paid, please ignore this notice.

### `overdue_late` — Email
> Subject: Action required: invoice {{invoice_id}} is {{days_overdue}} days overdue
>
> Hi {{name}},
>
> Invoice **{{invoice_id}}** for **{{amount}} {{currency}}** is now **{{days_overdue}} days past due**. {{#late_fee}}A late fee of {{late_fee}} may apply.{{/late_fee}} Please settle this invoice promptly. {{#payment_link}}Pay here: {{payment_link}}{{/payment_link}}

### `overdue_severe` — Email
> Subject: Final notice: invoice {{invoice_id}}
>
> Hi {{name}},
>
> This is a **final notice** for invoice **{{invoice_id}}** ({{amount}} {{currency}}), which is now **{{days_overdue}} days overdue**. If payment is not received within 7 days, your account may be suspended and the balance referred for collection. {{#payment_link}}Pay here to avoid suspension: {{payment_link}}{{/payment_link}}
>
> Please contact us immediately if there is an issue with this invoice.

---

## Safety Rules

- **Always preview & confirm before sending.** Never send messages without explicit user approval.
- **Validate phone numbers.** Reject anything not in E.164 (`+` followed by 8–15 digits). Skip those rows and report them.
- **Validate emails.** Reject anything that doesn't match a basic `local@domain.tld` pattern. Skip and report.
- **Per-run rate limit.** Batch sends in groups of ≤ 50 with a short pause between groups, to stay under provider rate limits. If the input has more than 500 customers, ask the user to confirm before proceeding.
- **No silent failures.** Every send result (success or failure) must appear in the final report.
- **No PII leakage.** When previewing or summarizing, mask phone (`+1***5678`) and email (`a***@example.com`) by default unless the user explicitly asks for full values.
- **Quiet hours.** By default, do not send SMS between 22:00 and 08:00 in the customer's timezone. Override only if the user explicitly asks.

---

## Example Conversations

**A — From Google Sheets, full lifecycle**
> User: "Run today's billing reminders. Customers are in this sheet: <url>. Use my company name 'Acme Co.', payment link is https://pay.acme.co/{{invoice_id}}."
>
> Agent: 1) read sheet via `skill://gws-sheets-read`, 2) classify each row by stage, 3) preview send plan, 4) on approval, send via `skill://gws-gmail-send` and `skill://twilio-api`, 5) report.

**B — From uploaded CSV, only overdue**
> User: "I uploaded customers.csv. Only chase the ones already overdue."
>
> Agent: list `workspace/upload/`, read `customers.csv`, filter to `overdue_*` stages, preview, confirm, send (with `late_fee` template if user provides one), report.

**C — Manual list, dry run**
> User: "Just preview a reminder for these three: <pasted list>. Don't actually send."
>
> Agent: parse the list, render the per-customer messages, present them in a preview table with `dry_run=true`, do **not** call any send skill.

---

## Output Format (final report)

| customer | stage | channel | status | reference | note |
|---|---|---|---|---|---|
| Alice Wang | upcoming_due | email | sent | <gmail-msg-id> | |
| Alice Wang | upcoming_due | sms | sent | <twilio-sid> | |
| Bob Li | overdue_late | sms | failed | — | invalid phone format |
| Carol Chen | due_today | email | skipped | — | dry_run=true |

End with a one-line summary: `Sent X email + Y SMS, skipped Z, failed W.`
""",
            },
        ],
    },
    # ─── Plaid Chase Transactions ─────────────────
    {
        "name": "Plaid Chase Transactions",
        "description": "Fetch customer payment transactions from Chase bank accounts via Plaid's /transactions/get API. Designed to feed the Billing Notifier so paid invoices can be reconciled and reminders skipped.",
        "category": "productivity",
        "icon": "🏦",
        "folder_name": "plaid-chase-transactions",
        "files": [
            {
                "path": "SKILL.md",
                "content": """---
name: Plaid Chase Transactions
description: Fetch and normalize Chase bank transactions from the Plaid /transactions/get API
keywords: plaid, chase, bank, transactions, payments, reconciliation, AR, accounts receivable
---

# Plaid Chase Transactions

## Overview
Use this skill when the user wants to **pull recent transactions from Chase bank accounts via Plaid**, typically to:
- Reconcile incoming customer payments against open invoices.
- Detect which customers in the **Billing Notifier** workflow have already paid so they can be skipped from reminders.
- Generate a daily / weekly cash-in report from Chase deposits.

This skill **only reads transactions**. It does not move money, modify accounts, or store data anywhere except the agent's working response.

Reference: https://plaid.com/docs/api/products/transactions/#transactionsget

**When to use**
- "Pull yesterday's Chase deposits."
- "Which customers paid us this week?"
- "Get all Chase transactions between 2026-04-01 and 2026-04-30."
- (As a sub-step) "Before sending billing reminders, exclude anyone who already paid via Chase."

**When NOT to use**
- For non-Chase banks (this skill is Chase-only; for other institutions use a generalized Plaid skill).
- For real-time webhooks (use Plaid's `/webhooks` channel instead).
- For initiating payments or transfers.

---

## Required Configuration

Before running, **verify the following environment variables / secrets are configured for the tenant** (ask the user to add any that are missing — never invent values):

| Variable | Purpose | Notes |
|---|---|---|
| `PLAID_CLIENT_ID` | Plaid client ID | from Plaid Dashboard → Team Settings → Keys |
| `PLAID_SECRET` | Plaid secret | environment-specific (sandbox / development / production) |
| `PLAID_ENV` | one of `sandbox`, `development`, `production` | determines API base URL |
| `PLAID_CHASE_ACCESS_TOKENS` | JSON array of Plaid `access_token`s belonging to Chase Items | one entry per linked Chase Item; obtained via `/item/public_token/exchange` |

The base URL is derived from `PLAID_ENV`:
- `sandbox` → `https://sandbox.plaid.com`
- `development` → `https://development.plaid.com`
- `production` → `https://production.plaid.com`

If `PLAID_CHASE_ACCESS_TOKENS` is missing, **stop and instruct the user how to obtain one** (Plaid Link flow). Do not attempt the call without a valid access_token — Plaid will reject it.

---

## How to Execute

This skill ships with a **runnable Python script** at `skills/plaid-chase-transactions/script.py` that handles all the HTTP, pagination, retry, and normalization for you. **You should always call the script** rather than re-implement the Plaid calls inline.

**Always `cd` into the skill folder first, then run the script with a relative path:**

```bash
cd skills/plaid-chase-transactions && python script.py '<JSON_ARGS>'
```

Why `cd` matters:
- The script's `.env` auto-loader looks first in the current working directory. Running from inside the skill folder lets you keep a local `skills/plaid-chase-transactions/.env` for credentials that overrides nothing in the global env.
- Relative paths in any future companion files (logs, cache, fixtures) resolve consistently.
- Avoids ambiguity about which `.env` is picked up.

Do **not** invoke the script with an absolute path or from the agent workspace root — always change directory first.

`<JSON_ARGS>` is a single JSON string, for example:

```json
{"start_date":"2026-04-01","end_date":"2026-04-30","direction":"inflow","include_pending":false}
```

The script reads `PLAID_CLIENT_ID`, `PLAID_SECRET`, `PLAID_ENV`, and `PLAID_CHASE_ACCESS_TOKENS` from the process environment (already injected by the runtime — do not pass them on the command line). If any are missing it will fall back to a `.env` file in the current working directory (which is why `cd` matters). It writes a single JSON object to **stdout** (the result you'll parse) and human-readable progress to **stderr** (safe to ignore).

If the script exits non-zero, the JSON object on stdout will contain an `"error"` field; surface it verbatim to the user.

Skip directly to **Output Format** below for the shape of stdout.

---

## Inputs

The agent must collect:

| Field | Required | Default | Notes |
|---|---|---|---|
| `start_date` | yes | — | `YYYY-MM-DD` |
| `end_date` | yes | today | `YYYY-MM-DD` |
| `account_ids` | no | all Chase accounts under each token | array of Plaid `account_id`s; restricts to specific accounts |
| `min_amount` | no | none | filter out transactions below this absolute amount |
| `direction` | no | `inflow` | one of `inflow`, `outflow`, `both`; "inflow" = money received (customer payments) |
| `include_pending` | no | `false` | whether to include `pending=true` transactions |

**Date validation**: `end_date` must be ≥ `start_date`, and the window must be ≤ 730 days (Plaid's max history per call).

---

## Process

For **each** access_token in `PLAID_CHASE_ACCESS_TOKENS`:

### 1. Sanity-check the Item (optional but recommended)
Call `POST {base_url}/item/get` with `{client_id, secret, access_token}`. Inspect `item.institution_id` — if it is **not `ins_56`** (Chase), skip this token and warn the user. This prevents accidentally pulling transactions from a non-Chase Item that was placed in the wrong env var.

### 2. Fetch transactions with pagination
Plaid's `/transactions/get` returns at most `count` results per call (max `count` = 500). The full count is in `total_transactions`. Loop:

```
offset = 0
all_txs = []
while True:
    body = {
        "client_id": PLAID_CLIENT_ID,
        "secret": PLAID_SECRET,
        "access_token": <token>,
        "start_date": start_date,
        "end_date": end_date,
        "options": {
            "count": 500,
            "offset": offset,
            "include_personal_finance_category": true
        }
    }
    if account_ids:  body["options"]["account_ids"] = account_ids
    resp = POST {base_url}/transactions/get  with body
    all_txs += resp.transactions
    offset += len(resp.transactions)
    if offset >= resp.total_transactions: break
```

### 3. Handle Plaid's "not yet ready" response
If the response contains `error_code = "PRODUCT_NOT_READY"`, the Item was just linked and Plaid hasn't pulled history yet. **Wait 30 seconds and retry up to 3 times**, then surface the error to the user with guidance to retry later.

### 4. Filter
- Drop any transaction with `pending=true` unless `include_pending=true`.
- Apply `direction` filter:
  - In Plaid, **positive `amount` = money leaving the account (debit)**, **negative `amount` = money entering (credit)**. Customer payments to you are typically negative amounts on a depository account.
  - `inflow` → keep `amount < 0`
  - `outflow` → keep `amount > 0`
  - `both` → keep all
- Apply `min_amount` filter on `abs(amount)`.
- Apply `account_ids` filter if not already done at the API level.

### 5. Normalize into a stable schema
Map each Plaid transaction to:

```
{
  "transaction_id":   str,                  # plaid transaction_id (stable)
  "account_id":       str,
  "account_mask":     str|null,             # last 4 digits if available via /accounts/get
  "date":             "YYYY-MM-DD",         # posted date (transaction.date)
  "authorized_date":  "YYYY-MM-DD"|null,
  "amount_raw":       float,                # Plaid's signed amount
  "amount":           float,                # abs(amount_raw); positive
  "currency":         str,                  # iso_currency_code or unofficial
  "direction":        "inflow"|"outflow",
  "pending":          bool,
  "name":             str,                  # transaction.name
  "merchant_name":    str|null,
  "category":         str|null,             # personal_finance_category.primary
  "payment_channel":  str|null,             # online | in_store | other
  "counterparty":     str|null,             # best-effort guess at payer/payee
}
```

Set `counterparty`:
- Prefer `merchant_name` if present.
- Else parse `name` for known patterns: `"ZELLE FROM <NAME>"`, `"ACH CREDIT <NAME>"`, `"WIRE FROM <NAME>"`, `"DEPOSIT FROM <NAME>"`. The trailing `<NAME>` is the counterparty.
- Else fall back to the raw `name`.

### 6. Deduplicate
If the user is calling this skill repeatedly, dedupe across runs by `transaction_id`. Within a single run, Plaid does not return duplicates.

---

## Output Format

Return a JSON object:

```json
{
  "summary": {
    "start_date": "...",
    "end_date": "...",
    "items_queried": 1,
    "transactions_fetched": 142,
    "transactions_after_filter": 27,
    "total_inflow": 18540.00,
    "total_outflow": 0,
    "currency": "USD"
  },
  "transactions": [ ... normalized rows ... ],
  "warnings": [ "..." ]
}
```

Then present a markdown summary to the user with:
- A one-line headline (`Pulled 27 incoming Chase payments totaling $18,540 between {start_date} and {end_date}.`)
- A table of the top 20 inflows (date, counterparty, amount, account_mask).
- A note about how many were filtered out and why.

---

## Reconciliation Mode (optional)

If the user provides a list of open invoices (or asks to "match against the Billing Notifier list"), additionally:

1. Take each invoice with `(customer_name, amount, currency)`.
2. For each fetched inflow, score it against each open invoice:
   - **+3** if `counterparty` token-overlaps `customer_name` (case-insensitive).
   - **+2** if `amount` matches `invoice.amount` exactly (same currency).
   - **+1** if `amount` is within 1% of `invoice.amount`.
3. Mark the highest-scoring (≥ 4) match as `likely_paid` and emit a `paid_invoice_ids` list.
4. Surface ambiguous matches (score 2-3) for the user to confirm; do not auto-mark them.

The `paid_invoice_ids` output can be passed directly into `skill://billing-notifier` to suppress reminders for those customers.

---

## Safety Rules

- **Read-only.** Never call any Plaid endpoint that mutates state (e.g. `/item/remove`, `/sandbox/...`).
- **Never log secrets.** Do not echo `PLAID_CLIENT_ID`, `PLAID_SECRET`, or `access_token` values in any output, message, or stored artifact.
- **Mask account numbers.** Account masks (`last4`) are okay; full account/routing numbers are never returned by Plaid here, but if they ever appear, replace with `****`.
- **Stop on auth failure.** If Plaid returns `INVALID_API_KEYS` or `INVALID_ACCESS_TOKEN`, halt and tell the user which credential needs to be re-issued — do not retry blindly.
- **Bounded retries.** At most 3 retries on transient errors (`API_ERROR`, `PLANNED_MAINTENANCE`, `RATE_LIMIT_EXCEEDED`) with exponential backoff (2s, 8s, 30s).
- **Time window cap.** Refuse a single call if `end_date - start_date > 730` days; ask the user to chunk it.

---

## Example Conversations

**A — Daily reconcile**
> User: "Show me yesterday's Chase deposits."
>
> Agent: derive `start_date = end_date = yesterday`, `direction = inflow`, run the pagination loop for each token, normalize, present headline + table.

**B — Custom range, both directions**
> User: "Get all Chase activity in March, both directions, min $100."
>
> Agent: `start_date = 2026-03-01`, `end_date = 2026-03-31`, `direction = both`, `min_amount = 100`. Group output by `direction` in the summary.

**C — Pre-billing check**
> User: "Before you send today's billing reminders, check who paid via Chase in the last 7 days."
>
> Agent: pull last-7-day inflows → switch into Reconciliation Mode against the open invoices the Billing Notifier is about to remind → return `paid_invoice_ids` so the Billing Notifier skill skips them.
""",
            },
            {
                "path": "script.py",
                "content": r'''#!/usr/bin/env python3
"""Plaid Chase Transactions runner.

Invocation:
    python script.py '<JSON_ARGS>'

JSON_ARGS schema (all keys optional unless marked required):
    start_date         (required) "YYYY-MM-DD"
    end_date           (default: today) "YYYY-MM-DD"
    account_ids        (optional) [str, ...]
    min_amount         (optional) float, applied to abs(amount)
    direction          (default: "inflow") one of "inflow"|"outflow"|"both"
    include_pending    (default: false) bool
    enforce_chase_only (default: true) bool, skips tokens whose Item institution_id != ins_56
    open_invoices      (optional) reconciliation mode: [{"id":..., "customer_name":..., "amount":..., "currency":"USD"}, ...]

Reads from environment (never from argv):
    PLAID_CLIENT_ID, PLAID_SECRET, PLAID_ENV (sandbox|development|production),
    PLAID_CHASE_ACCESS_TOKENS (JSON array of access_token strings)

If any of these are missing from os.environ, the script will additionally try to
load them from a `.env` file. Search order (first match wins):
    1.  $PLAID_DOTENV (explicit override)
    2.  ./.env  (current working directory)
    3.  <script dir>/.env  (i.e. skills/plaid-chase-transactions/.env)
    4.  <script dir>/../../.env  (agent workspace root)
Existing os.environ values always take precedence — the .env file only fills gaps.

Writes a single JSON object to stdout. Progress / warnings go to stderr.
Exit code 0 = success (even with per-token warnings); non-zero = fatal config error.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


PLAID_BASE = {
    "sandbox": "https://sandbox.plaid.com",
    "development": "https://development.plaid.com",
    "production": "https://production.plaid.com",
}

CHASE_INSTITUTION_ID = "ins_56"
MAX_WINDOW_DAYS = 730
PAGE_SIZE = 500
TRANSIENT_ERRORS = {"API_ERROR", "PLANNED_MAINTENANCE", "RATE_LIMIT_EXCEEDED"}
NOT_READY = "PRODUCT_NOT_READY"
FATAL_AUTH = {"INVALID_API_KEYS", "INVALID_ACCESS_TOKEN", "INVALID_SECRET"}

COUNTERPARTY_PATTERNS = [
    re.compile(r"ZELLE\s+FROM\s+(.+?)(?:\s+ON\s+|\s*$)", re.I),
    re.compile(r"ACH\s+CREDIT\s+(.+?)(?:\s+REF\s+|\s*$)", re.I),
    re.compile(r"WIRE\s+FROM\s+(.+?)(?:\s+REF\s+|\s*$)", re.I),
    re.compile(r"DEPOSIT\s+FROM\s+(.+?)(?:\s+ON\s+|\s*$)", re.I),
]


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def fail(message: str, code: int = 2) -> None:
    print(json.dumps({"error": message}), flush=True)
    sys.exit(code)


def _parse_dotenv_line(line: str) -> tuple[str, str] | None:
    """Parse a single KEY=VALUE line. Supports quoted values and `export` prefix."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if line.startswith("export "):
        line = line[len("export "):].lstrip()
    if "=" not in line:
        return None
    key, _, val = line.partition("=")
    key = key.strip()
    val = val.strip()
    if not key or not key.replace("_", "").isalnum():
        return None
    if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
        quote = val[0]
        val = val[1:-1]
        if quote == '"':
            val = val.encode("utf-8").decode("unicode_escape")
    else:
        val = val.split(" #", 1)[0].rstrip()
    return key, val


def _candidate_dotenv_paths() -> list[Path]:
    paths: list[Path] = []
    explicit = os.environ.get("PLAID_DOTENV")
    if explicit:
        paths.append(Path(explicit))
    paths.append(Path.cwd() / ".env")
    here = Path(__file__).resolve().parent
    paths.append(here / ".env")
    paths.append(here.parent.parent / ".env")
    seen: set[Path] = set()
    out: list[Path] = []
    for p in paths:
        try:
            rp = p.resolve()
        except OSError:
            continue
        if rp in seen:
            continue
        seen.add(rp)
        out.append(p)
    return out


def load_dotenv_into_environ(required: list[str]) -> str | None:
    """Fill missing required keys from the first .env file found. Existing env wins.

    Returns the path of the .env file that was read, or None if none was needed/found.
    """
    if all(os.environ.get(k) for k in required):
        return None
    for path in _candidate_dotenv_paths():
        try:
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            log(f"  .env candidate {path} unreadable: {e}")
            continue
        loaded = 0
        for raw_line in text.splitlines():
            parsed = _parse_dotenv_line(raw_line)
            if not parsed:
                continue
            k, v = parsed
            if k in required and not os.environ.get(k):
                os.environ[k] = v
                loaded += 1
        log(f"  loaded {loaded} key(s) from {path}")
        return str(path)
    return None


def post(url: str, body: dict, timeout: int = 30) -> dict:
    """POST JSON, return parsed JSON. Never raises for HTTP 400 — Plaid uses 400 for app errors."""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:
            return {"error_code": "HTTP_ERROR", "error_message": f"HTTP {e.code}", "_status": e.code}
    except urllib.error.URLError as e:
        return {"error_code": "NETWORK_ERROR", "error_message": str(e.reason)}


def post_with_retry(url: str, body: dict) -> dict:
    """Bounded retry on transient errors and PRODUCT_NOT_READY."""
    backoffs = [2, 8, 30]
    not_ready_waits = [30, 30, 30]
    for attempt in range(4):
        resp = post(url, body)
        err = resp.get("error_code")
        if not err:
            return resp
        if err in FATAL_AUTH:
            return resp
        if err == NOT_READY and attempt < len(not_ready_waits):
            log(f"PRODUCT_NOT_READY, sleeping {not_ready_waits[attempt]}s (attempt {attempt+1}/3)")
            time.sleep(not_ready_waits[attempt])
            continue
        if err in TRANSIENT_ERRORS and attempt < len(backoffs):
            log(f"transient {err}, sleeping {backoffs[attempt]}s (attempt {attempt+1}/3)")
            time.sleep(backoffs[attempt])
            continue
        return resp
    return resp


def parse_args() -> dict:
    if len(sys.argv) < 2:
        fail("missing JSON_ARGS argv[1]")
    try:
        args = json.loads(sys.argv[1])
    except json.JSONDecodeError as e:
        fail(f"argv[1] is not valid JSON: {e}")
    if not isinstance(args, dict):
        fail("argv[1] must be a JSON object")
    if "start_date" not in args:
        fail("start_date is required (YYYY-MM-DD)")
    args.setdefault("end_date", date.today().isoformat())
    args.setdefault("direction", "inflow")
    args.setdefault("include_pending", False)
    args.setdefault("enforce_chase_only", True)
    if args["direction"] not in ("inflow", "outflow", "both"):
        fail("direction must be one of: inflow, outflow, both")
    try:
        sd = datetime.strptime(args["start_date"], "%Y-%m-%d").date()
        ed = datetime.strptime(args["end_date"], "%Y-%m-%d").date()
    except ValueError as e:
        fail(f"date parse error: {e}")
    if ed < sd:
        fail("end_date must be >= start_date")
    if (ed - sd).days > MAX_WINDOW_DAYS:
        fail(f"window {(ed - sd).days}d exceeds Plaid's {MAX_WINDOW_DAYS}d max — chunk the call")
    return args


def load_env() -> tuple[str, str, str, list[str]]:
    required = ["PLAID_CLIENT_ID", "PLAID_SECRET", "PLAID_ENV", "PLAID_CHASE_ACCESS_TOKENS"]
    src = load_dotenv_into_environ(required)
    if src:
        log(f"merged missing env keys from {src}")
    cid = os.environ.get("PLAID_CLIENT_ID")
    sec = os.environ.get("PLAID_SECRET")
    env = os.environ.get("PLAID_ENV")
    raw_tokens = os.environ.get("PLAID_CHASE_ACCESS_TOKENS")
    missing = [k for k, v in [
        ("PLAID_CLIENT_ID", cid), ("PLAID_SECRET", sec),
        ("PLAID_ENV", env), ("PLAID_CHASE_ACCESS_TOKENS", raw_tokens),
    ] if not v]
    if missing:
        fail(f"missing required env vars: {', '.join(missing)}")
    if env not in PLAID_BASE:
        fail(f"PLAID_ENV must be one of {list(PLAID_BASE)}; got {env!r}")
    try:
        tokens = json.loads(raw_tokens)
    except json.JSONDecodeError:
        fail("PLAID_CHASE_ACCESS_TOKENS must be a JSON array of strings")
    if not isinstance(tokens, list) or not all(isinstance(t, str) and t for t in tokens):
        fail("PLAID_CHASE_ACCESS_TOKENS must be a non-empty JSON array of strings")
    return cid, sec, env, tokens


def extract_counterparty(name: str | None, merchant: str | None) -> str | None:
    if merchant:
        return merchant
    if not name:
        return None
    for pat in COUNTERPARTY_PATTERNS:
        m = pat.search(name)
        if m:
            return m.group(1).strip()
    return name


def normalize(tx: dict, account_masks: dict[str, str]) -> dict:
    amt_raw = float(tx.get("amount") or 0.0)
    direction = "inflow" if amt_raw < 0 else "outflow"
    pfc = tx.get("personal_finance_category") or {}
    return {
        "transaction_id": tx.get("transaction_id"),
        "account_id": tx.get("account_id"),
        "account_mask": account_masks.get(tx.get("account_id") or ""),
        "date": tx.get("date"),
        "authorized_date": tx.get("authorized_date"),
        "amount_raw": amt_raw,
        "amount": abs(amt_raw),
        "currency": tx.get("iso_currency_code") or tx.get("unofficial_currency_code"),
        "direction": direction,
        "pending": bool(tx.get("pending")),
        "name": tx.get("name"),
        "merchant_name": tx.get("merchant_name"),
        "category": pfc.get("primary"),
        "payment_channel": tx.get("payment_channel"),
        "counterparty": extract_counterparty(tx.get("name"), tx.get("merchant_name")),
    }


def fetch_account_masks(base: str, cid: str, sec: str, token: str) -> dict[str, str]:
    resp = post_with_retry(f"{base}/accounts/get", {
        "client_id": cid, "secret": sec, "access_token": token,
    })
    if resp.get("error_code"):
        return {}
    return {a["account_id"]: (a.get("mask") or "") for a in resp.get("accounts", [])}


def check_chase(base: str, cid: str, sec: str, token: str) -> tuple[bool, str | None]:
    resp = post_with_retry(f"{base}/item/get", {
        "client_id": cid, "secret": sec, "access_token": token,
    })
    if resp.get("error_code"):
        return False, resp.get("error_message") or resp.get("error_code")
    inst = (resp.get("item") or {}).get("institution_id")
    if inst != CHASE_INSTITUTION_ID:
        return False, f"institution_id={inst!r} (not Chase ins_56)"
    return True, None


def fetch_all_transactions(base: str, cid: str, sec: str, token: str, args: dict) -> tuple[list[dict], str | None]:
    out: list[dict] = []
    offset = 0
    while True:
        body: dict[str, Any] = {
            "client_id": cid, "secret": sec, "access_token": token,
            "start_date": args["start_date"], "end_date": args["end_date"],
            "options": {
                "count": PAGE_SIZE, "offset": offset,
                "include_personal_finance_category": True,
            },
        }
        if args.get("account_ids"):
            body["options"]["account_ids"] = args["account_ids"]
        resp = post_with_retry(f"{base}/transactions/get", body)
        err = resp.get("error_code")
        if err:
            return out, resp.get("error_message") or err
        page = resp.get("transactions") or []
        out.extend(page)
        offset += len(page)
        total = int(resp.get("total_transactions") or 0)
        log(f"  fetched {offset}/{total}")
        if offset >= total or not page:
            break
    return out, None


def reconcile(inflows: list[dict], invoices: list[dict]) -> dict:
    paid_ids: list[str] = []
    likely: list[dict] = []
    ambiguous: list[dict] = []
    for tx in inflows:
        best_score = 0
        best_inv = None
        for inv in invoices:
            score = 0
            cp = (tx.get("counterparty") or "").lower()
            cn = (inv.get("customer_name") or "").lower()
            if cp and cn:
                cp_tokens = set(re.findall(r"\w+", cp))
                cn_tokens = set(re.findall(r"\w+", cn))
                if cp_tokens & cn_tokens:
                    score += 3
            try:
                ia = float(inv.get("amount") or 0)
                ta = float(tx.get("amount") or 0)
                cur_match = (inv.get("currency") or "USD").upper() == (tx.get("currency") or "USD").upper()
                if cur_match and ia and abs(ta - ia) < 0.005:
                    score += 2
                elif cur_match and ia and abs(ta - ia) / ia <= 0.01:
                    score += 1
            except (TypeError, ValueError):
                pass
            if score > best_score:
                best_score, best_inv = score, inv
        if best_inv and best_score >= 4:
            paid_ids.append(best_inv["id"])
            likely.append({"transaction_id": tx["transaction_id"], "invoice_id": best_inv["id"], "score": best_score})
        elif best_inv and best_score >= 2:
            ambiguous.append({"transaction_id": tx["transaction_id"], "invoice_id": best_inv["id"], "score": best_score})
    return {"paid_invoice_ids": paid_ids, "likely_matches": likely, "ambiguous_matches": ambiguous}


def main() -> None:
    args = parse_args()
    cid, sec, env, tokens = load_env()
    base = PLAID_BASE[env]
    log(f"Plaid env={env}, tokens={len(tokens)}, range={args['start_date']}..{args['end_date']}")

    all_txs: list[dict] = []
    warnings: list[str] = []
    items_queried = 0

    for i, token in enumerate(tokens, 1):
        log(f"[token {i}/{len(tokens)}]")
        if args.get("enforce_chase_only", True):
            ok, why = check_chase(base, cid, sec, token)
            if not ok:
                warnings.append(f"token #{i} skipped: {why}")
                log(f"  skipped: {why}")
                continue
        masks = fetch_account_masks(base, cid, sec, token)
        raw, err = fetch_all_transactions(base, cid, sec, token, args)
        if err:
            warnings.append(f"token #{i} fetch error: {err}")
            log(f"  fetch error: {err}")
            continue
        items_queried += 1
        all_txs.extend(normalize(t, masks) for t in raw)

    pre = len(all_txs)
    if not args["include_pending"]:
        all_txs = [t for t in all_txs if not t["pending"]]
    direction = args["direction"]
    if direction == "inflow":
        all_txs = [t for t in all_txs if t["direction"] == "inflow"]
    elif direction == "outflow":
        all_txs = [t for t in all_txs if t["direction"] == "outflow"]
    if args.get("min_amount") is not None:
        m = float(args["min_amount"])
        all_txs = [t for t in all_txs if t["amount"] >= m]

    seen: set[str] = set()
    deduped: list[dict] = []
    for t in all_txs:
        tid = t.get("transaction_id") or ""
        if tid and tid in seen:
            continue
        if tid:
            seen.add(tid)
        deduped.append(t)
    all_txs = deduped

    inflow_total = sum(t["amount"] for t in all_txs if t["direction"] == "inflow")
    outflow_total = sum(t["amount"] for t in all_txs if t["direction"] == "outflow")
    currencies = sorted({t["currency"] for t in all_txs if t.get("currency")})

    result: dict = {
        "summary": {
            "start_date": args["start_date"],
            "end_date": args["end_date"],
            "items_queried": items_queried,
            "transactions_fetched": pre,
            "transactions_after_filter": len(all_txs),
            "total_inflow": round(inflow_total, 2),
            "total_outflow": round(outflow_total, 2),
            "currency": currencies[0] if len(currencies) == 1 else (currencies or None),
        },
        "transactions": all_txs,
        "warnings": warnings,
    }

    invoices = args.get("open_invoices")
    if invoices:
        inflows = [t for t in all_txs if t["direction"] == "inflow"]
        result["reconciliation"] = reconcile(inflows, invoices)

    print(json.dumps(result, default=str), flush=True)


if __name__ == "__main__":
    main()
''',
            },
        ],
    },
    # ─── Skill Creator (mandatory default) ─────────
    {
        "name": "Skill Creator",
        "description": "Create new skills, modify and improve existing skills, and measure skill performance",
        "category": "development",
        "icon": "🛠️",
        "folder_name": "skill-creator",
        "is_default": True,
        "files": [],  # populated at runtime from skill_creator_content
    },
    # ─── Content Research Writer ──────────────────
    {
        "name": "Content Research Writer",
        "description": "Assists in writing high-quality content by conducting research, adding citations, improving hooks, iterating on outlines, and providing real-time section feedback",
        "category": "writing",
        "icon": "✍️",
        "folder_name": "content-research-writer",
        "files": [],  # populated at runtime
    },
    # ─── MCP Tool Installer (mandatory default) ──────────────
    {
        "name": "MCP Tool Installer",
        "description": "Guide users through discovering, configuring, and installing MCP tools directly in chat — no Settings page required",
        "category": "development",
        "icon": "🔌",
        "folder_name": "mcp-installer",
        "is_default": True,
        "files": [],  # populated at runtime from agent_template/skills/mcp-installer/SKILL.md
    },
    # ─── Market Data (trading agents) ──────────────
    {
        "name": "Market Data",
        "description": "Fetch stock quotes, OHLCV history, and fundamentals via a remote MCP server. Use when a trading agent needs price/financial data on US equities.",
        "category": "trading",
        "icon": "MD",
        "folder_name": "market-data",
        "files": [
            {
                "path": "SKILL.md",
                "content": """---
name: Market Data
description: Stock quotes, OHLCV history, and fundamentals for US equities via Smithery MCP
---

# Market Data

## When to Use This Skill

Use when a trading agent needs:
- Real-time or historical price data on US equities (NYSE / NASDAQ)
- Financial statements (income, balance sheet, cash flow)
- Pre-computed technical indicators (RSI, MACD, Bollinger Bands, SMA, EMA, ADX, etc.)
- Quarterly EPS actuals, estimates, and surprises

**Scope (v1)**: US-listed equities only. **Not yet covered**: futures (CL=F, GC=F, ES=F), forex, crypto, international stocks. For these, fall back to `web-research`.

---

## Step-by-Step Protocol

### Step 1 — Check if Shibui Finance MCP is already installed

Look at your tool list. If you have `unlock_financial_analysis` and `stock_data_query` tools, skip to Step 3.

### Step 2 — Install via MCP_INSTALLER

Use the `mcp-installer` skill to install Shibui Finance (free, no API key, no per-call cost):

```
import_mcp_server(
  server_id="shibui/finance",
  config={"smithery_api_key": "<key>"}  # only on first import; reused after
)
```

If the user has not yet provided a Smithery API key, the `mcp-installer` skill explains how to register and obtain one.

### Step 3 — Activate the data session

The Shibui MCP requires a one-time activation per session before SQL queries work:

```
unlock_financial_analysis(...)
```

This returns an access token automatically managed by the MCP — you don't need to pass it in subsequent calls.

### Step 4 — Query data

The primary tool is `stock_data_query`, which takes natural-language prompts or SQL. Examples:

#### Get latest quote
```
stock_data_query(query="Get the most recent close price, daily change %, and volume for AAPL")
```

#### Get OHLCV history
```
stock_data_query(query="Daily OHLCV for TSLA over the past 90 trading days")
```

#### Get fundamentals
```
stock_data_query(query="Latest annual income statement and balance sheet for MSFT, with key ratios PE PB ROE")
```

#### Get pre-computed indicator
```
stock_data_query(query="14-day RSI for NVDA over the past 30 trading days")
```

#### Symbol screening
```
stock_data_query(query="US stocks with market cap > $10B, P/E < 20, and revenue growth > 15% YoY")
```

### Step 5 — Always cite as-of date

Every fetched number ships with the **as-of date** Shibui returns. Include it in your output to the user — never present stale data without timestamp context.

---

## Output Conventions

When you present market data to the user:

- Quote: `**AAPL** $192.45 +1.2% · Vol 48.2M · as of 2026-04-25 close`
- Indicator: `**TSLA RSI(14)** 68.4 (mildly overbought) · as of 2026-04-25`
- Fundamentals: bullet the headline numbers + 1-line interpretation, never dump raw tables

For OHLCV history with many rows, save to `workspace/<task>/<symbol>-history.csv` rather than rendering inline.

---

## What NOT to Do

- Do not present data without an as-of date — stale prices mislead
- Do not extrapolate from one query to another asset class (no futures, FX, crypto via this MCP)
- Do not exceed reasonable query depth — Shibui is free, but courtesy says don't run 100 SQL queries when 5 will do
- Do not fabricate numbers when the MCP can't answer — say "not available via this skill, falling back to web-research"

---

## Fallback (if Shibui MCP not available)

If the user can't / won't install the MCP, downgrade to `web-research`:
- Quotes: search "AAPL stock price now"
- History: search "AAPL daily chart 90 days"
- Fundamentals: search "AAPL 10-Q latest" or company IR page

Always tell the user "I'm using web search instead of structured market data — accuracy and timeliness will be lower."

---

## Asset Class Coverage (clawith roadmap)

| Asset class | v1 (this skill) | v2 plan |
|---|---|---|
| US equities | Yes (Shibui) | — |
| US ETFs | Partial (Shibui) | improve |
| Futures (CME) | No — use web-research | self-built yfinance MCP |
| Forex | No — use web-research | self-built MCP |
| Crypto | No — use web-research | dedicated crypto MCP |
| International stocks | No — use web-research | TBD |
""",
            },
        ],
    },
    # ─── Financial Calendar (trading agents) ──────────────
    {
        "name": "Financial Calendar",
        "description": "Look up earnings dates, FOMC meetings, CPI/NFP/GDP release dates, and other macro events that move markets. v1 uses structured web search; v2 will add dedicated MCP.",
        "category": "trading",
        "icon": "FC",
        "folder_name": "financial-calendar",
        "files": [
            {
                "path": "SKILL.md",
                "content": """---
name: Financial Calendar
description: Earnings calendar + macro events (FOMC, CPI, NFP, central banks) via structured web research
---

# Financial Calendar

## When to Use This Skill

Use when a trading agent needs:
- Upcoming earnings release dates for specific companies (or this week's reporters)
- Federal Reserve FOMC meeting dates and minutes release
- US economic data release schedule: CPI, PPI, NFP, GDP, retail sales, ISM, PCE
- Central bank decision dates (ECB, BoE, BoJ, PBoC)
- Geopolitical / fiscal events (debt ceiling, election dates, OPEC meetings)

---

## Implementation Note (v1)

clawith does **not** ship a dedicated calendar MCP server in v1. Smithery doesn't yet have a robust earnings/macro calendar tool. So this skill is a **structured wrapper around `web-research`** with curated query templates and source preferences. v2 will add a dedicated MCP backed by a free API (likely finnhub or trading-economics).

This means: every calendar query in v1 takes a web round-trip. Cache results in `memory/calendar_<month>.md` so the agent doesn't re-fetch the same Fed schedule three times in one week.

---

## Step-by-Step Protocol

### Step 1 — Check memory first

Before web searching, check `memory/calendar_<YYYY-MM>.md` for the current month. If you've already cached this month's events, use them and only web-search for what's missing.

### Step 2 — Run targeted query (use templates below)

#### Earnings calendar
```
web_research("AAPL next earnings date 2026 site:investor.apple.com OR site:nasdaq.com")
```

For a sector / market scan: `"this week earnings calendar US large cap"` then verify each name against IR sources.

#### FOMC schedule
```
web_research("Federal Reserve FOMC meeting schedule 2026 site:federalreserve.gov")
```

Authoritative source: federalreserve.gov/monetarypolicy/fomccalendars.htm — the calendar page directly.

#### US economic data calendar
```
web_research("BLS CPI release schedule 2026 site:bls.gov")
web_research("Bureau of Economic Analysis GDP release schedule 2026 site:bea.gov")
web_research("BLS Employment Situation NFP schedule 2026 site:bls.gov")
```

#### Central bank decisions
```
web_research("ECB Governing Council meeting schedule 2026 site:ecb.europa.eu")
web_research("Bank of England MPC schedule 2026 site:bankofengland.co.uk")
```

#### Aggregate calendar (lower fidelity, faster)
```
web_research("economic calendar this week high impact events")
```
Trusted aggregators: investing.com/economic-calendar, forexfactory.com/calendar, tradingeconomics.com/calendar

### Step 3 — Persist to memory

After each successful fetch, append to `memory/calendar_<YYYY-MM>.md`:

```markdown
## 2026-04 Calendar (last updated: 2026-04-27)

### FOMC
- 2026-04-30: rate decision + press conference (1 day, both PM EDT)
- 2026-06-12: rate decision

### US Data
- 2026-04-30: GDP advance Q1 (8:30am ET, BEA)
- 2026-05-02: NFP April (8:30am ET, BLS)
- 2026-05-13: CPI April (8:30am ET, BLS)

### Earnings (tracked tickers only)
- 2026-04-30 AMC: AAPL Q2 (consensus EPS $1.57)
- 2026-05-01 BMO: AMZN Q1 (consensus EPS $0.99)
```

### Step 4 — Cite source + confidence

Every event ships with:
- The source URL (preferring official: federalreserve.gov, bls.gov, bea.gov)
- A "confidence" tag: `[official]` for sources directly from the agency, `[aggregator]` for investing.com / forexfactory etc.

---

## Output Conventions

For a single event lookup:
```
**AAPL Q2 earnings** — 2026-04-30 AMC (after market close) · consensus EPS $1.57 [aggregator: nasdaq.com]
```

For a weekly briefing block:
```
**This week (2026-04-28 to 2026-05-02)**
- Tue 4/29 — JOLTS (10am, low impact)
- Wed 4/30 — **FOMC decision + presser** (2pm/2:30pm, very high impact)
- Wed 4/30 — GDP Q1 advance (8:30am, high impact)
- Wed 4/30 AMC — **AAPL Q2** (very high impact)
- Fri 5/2 — **NFP April** (8:30am, very high impact)
```

---

## What NOT to Do

- Do not invent dates when web-research returns ambiguous results — say "I couldn't pin down the exact date, here's the source page to check"
- Do not present aggregator data (investing.com etc.) as authoritative when the user is making a decision — escalate to the official agency source
- Do not over-cache — events get rescheduled. Re-verify FOMC and NFP dates within 7 days of the event
- Do not flag everything as "high impact" — distinguish **very high** (FOMC, NFP, CPI), **high** (GDP, retail sales, ISM, mega-cap earnings), **medium** (sector earnings, Fed speakers), **low** (weekly claims, regional Fed indices)

---

## v2 Roadmap

When clawith builds a dedicated finance-calendar MCP server, this skill will switch to direct API calls:

```
get_earnings_calendar(start="2026-04-28", end="2026-05-02")
get_macro_calendar(start="2026-04-28", end="2026-05-02", min_impact="high")
get_econ_event_consensus(event_id="us-cpi-2026-05")
```

Until then, structured web search is the contract.
""",
            },
        ],
    },
    {
        "name": "Full-Stack App Deploy (Vercel + Neon)",
        "description": "Guides the agent through the planning, development, and deployment of a full-stack application (frontend, API routes, database) to Vercel and Neon. Recommend reading this skill at the project's inception to configure tokens, choose frameworks, and design the database architecture upfront, avoiding late-stage deployment surprises.",
        "category": "deploy",
        "icon": "🚀",
        "folder_name": "vercel-full-stack-deploy",
        "is_default": True,
        "files": [
            {
                "path": "SKILL.md",
                "content": """---
name: Full-Stack App Deploy (Vercel + Neon)
description: Guides the agent through the planning, development, and deployment of a full-stack application to Vercel and Neon, ensuring configuration, credentials, and architecture decisions are addressed early.
---

# Full-Stack App Deploy (Vercel + Neon)

## When to Use
Use this skill when the user requests a "website", "web app", or "online system" (product) that requires a database.
If the user only requests static frontend pages without a database or backend APIs, use the existing `publish_page` tool directly.

> [!IMPORTANT]
> **Code Development and Editing Priority:**
> Code development and editing MUST be prioritized inside the local workspace (`workspace`). First develop and edit your changes in the workspace. If the `execute_code` tool is enabled, you can run `npm run build` inside the workspace using bash to verify compilation locally. Otherwise, directly call the `vercel_deploy` tool to deploy the workspace to Vercel (using the default Direct Upload method); if the build fails, use the `vercel_get_deploy_logs` tool to retrieve build logs and fix any errors. Do not write code in remote environments or rely on external triggers.

---

## Step 0: Guide the User to Enable Tools and Configure Tokens

> 🔔 All Vercel/Neon deployment-related tools are disabled by default and must be enabled manually by the user.

**The Agent should proactively check and guide the user through the following actions:**

### 0.1 Check if Vercel Tools are Enabled
- Verify if the Vercel tools under the "deploy" category in the tool list are enabled.
- If not enabled, inform the user:
  "To develop and deploy full-stack applications, you need to enable the Vercel-related tools in the 'Tool Management' page under the 'Deploy' category: Deploy to Vercel, List Vercel Deployments, Get Deploy Logs, Set Environment Variable, and Create Postgres Database. You can also enable Manage Domain if you want to use custom domains."

### 0.2 Guide the User to Sign Up for Vercel and Get a Token
- If Vercel tools are enabled but the `vercel_token` is missing or empty, guide the user:
  1. Visit https://vercel.com/signup to register (supports GitHub / Email sign up).
  2. Once logged in, go to https://vercel.com/account/tokens.
  3. Click "Create" to generate a new token (suggested name: "clawith", Scope: "Full Account").
  4. Copy the generated token, return to the Clawith tool settings page, and paste it into the "Vercel Access Token" configuration field for "Deploy to Vercel" or any other Vercel tools.

### 0.3 Guide the User to Sign Up for Neon and Get an API Key
- If the project requires a database (Postgres), guide the user:
  1. Visit https://neon.tech to register (recommending GitHub OAuth for instant registration).
  2. Once registered, go to the API Keys section in the console settings (https://console.neon.tech/app/settings/api-keys).
  3. Click "Create new API Key", name it (e.g., "clawith"), and copy the generated key.
  4. Return to the Clawith tool settings page, find the `Create Postgres Database` tool, and paste the key into the "Neon API Key" configuration field.

---

## Step 1: Choose Framework and Initialize

### 1.1 Confirm Development Framework
Confirm the framework to be used with the user:
- **Proactively Recommend Next.js**: Explain to the user: "Next.js is the official native framework for Vercel, offering the best integration, zero-config serverless deployments, API routes, and seamless database connections."
- **Default Framework**: If the user has no explicit preference, default to using **Next.js** to initialize the project.
- **Other Options**: If the user explicitly asks for a single-page app (SPA) or lighter alternatives, Vite/Astro can be used, but warn them about independent API hosting limitations.

---

## Step 2: Full-Stack Development and Debugging

### 2.1 Initialize Boilerplate
- Initialize the project using Next.js (prefer non-interactive setup: `npx create-next-app@latest ./ --typescript --eslint --tailwind --src-dir --app --import-alias "@/*"` or modify based on project directory).
- Write backend APIs under `src/app/api/`.

### 2.2 Optimized Deployment & Database Association Sequence (Crucial)
To avoid unnecessary deployments, save Vercel build limits, and prevent serving a broken state without database configuration, strictly follow this sequence:
1. **Create the Database first**: Call the `neon_create_database` tool to obtain the `DATABASE_URL`.
   - **Important**: If the tool returns a "Neon free limit reached" warning, notify the user and guide them to delete old projects or supply an existing database connection string.
2. **Configure Vercel Environment Variables**: Call the `vercel_set_env` tool to inject the `DATABASE_URL` into Vercel.
   - Key: `DATABASE_URL`
   - Value: `<The connection string obtained>`
3. **Deploy the application**: Once the environment variables are successfully configured in Vercel, call the `vercel_deploy` tool to deploy.
   - **Note on Deployment Security**: The deploy tool automatically sends a request to disable Vercel's Deployment Protection (SSO/password protection) on project creation and deployment. This is done to enable full-auto debugging, screenshot verification, and crawling of preview URLs by the AI Agent.

### 2.3 Development, Testing, and Debugging
- **Local Verification (Optional)**: If the `execute_code` tool is enabled, run `npm run build` inside the workspace using the `execute_code` tool (with `bash` language) to ensure there are no compilation or TypeScript errors before deploying. Otherwise, skip local verification and deploy directly.
- **Preview Deployment**: Call `vercel_deploy` (specifying `production=False`) to get a unique Preview URL.
- **Automated Verification**: Use the Browser tool to navigate to the Preview URL, take screenshots, and verify the UI rendering and API operations.
- **Build and Log Debugging**: If the build fails, call `vercel_get_deploy_logs` to view compilation or runtime logs to diagnose and fix errors.
- **Production Deployment**: Once testing is successful, call `vercel_deploy` (specifying `production=True`) to publish to production.

---

## Debugging and Limit Status Monitoring
- **Build Failures** → Use `vercel_get_deploy_logs` to check build logs.
- **Runtime Errors** → Use `vercel_get_deploy_logs` to check runtime logs.
- **Limit Monitoring** → Whenever a deployment completes, check the build logs/Vercel status, and proactively display the Vercel bandwidth/build usage percentage and Neon project limit status (e.g. 1/1 projects). If usage exceeds 80%, highlight it in bold to warn the user.
- **Visual Checks** → Use the Browser tool to screenshot and verify layouts.
"""
            }
        ]
    }
]


async def seed_skills():
    """Insert builtin skills if they don't exist."""
    from app.services.skill_creator_content import get_skill_creator_files
    from app.services.seeder_state import is_seeder_done, mark_seeder_done
    from pathlib import Path as _Path

    if await is_seeder_done("seeder:skills", 6):
        logger.info("[SkillSeeder] Already seeded (seeder:skills v6), skipping")
        return

    _files_dir = _Path(__file__).parent / "skill_creator_files"
    _template_skills_dir = _Path(__file__).parent.parent.parent / "agent_template" / "skills"

    # Populate skill-creator files at runtime
    for s in BUILTIN_SKILLS:
        if s["folder_name"] == "skill-creator" and not s["files"]:
            s["files"] = get_skill_creator_files()
        elif s["folder_name"] == "content-research-writer" and not s["files"]:
            # Load from downloaded file
            crw_file = _files_dir / "content_research_writer__SKILL.md"
            if crw_file.exists():
                s["files"] = [{"path": "SKILL.md", "content": crw_file.read_text(encoding="utf-8")}]
        elif s["folder_name"] == "mcp-installer" and not s["files"]:
            mcp_file = _template_skills_dir / "mcp-installer" / "SKILL.md"
            if mcp_file.exists():
                s["files"] = [{"path": "SKILL.md", "content": mcp_file.read_text(encoding="utf-8")}]
            else:
                logger.warning("[SkillSeeder] mcp-installer/SKILL.md not found in agent_template/skills/")

    async with async_session() as db:
        for skill_data in BUILTIN_SKILLS:
            result = await db.execute(
                select(Skill).where(Skill.folder_name == skill_data["folder_name"])
            )
            existing = result.scalar_one_or_none()
            is_default = skill_data.get("is_default", False)
            if existing:
                # Update metadata
                existing.name = skill_data["name"]
                existing.description = skill_data["description"]
                existing.category = skill_data["category"]
                existing.icon = skill_data["icon"]
                existing.is_default = is_default
                # Sync files — add missing ones
                from sqlalchemy.orm import selectinload
                res2 = await db.execute(
                    select(Skill).where(Skill.id == existing.id).options(selectinload(Skill.files))
                )
                sk = res2.scalar_one()
                existing_paths = {f.path: f for f in sk.files}
                for f in skill_data["files"]:
                    if f["path"] in existing_paths:
                        # Update content if changed
                        existing_file = existing_paths[f["path"]]
                        if existing_file.content != f["content"]:
                            existing_file.content = f["content"]
                            logger.info(f"[SkillSeeder] Updated {f['path']} in {skill_data['name']}")
                    else:
                        db.add(SkillFile(skill_id=existing.id, path=f["path"], content=f["content"]))
                        logger.info(f"[SkillSeeder] Added file {f['path']} to {skill_data['name']}")
            else:
                skill = Skill(
                    name=skill_data["name"],
                    description=skill_data["description"],
                    category=skill_data["category"],
                    icon=skill_data["icon"],
                    folder_name=skill_data["folder_name"],
                    is_builtin=True,
                    is_default=is_default,
                )
                db.add(skill)
                await db.flush()
                for f in skill_data["files"]:
                    db.add(SkillFile(skill_id=skill.id, path=f["path"], content=f["content"]))
                logger.info(f"[SkillSeeder] Created skill: {skill_data['name']}")
        await db.commit()
        logger.info("[SkillSeeder] Skills seeded")

    await mark_seeder_done("seeder:skills", 6, {"count": len(BUILTIN_SKILLS)})


async def push_default_skills_to_existing_agents():
    """Deploy all is_default skills into the workspace of every existing agent that is missing them.
    
    Called at startup after seed_skills() so existing agents automatically receive new default skills
    like mcp-installer without requiring manual re-creation.
    """
    from app.models.agent import Agent
    from app.models.skill import Skill
    from app.models.system_settings import SystemSetting
    from sqlalchemy.orm import selectinload
    from app.services.storage.factory import get_storage
    from app.services.seeder_state import is_seeder_done, get_seeder_state, mark_seeder_done

    storage = get_storage()
    current_backend = storage.backend_name

    state = await get_seeder_state("seeder:skills-push")
    if state and state.get("version", 0) >= 1:
        stored_backend = state.get("backend", "")
        if stored_backend == current_backend:
            logger.info(f"[SkillSeeder] Skills push already done for backend '{current_backend}', skipping")
            return

    async with async_session() as db:
        # Load all is_default skills with their files
        default_skills_r = await db.execute(
            select(Skill).where(Skill.is_default == True).options(selectinload(Skill.files))
        )
        default_skills = default_skills_r.scalars().all()
        if not default_skills:
            return

        # Compute a hash of default skill folder names to detect newly added skills
        hasher = hashlib.sha256()
        for skill in sorted(default_skills, key=lambda s: s.folder_name):
            hasher.update(skill.folder_name.encode("utf-8"))
        current_hash = hasher.hexdigest()

        # Check if we already synced this version of default skills
        setting_r = await db.execute(
            select(SystemSetting).where(SystemSetting.key == "default_skills_sync_hash")
        )
        setting = setting_r.scalar_one_or_none()
        if setting and setting.value.get("hash") == current_hash:
            logger.info(f"[SkillSeeder] Default skills sync hash '{current_hash}' matches, skipping sync for existing agents")
            return

        # Load all agents
        agents_r = await db.execute(select(Agent))
        agents = agents_r.scalars().all()

        pushed = 0
        removed_legacy = 0
        storage = get_storage_backend()
        for agent in agents:
            skills_prefix = f"{agent.id}/skills/"
            for skill in default_skills:
                if not skill.files:
                    continue
                for sf in skill.files:
                    file_key = f"{skills_prefix}{skill.folder_name}/{sf.path}"
                    if await storage.exists(file_key):
                        existing_content = await storage.read(file_key)
                        if existing_content == sf.content:
                            continue  # already up-to-date
                        await storage.write(file_key, sf.content)
                        updated += 1
                    else:
                        await storage.write(file_key, sf.content)
                        pushed += 1
                        logger.info(f"[SkillSeeder] Pushed '{skill.name}' to agent {agent.id}")

                # Determine if the agent already has this skill by checking if its first file exists in storage
                first_file_key = f"{agent_prefix}/skills/{skill.folder_name}/{skill.files[0].path}"
                if await storage.is_file(first_file_key):
                    continue  # Skill already exists, do not update

                for sf in skill.files:
                    key = f"{agent_prefix}/skills/{skill.folder_name}/{sf.path}"
                    await storage.write_text(key, sf.content, encoding="utf-8")
                    pushed += 1
                logger.info(f"[SkillSeeder] Pushed new default skill '{skill.name}' to agent {agent.id}")

        # Save/update the sync hash in settings
        if setting:
            setting.value = {"hash": current_hash}
        else:
            logger.info("[SkillSeeder] All existing agents already have up-to-date default skills")

    await mark_seeder_done("seeder:skills-push", 1, {"backend": current_backend})
