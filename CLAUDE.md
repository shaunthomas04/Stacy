# Stacy HR Bot — Project Context for Claude

## What This Project Is

Stacy is a Discord bot that acts as a satirical-but-functional HR department for Discord servers. It passively monitors chat, logs policy violations as "infractions" to a MySQL database, tracks a per-user "social credit score", assigns Discord roles based on that score, handles appeals, escalates severe incidents to a forum channel, and generates styled HTML compliance reports on demand.

The project runs entirely locally (Python process + MySQL), tunneled to the internet via ngrok for report serving.

---

## Repository Layout

```
Stacy/
├── src/
│   ├── config.py                # All env vars + app constants — single source of truth for config
│   ├── bot.py                   # Slim entrypoint: discord.py client, event handlers, startup
│   ├── moderation.py             # Moderation pipeline: caches, run_stacy(), forum handling, decay job
│   ├── commands.py              # All !prefix commands
│   ├── report_server.py         # FastAPI report route + ngrok tunnel bootstrap
│   ├── stacy_graph.py           # LangGraph agent + standalone LLM helpers
│   ├── stacy_graph_tools.py     # LangGraph state type + DB write tools
│   ├── database_functions.py    # All MySQL queries — single source of truth for DB access
│   ├── social_credit.py         # Role assignment logic, sync_all_roles, score→role mapping
│   ├── report.py                # HTML report generator (no DB access)
│   └── temp/                    # Generated HTML report files (gitignored)
├── stacy_hr_db.sql              # Full MySQL schema (source of truth for DB structure)
├── Dockerfile                   # Bot image (python:3.11-slim)
├── docker-compose.yml           # mysql + bot services; schema auto-loads on first boot
├── requirements.txt
├── .env.example                 # Template for required/optional env vars
├── .env                         # Not committed
├── LICENSE                      # MIT
└── README.md
```

---

## Architecture

### Message Flow — Passive Moderation

```
Discord message
      │
      ▼
on_message (bot.py)
      │
      ├─ bot message? → return
      ├─ process_commands() — handles !commands
      ├─ valid command? → return (skip moderation)
      │
      ├─ policy-violations forum thread? → moderation.handle_forum_message() → return
      │
      ├─ @mention path: resolves target user, calls moderation.run_stacy() immediately
      │
      └─ passive path: calls moderation.run_stacy() on the current message
            │
            ▼
moderation.run_stacy() → asyncio.to_thread → LangGraph app.stream()
      │       returns (response_text, severity, points)
      ▼
stacy_router (LLM call #1, structured output → RouterDecision) — reads hr_policy + sensitivity from state
      │
      ├─ "ignore"           → silent_ignore_node → no reply
      ├─ "hr_question"      → hr_node (LLM call #2) → reply
      └─ "report_violation" → report_and_reply_node (LLM call #2, structured output → InfractionAssessment)
                                    │
                                    ├─ one call returns severity + points + Stacy's reply together
                                    ├─ DB write (warn / minor / severe tool) — no LLM call needed for this part
                                    └─ reply to message

moderation.deliver_result() sends the reply, and:
  - If severity == "severe": moderation.post_violation_thread() opens a forum thread
  - Always: moderation.refresh_role() re-fetches the target's score and updates their Discord role
```

### Forum Thread Flow

Messages inside a `policy-violations` forum thread are handled separately by `moderation.handle_forum_message()`. Stacy responds to every message when one person is talking, and backs off as more unique participants join (interval = `max(1, (unique_participants - 1) * 3)`). She responds immediately if directly mentioned. The thread buffer is pre-populated with the incident report so she always has context.

### Cron Job

