# Stacy HR Bot — Project Context for Claude

## What This Project Is

Stacy is a Discord bot that acts as a satirical-but-functional HR department for Discord servers. It passively monitors chat, logs policy violations as "infractions" to a MySQL database, tracks a per-user "social credit score", assigns Discord roles based on that score, and generates styled HTML compliance reports on demand. The tone is corporate-satirical — Stacy is strict, slightly sassy, and treats Discord drama like a workplace incident.

The project runs entirely locally (Python process + MySQL), tunneled to the internet via ngrok for report serving.

---

## Repository Layout

```
Stacy/
├── src/
│   ├── bot.py                  # Discord bot entrypoint, all event handlers and commands
│   ├── stacy_graph.py          # LangGraph agent definition (nodes, edges, router)
│   ├── stacy_graph_tools.py    # LangGraph state type + tool definitions (DB write tools)
│   ├── database_functions.py   # All MySQL queries — single source of truth for DB access
│   ├── social_credit.py        # Role assignment logic, sync_all_roles, score→role mapping
│   ├── report.py               # HTML report generator (no DB access — takes raw data)
│   └── temp/                   # Generated HTML report files (gitignored)
├── stacy_hr_db.sql             # Full MySQL schema (source of truth for DB structure)
├── stacy_hr_view.sql           # Optional view definitions
├── requirements.txt
├── .env                        # Not committed — see env vars section below
└── README.md
```

---

## Architecture

### Request Flow — Passive Moderation

```
Discord message
      │
      ▼
on_message (bot.py)
      │
      ├─ Is it a bot? → return (ignore)
      ├─ process_commands() (handles !commands first)
      ├─ upsert_user() (register sender in DB)
      │
      ▼
run_stacy() → asyncio.to_thread → LangGraph app.stream()
      │
      ▼
stacy_router (LLM call #1)
      │
      ├─ "ignore"          → silent_ignore_node → no Discord reply
      ├─ "hr_question"     → hr_node (LLM call #2) → reply to message
      └─ "report_violation"→ report_node (LLM call #2) → apply_infraction_node (LLM call #3)
                                                              │
                                                              ├─ DB write (warn/minor/severe tool)
                                                              └─ reply to message
```

After any infraction, `bot.py` immediately calls `setup_and_assign_hr_role()` to update the offending user's Discord role.

### Request Flow — @Mention with Target User

When a message contains `@Stacy @someUser reason`, `bot.py` extracts the target user as `target_user_id` and passes it into the LangGraph state. The infraction is recorded against the *target*, not the reporter.

### Cron Jobs (APScheduler)

Two jobs run on the Discord event loop:
1. **Role sync** — every 5 minutes, calls `sync_all_roles()` for every guild the bot is in
2. **Score decay** — *(planned)* daily at midnight, applies a 5% decay to all scores

---

## Key Files — What Each One Does

### `bot.py`
The main entrypoint. Owns:
- FastAPI server (background thread) + ngrok tunnel for report URLs
- All Discord event handlers (`on_ready`, `on_message`)
- All `!commands` (`!ping`, `!hello`, `!rules`, `!helpme`, `!History`)
- The APScheduler setup
- `run_stacy()` helper that wraps LangGraph in `asyncio.to_thread` (LangGraph is sync)

The HR policy text is currently **hardcoded** in `on_message` as a string passed to `initialize_guild()`. This is a known issue — it should be pulled from the DB via `lookup_hr_policy`.

### `stacy_graph.py`
Defines the LangGraph `StateGraph` and compiles it to `app`. Four nodes:
- `stacy_router` — classifies the message as `ignore` / `hr_question` / `report_violation`
- `hr_node` — answers HR policy questions using the `lookup_hr_policy` tool
- `report_node` — asks the LLM to output `{"severity": "...", "points": N}` JSON
- `apply_infraction_node` — dispatches to the correct DB tool, generates Stacy's reply
- `silent_ignore_node` — returns a sentinel string so `bot.py` knows to stay quiet

### `stacy_graph_tools.py`
Defines `StacyState` (the TypedDict that flows through the graph) and four `@tool` functions:
- `lookup_hr_policy` — **currently returns hardcoded text** — should query `guilds` table
- `warn_user` — writes a Low severity, 0-point record to `infractions`
- `upload_minor_infraction` — writes Medium severity to `infractions`
- `upload_severe_infraction` — writes Critical severity to `infractions`

Note: `determine_severity` exists in this file but is **not used** — severity is determined inline in `report_node` via JSON parsing.

### `database_functions.py`
All MySQL operations. Key functions:
- `initialize_guild(guild_id, name, policy)` — upsert into `guilds`
- `upsert_user(user_id, guild_id, username)` — safe insert-or-ignore for user records
- `log_stacy_inference(...)` — writes an infraction row and increments the user's score
- `get_hr_report_data(user_id, guild_id)` — LEFT JOIN query for report generation
- `get_user_score(user_id, guild_id)` — returns current score integer

DB credentials are hardcoded at the top of this file. If you change them, update `db_config`.

### `social_credit.py`
Role management. Key functions:
- `get_role_for_score(score)` — maps score integer to role name string
- `sync_all_roles(guild)` — iterates all non-bot members, upserts them, assigns correct role
- `setup_and_assign_hr_role(guild, member, score)` — single-member assignment used after an infraction

Role thresholds live in `ROLE_THRESHOLDS` at the top of this file. Change them here if you want different cutoffs.

