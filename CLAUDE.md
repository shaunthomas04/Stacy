# Stacy HR Bot — Project Context for Claude

## What This Project Is

Stacy is a Discord bot that acts as a satirical-but-functional HR department for Discord servers. It passively monitors chat using a sliding context window, logs policy violations as "infractions" to a MySQL database, tracks a per-user "social credit score", assigns Discord roles based on that score, and generates styled HTML compliance reports on demand. The tone is corporate-satirical — Stacy sounds like a sincere, slightly anxious HR rep who takes the rules very seriously and occasionally misreads the room.

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
      ├─ @mention path: runs immediately with target user resolution
      │
      └─ passive path: append to channel deque (maxlen=10)
            │
            ▼
         build participants map + conversation string from window
            │
            ▼
run_stacy() → asyncio.to_thread → LangGraph app.stream()
      │
      ▼
stacy_router (LLM call #1) — reads hr_policy + sensitivity from state
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

### Cron Job (APScheduler)

One job runs on the Discord event loop:
- **Decay + role sync** — every 1 minute, calls `decay_scores_due()` which finds guilds where decay is due (based on their `decay_interval_minutes`), applies `GREATEST(0, score - decay_amount)`, then syncs roles for any guild whose scores changed.

---

## Key Files — What Each One Does

### `bot.py`
The main entrypoint. Owns:
- FastAPI server (background thread) + ngrok tunnel for report URLs
- All Discord event handlers (`on_ready`, `on_message`, `on_member_join`)
- All `!commands`: `ping`, `hello`, `policy`, `stacyHelp`, `setPolicy`, `setSensitivity`, `setDecayInterval`, `setDecayAmount`, `pardon`, `history`
- The APScheduler setup (`decay_and_sync_job` every 1 minute)
- `run_stacy()` helper that wraps LangGraph in `asyncio.to_thread` (LangGraph is sync)
- In-memory caches: `_guild_cache` (policy strings), `_sensitivity_cache` (sensitivity strings)
- `_ensure_guild(guild_id, guild_name)` — initialises guild in DB on first message, populates both caches

### `stacy_graph.py`
Defines the LangGraph `StateGraph` and compiles it to `app`. Nodes:
- `stacy_router` — classifies the message as `ignore` / `hr_question` / `report_violation`. Uses `_ROUTER_PROMPTS` dict to select a system prompt based on `state["sensitivity"]` (low / medium / high).
- `hr_node` — answers HR policy questions. Earnest, slightly over-literal tone, hedges frequently.
- `report_node` — asks the LLM to output `{"severity": "...", "points": N}` JSON. Batch mode adds `"violator"` field.
- `apply_infraction_node` — dispatches to correct DB tool, generates Stacy's reply. Three severity-specific prompts: warning (just flagging it), minor (apologetic but logged), severe (clear and direct but uncomfortable).
- `silent_ignore_node` — returns sentinel string so `bot.py` knows to stay quiet.

### `stacy_graph_tools.py`
Defines `StacyState` (TypedDict) and DB write tools:
- `StacyState` fields: `messages`, `user_id`, `guild_id`, `target_user_id`, `username`, `target_username`, `hr_policy`, `sensitivity`, `severity`, `points`, `participants`, `violator_name`
- `lookup_hr_policy(guild_id, user_question)` — queries `guilds.hr_policy_text`
- `warn_user` — Low severity, 0-point infraction
- `upload_minor_infraction` — Medium severity infraction
- `upload_severe_infraction` — Critical severity infraction

### `database_functions.py`
All MySQL operations. Key functions:
- `initialize_guild(guild_id, name, policy)` — upsert into `guilds`; ON DUPLICATE KEY only updates `guild_name`, never overwrites `hr_policy_text`
- `upsert_user(user_id, guild_id, username)` — insert-or-update username
- `log_stacy_inference(...)` — writes infraction row and increments score
- `decay_scores_due() -> list[str]` — finds guilds due for decay, applies `GREATEST(0, score - decay_amount)`, updates `last_decay_at`, returns affected guild_ids
- `get_guild_policy(guild_id)` / `update_guild_policy(guild_id, text)`
- `get_guild_sensitivity(guild_id)` / `set_guild_sensitivity(guild_id, level)`
- `set_decay_interval(guild_id, minutes)` / `set_decay_amount(guild_id, amount)`
- `reset_user_score(user_id, guild_id)` — sets score to 0 (used by `!pardon`)
- `get_hr_report_data(user_id, guild_id)` — LEFT JOIN for report generation
- `get_user_score(user_id, guild_id)` — returns current score integer

DB credentials are hardcoded at the top in `db_config`. Update there if credentials change.

### `social_credit.py`
Role management. Key functions:
- `get_role_for_score(score)` — maps score integer to role name string
- `sync_all_roles(guild)` — iterates all non-bot members, upserts them, assigns correct role
- `setup_and_assign_hr_role(guild, member, score)` — single-member assignment used after an infraction or pardon

Role thresholds live in `ROLE_THRESHOLDS` at the top of this file.

### `report.py`
Pure HTML generation — takes data from `get_hr_report_data()` and builds a self-contained HTML file with Chart.js. No DB access of its own. Saved to `src/temp/`. Served by the FastAPI endpoint in `bot.py`.

---

## Database Schema

```sql
CREATE TABLE guilds (
    guild_id               VARCHAR(255) PRIMARY KEY,
    guild_name             VARCHAR(255),
    hr_policy_text         TEXT,
    decay_interval_minutes INT DEFAULT 1440,
    decay_amount           INT DEFAULT 5,
    last_decay_at          DATETIME DEFAULT NULL,
    sensitivity            ENUM('low','medium','high') DEFAULT 'low',
    created_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE users (
    user_id             VARCHAR(255),
    guild_id            VARCHAR(255),
    username            VARCHAR(255),
    social_credit_score INT DEFAULT 0,
    current_status_role VARCHAR(100) DEFAULT 'HR Approved',
    PRIMARY KEY (user_id, guild_id),
    FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
);

CREATE TABLE infractions (
    infraction_id   INT AUTO_INCREMENT PRIMARY KEY,
    guild_id        VARCHAR(255),
    user_id         VARCHAR(255),
    violation_context TEXT,
    stacy_inference   TEXT,
    severity_level    ENUM('Low','Medium','Critical'),
    score_penalty     INT,
    timestamp         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (guild_id) REFERENCES guilds(guild_id),
    FOREIGN KEY (user_id, guild_id) REFERENCES users(user_id, guild_id)
);
```

---

## Environment Variables (`.env`)

```env
DISCORD_TOKEN=        # Discord bot token
OPENAI_API_KEY=       # OpenAI API key (used by stacy_graph.py)
NGROK_AUTHTOKEN=      # ngrok auth token (free tier works)
```

`GUILD_ID` and `USER_ID` were previously used only by `social_credit.py __main__` test block — no longer required.

---

## LLM Usage

Uses **OpenAI `gpt-4o-mini`** via `langchain-openai`. Do not reintroduce Ollama — the OpenAI API is the intended runtime.

Every passive message makes **1 LLM call** (the router). `report_violation` path makes **2 more** (report_node + apply_infraction_node). HR questions make **1 more** (hr_node). Cost is ~1 API call per message for normal chat.

---

## Stacy's Personality

Stacy sounds like a sincere, slightly anxious HR representative who takes the rules very seriously. She:
- Hedges frequently: "just", "technically", "from a consistency standpoint", "I don't want anyone getting mixed signals"
- Trails off with "…" or adds asides with dashes
- Is apologetic about having to log things but does it anyway
- Occasionally misreads the room and self-corrects
- Is not punitive or robotic — she genuinely believes consistency makes things smoother for everyone

No emojis in any LLM-generated output. Prompts explicitly say "Do not use any emojis."

---

## Planned Features

- **Forum thread escalation** — on Critical infractions, open a Discord thread in a designated HR channel. Needs `!setHrChannel` command, a DB column for `hr_channel_id`, and thread-creation logic in `bot.py` after `upload_severe_infraction` fires.
- **`!standings`** — leaderboard of top offenders in the server
- **`!myRecord`** — let any user check their own current score and role tier

---

## Known Issues / Gotchas

- **Score is "social debt"** — higher score is *worse*. Zero is a clean record. Intentional for the HR theme.
- **FastAPI + ngrok race condition** — if ngrok is slow to connect, `PUBLIC_URL` may not be set before the first report request. In practice this hasn't been an issue.
- **`social_credit.py __main__` block** — standalone test runner, not used by the bot.

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