One APScheduler job runs every 1 minute (registered in `bot.py`'s `on_ready`): `moderation.decay_and_sync_job()` calls `decay_scores_due()` which finds guilds where decay is due, applies `GREATEST(0, score - decay_amount)`, updates `last_decay_at`, and syncs Discord roles for any affected guild.

---

## Key Files

### `config.py`
Loads `.env` once and validates required vars (`DISCORD_TOKEN`, `OPENAI_API_KEY`, `NGROK_AUTHTOKEN`) at import time — raises immediately with a clear message if one is missing, instead of failing mysteriously later. Also holds `DB_*` vars (with local-dev defaults), `REPORT_PORT`, and app-wide constants (`DEFAULT_POLICY`, `VIOLATIONS_FORUM_NAME`). Every other module reads config from here rather than calling `os.getenv` directly.

### `bot.py`
Thin entrypoint only — no business logic:
- Creates the `discord.py` `Bot` instance and intents
- Event handlers: `on_ready` (starts the APScheduler decay job, warms caches, syncs roles), `on_message` (routes to forum handling / mention path / passive path via `moderation.py`), `on_member_join`
- `__main__` block: starts the report server, registers commands, calls `bot.run()`

### `moderation.py`
The moderation pipeline, decoupled from the bot instance (functions take `guild`/`bot.user` as arguments, not the bot itself — avoids an import cycle with `bot.py`):
- `run_stacy()` — wraps the LangGraph agent in `asyncio.to_thread`, returns `(response_text, severity, points)`
- `deliver_result()` — sends Stacy's reply, escalates to the forum if severe, refreshes the target's role — shared by both the @mention and passive `on_message` paths
- `handle_forum_message()` — manages per-thread buffers and Stacy's participation cadence
- `get_violations_forum()` / `post_violation_thread()` — forum channel management
- `_forum_interval()` — calculates response cadence based on unique participant count
- `refresh_role()` — re-fetches a user's score and syncs their Discord role; called after any score change
- In-memory caches: `_guild_cache` (policy), `_sensitivity_cache` (sensitivity), `thread_buffers`, `thread_message_counts`
- `ensure_guild()` — initialises guild in DB on first message, populates caches
- `decay_and_sync_job()` — the APScheduler job body

### `commands.py`
`register_commands(bot, public_url)` registers all `!` commands onto the bot: `ping`, `hello`, `policy`, `stacyHelp`, `appeal`, `setPolicy`, `setSensitivity`, `setDecayInterval`, `setDecayAmount`, `pardon`, `resolve`, `history`.

### `report_server.py`
FastAPI app serving `/report/{guild_id}/{user_id}`, plus `start_report_server()` which launches uvicorn in a background thread, opens the ngrok tunnel, and returns the public URL used by `!history`.

### `stacy_graph.py`
LangGraph `StateGraph` compiled to `app`, plus standalone LLM helpers. Router and infraction classification use `llm.with_structured_output()` (Pydantic schemas `RouterDecision` / `InfractionAssessment`) instead of parsing free text — reliable, no substring matching, no JSON-parse fallback needed for those two paths.
- `stacy_router` — classifies messages using `_ROUTER_PROMPTS[sensitivity]`, returns a `RouterDecision.action`
- `hr_node` — answers policy questions
- `report_and_reply_node` — **one** LLM call classifies severity/points AND writes Stacy's in-character reply (`InfractionAssessment`); the DB write itself is done in code afterward via the warn/minor/severe tools, no second LLM call. Replaced the old two-node `report_node` → `apply_infraction_node` pair.
- `silent_ignore_node` — sentinel for ignored messages
- `_safe_invoke(messages, fallback)` — wraps `llm.invoke()`, returns `fallback` text instead of raising on timeout/API error. Used by `hr_node`, `get_pardon_response`, `get_resolve_response`, `get_forum_response` so a failed OpenAI call never leaves the user with silence (or a DB write with no explanation, in the infraction path).
- `get_appeal_decision(username, context, severity, points, reason)` — reviews appeals, returns `{"decision", "points_removed", "message"}`
- `get_pardon_response(member_name)` — LLM pardon message
- `get_resolve_response(thread_name)` — LLM thread closing message
- `get_forum_response(thread_name, conversation, directly_addressed)` — contextual forum reply; empty string on failure so Stacy just stays quiet instead of erroring

### `stacy_graph_tools.py`
- `StacyState` TypedDict: `messages`, `user_id`, `guild_id`, `target_user_id`, `username`, `target_username`, `hr_policy`, `sensitivity`, `severity`, `points`, `participants`, `violator_name`
- `lookup_hr_policy` — queries `guilds.hr_policy_text`
- `warn_user`, `upload_minor_infraction`, `upload_severe_infraction` — DB write tools

### `database_functions.py`
All MySQL. `get_cursor()` is a context manager that owns the connection lifecycle (connect, commit-on-success, always close, log-and-swallow on error) — every query function is just a `with get_cursor() as cursor:` block, no repeated connect/close/try/except boilerplate. Reads DB credentials from `config.py`. Key functions:
- `initialize_guild` — upsert guilds; never overwrites `hr_policy_text` on duplicate
- `upsert_user` — insert-or-update username
- `log_stacy_inference` — writes infraction, increments score
- `decay_scores_due() -> list[str]` — applies decay to due guilds, returns affected guild_ids
- `get_guild_policy` / `update_guild_policy`
- `get_guild_sensitivity` / `set_guild_sensitivity`
- `set_decay_interval` / `set_decay_amount`
- `reset_user_score` — sets score to 0 (`!pardon`)
- `deduct_user_score` — subtracts points floored at 0 (`!appeal` dismissal)
- `get_latest_infraction` — most recent unappealed infraction for a user
- `mark_infraction_appealed` — prevents the same infraction being appealed twice
- `get_user_score`, `get_hr_report_data`

### `social_credit.py`
- `ROLE_THRESHOLDS` — edit here to change score cutoffs
- `sync_all_roles(guild)` — full role sync for all non-bot members
- `setup_and_assign_hr_role(guild, member, score)` — single-member assignment

### `report.py`
Pure HTML generation from `get_hr_report_data()`. Chart.js score graph + infraction table. Saved to `src/temp/`, served by FastAPI.

---

## Database Schema

```sql
guilds (
    guild_id               VARCHAR(255) PRIMARY KEY,
    guild_name             VARCHAR(255),
    hr_policy_text         TEXT,
    decay_interval_minutes INT DEFAULT 1440,
    decay_amount           INT DEFAULT 5,
    last_decay_at          DATETIME DEFAULT NULL,
    sensitivity            ENUM('low','medium','high') DEFAULT 'low'
)

users (
    user_id              VARCHAR(255),
    guild_id             VARCHAR(255),
    username             VARCHAR(255),
    social_credit_score  INT DEFAULT 0,
    current_status_role  VARCHAR(100) DEFAULT 'HR Approved',
    PRIMARY KEY (user_id, guild_id)
)

infractions (
    infraction_id     INT AUTO_INCREMENT PRIMARY KEY,
    guild_id          VARCHAR(255),
    user_id           VARCHAR(255),
    violation_context TEXT,
    stacy_inference   TEXT,
    severity_level    ENUM('Low','Medium','Critical'),
    score_penalty     INT,
    appealed          BOOLEAN DEFAULT FALSE,
    timestamp         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
```

---

## Environment Variables (`.env`)

See `.env.example` for the authoritative list. Loaded and validated once by `config.py`.

```env
# Required
DISCORD_TOKEN=        # Discord bot token
OPENAI_API_KEY=       # OpenAI API key
NGROK_AUTHTOKEN=      # ngrok auth token (free tier works)

# Set if your local MySQL root user has a password, or for a non-default
# Docker MySQL root password. Blank works for a fresh local install.
DB_PASSWORD=

# Optional overrides — all have working defaults in config.py.
# DB_HOST=localhost
# DB_PORT=3306
# DB_USER=root
# DB_NAME=stacy_hr_db
# REPORT_PORT=5000
```

---

## LLM Usage

Uses **OpenAI `gpt-4o-mini`** via `langchain-openai`. Do not reintroduce Ollama.

- Passive message: 1 LLM call (router)
- Violation: 1 more call (report_and_reply_node — classification + reply combined)
- HR question: 1 more call (hr_node)
- Forum message: 1 call (get_forum_response)
- Appeal: 1 call (get_appeal_decision)
- Pardon / resolve: 1 call each

So a flagged message is 2 total LLM calls (router + report_and_reply), not 3 — the old `report_node`/`apply_infraction_node` split made two sequential calls where only one was actually needed, since the second call was purely for reply text.

---

## Stacy's Personality

Stacy sounds like a sincere, slightly anxious HR representative who takes the rules very seriously. Prompts describe personality traits — not example phrases — so responses vary naturally. Key traits:
- Hedges and qualifies naturally ("just", "technically", "from a consistency standpoint")
- Trails off with "…" or adds asides with dashes
- Apologetic about having to log things but does it anyway
- Not punitive or robotic — genuinely believes consistency makes things smoother
- In forum threads: more direct and engaged, reacts to what people actually say

No emojis in any LLM output. All prompts explicitly say "Do not use any emojis."

---

## Known Gotchas

- **Score is "social debt"** — higher is worse. Zero is a clean record. Intentional.
- **FastAPI + ngrok** — `report_server.start_report_server()` blocks on `ngrok.connect()` at startup and returns the public URL, which is then passed into `commands.register_commands()`. If ngrok is slow, the first report request may 404. Hasn't been an issue in practice.
- **Forum thread buffers are in-memory** — if the bot restarts, `thread_buffers` and `thread_message_counts` reset. Stacy loses conversation context for open threads but continues to function.
- **`!resolve` deletes threads** — there is no undo. The infraction record remains in the DB.
- **Docker schema init is first-boot-only** — `stacy_hr_db.sql` is mounted into `/docker-entrypoint-initdb.d/` and only runs when the MySQL container's data volume is empty. Editing the schema after that requires either `docker compose down -v` (wipes dev data) or a manual `ALTER TABLE`. DB data itself persists across `docker compose down`/`up`/rebuilds via the named `mysql_data` volume — only `-v` wipes it.