### `report.py`
Pure HTML generation — takes data from `get_hr_report_data()` and builds a self-contained HTML file with Chart.js. No DB access of its own. Saved to `src/temp/`. Served by the FastAPI endpoint in `bot.py`.

---

## Database Schema

```sql
CREATE TABLE guilds (
    guild_id        VARCHAR(20) PRIMARY KEY,
    guild_name      VARCHAR(100),
    hr_policy_text  TEXT
);

CREATE TABLE users (
    user_id             VARCHAR(20),
    guild_id            VARCHAR(20),
    username            VARCHAR(100),
    social_credit_score INT DEFAULT 0,
    current_status_role VARCHAR(50) DEFAULT 'HR Approved',
    PRIMARY KEY (user_id, guild_id),
    FOREIGN KEY (guild_id) REFERENCES guilds(guild_id)
);

CREATE TABLE infractions (
    infraction_id       INT AUTO_INCREMENT PRIMARY KEY,
    guild_id            VARCHAR(20),
    user_id             VARCHAR(20),
    violation_context   TEXT,
    stacy_inference     TEXT,
    severity_level      ENUM('Low','Medium','Critical'),
    score_penalty       INT DEFAULT 0,
    timestamp           DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (guild_id, user_id) REFERENCES users(guild_id, user_id)
);
```

---

## Environment Variables (`.env`)

```env
DISCORD_TOKEN=        # Discord bot token
OPENAI_API_KEY=       # OpenAI API key (used by stacy_graph.py)
NGROK_AUTHTOKEN=      # ngrok auth token (free tier works)
GUILD_ID=             # Used only by social_credit.py __main__ block
USER_ID=              # Used only by social_credit.py __main__ block
```

---

## LLM Usage

The project uses **OpenAI `gpt-4o-mini`** via `langchain-openai`. This replaced an earlier Ollama/Qwen3 setup. Do not reintroduce Ollama — the OpenAI API is the intended runtime.

Every non-bot Discord message that reaches passive moderation makes **1 LLM call** (the router). If the router returns `report_violation`, it makes **2 more calls** (report_node + apply_infraction_node). HR questions make **1 more call** (hr_node).

This means normal chat costs ~1 API call per message. High-traffic servers should use the planned rolling window feature to batch messages and reduce this to ~1 call per N messages.

---

## Planned Features (see README.md for full specs)

### 1. Custom HR Rules (`!SetPolicy`)
- `lookup_hr_policy` tool must query `guilds.hr_policy_text` by `guild_id`
- Need to pass `guild_id` into the tool from `hr_node`
- Need `!SetPolicy <text>` command (admin-only) that calls a new `update_guild_policy()` DB function
- The hardcoded policy string in `bot.py on_message` should become a fallback default only

### 2. Message Rolling Window
- Add `channel_buffers: dict[int, deque]` in `bot.py`
- Buffer N messages per channel; only invoke LangGraph when buffer is full OR message @mentions Stacy
- `stacy_router` needs a bulk-evaluation mode: receives multiple messages, returns list of findings
- Configurable buffer size (default 10)
- @mention always bypasses buffer and triggers immediate evaluation

### 3. Per-Guild Sensitivity
- Add `sensitivity ENUM('low','medium','high') DEFAULT 'low'` to `guilds` table
- Add `get_guild_sensitivity()` / `set_guild_sensitivity()` to `database_functions.py`
- Add `sensitivity` field to `StacyState` in `stacy_graph_tools.py`
- `stacy_router` selects from 3 different system prompts based on sensitivity value
- `!SetSensitivity low|medium|high` command (admin-only) in `bot.py`

### 4. Score Decay
- Add `decay_all_scores()` to `database_functions.py`: `UPDATE users SET social_credit_score = GREATEST(0, FLOOR(social_credit_score * 0.95))`
- Second `scheduler.add_job` in `on_ready` with `trigger='cron', hour=0`
- After decay, trigger a role sync so roles reflect the new scores immediately

### 5. !AskStacy / !TellStacy Commands
- `!AskStacy <question>` — bypasses router, calls `hr_node` directly
- `!TellStacy @user <reason>` — bypasses router, calls `report_node` → `apply_infraction_node` directly

---

## Known Issues / Gotchas

- **`lookup_hr_policy` is hardcoded** — returns a fake string, never queries the DB. The policy stored via `initialize_guild()` is never actually read by the LLM.
- **`determine_severity` tool is dead code** — defined in `stacy_graph_tools.py` but never called. Severity is determined inline in `report_node`.
- **`social_credit.py __main__` block** reads `GUILD_ID`/`USER_ID` from `.env` — these are only used for the standalone test runner, not the bot.
- **FastAPI + ngrok race condition** — if ngrok is slow to connect, `PUBLIC_URL` may not be set before the first report request. In practice this hasn't been an issue.
- **Score is "social debt"** — higher score is *worse*. Zero is a clean record. This is the opposite of a typical "credit score" but is intentional for the HR theme.
- **`image_b64` / `image_mime` are threaded through state** but the LLM calls in `stacy_graph.py` don't actually use them yet. Vision moderation is scaffolded but not wired.

---

## How to Run Locally

```bash
# 1. Start MySQL
# 2. Apply schema
mysql -u root -p < stacy_hr_db.sql
# 3. Activate venv
venv\Scripts\activate
# 4. Set .env
# 5. Run
cd src
python bot.py
```

The bot logs the ngrok report URL on startup. Keep that terminal open.
