# Stacy HR Bot — Project Context for Claude

## What This Project Is

Stacy is a Discord bot that acts as a satirical-but-functional HR department for Discord servers. It passively monitors chat, logs policy violations as "infractions" to a MySQL database, tracks a per-user "social credit score", assigns Discord roles based on that score, handles appeals, escalates severe incidents to a forum channel, and generates styled HTML compliance reports on demand.

The project runs entirely locally (Python process + MySQL), tunneled to the internet via ngrok for report serving.

---

## Repository Layout

```
Stacy/
├── src/
│   ├── bot.py                  # Discord bot entrypoint, all event handlers and commands
│   ├── stacy_graph.py          # LangGraph agent + standalone LLM helpers
│   ├── stacy_graph_tools.py    # LangGraph state type + DB write tools
│   ├── database_functions.py   # All MySQL queries — single source of truth for DB access
│   ├── social_credit.py        # Role assignment logic, sync_all_roles, score→role mapping
│   ├── report.py               # HTML report generator (no DB access)
│   └── temp/                   # Generated HTML report files (gitignored)
├── stacy_hr_db.sql             # Full MySQL schema (source of truth for DB structure)
├── requirements.txt
├── .env                        # Not committed
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
      ├─ policy-violations forum thread? → _handle_forum_message() → return
      │
      ├─ @mention path: resolves target user, calls run_stacy() immediately
      │
      └─ passive path: calls run_stacy() on the current message
            │
            ▼
run_stacy() → asyncio.to_thread → LangGraph app.stream()
      │       returns (response_text, severity, points)
      ▼
stacy_router (LLM call #1) — reads hr_policy + sensitivity from state
      │
      ├─ "ignore"           → silent_ignore_node → no reply
      ├─ "hr_question"      → hr_node (LLM call #2) → reply
      └─ "report_violation" → report_node (LLM call #2)
                                    │
                                    ▼
                             apply_infraction_node (LLM call #3)
                                    │
                                    ├─ DB write (warn / minor / severe tool)
                                    └─ reply to message

If severity == "severe": _post_violation_thread() opens a forum thread
After any infraction: setup_and_assign_hr_role() updates the user's Discord role
```

### Forum Thread Flow

Messages inside a `policy-violations` forum thread are handled separately by `_handle_forum_message()`. Stacy responds to every message when one person is talking, and backs off as more unique participants join (interval = `max(1, (unique_participants - 1) * 3)`). She responds immediately if directly mentioned. The thread buffer is pre-populated with the incident report so she always has context.

### Cron Job

One APScheduler job runs every 1 minute: `decay_and_sync_job()` calls `decay_scores_due()` which finds guilds where decay is due, applies `GREATEST(0, score - decay_amount)`, updates `last_decay_at`, and syncs Discord roles for any affected guild.

---

## Key Files

### `bot.py`
- FastAPI + ngrok for report serving (background thread)
- All event handlers: `on_ready`, `on_message`, `on_member_join`
- All commands: `ping`, `hello`, `policy`, `stacyHelp`, `appeal`, `setPolicy`, `setSensitivity`, `setDecayInterval`, `setDecayAmount`, `pardon`, `resolve`, `history`
- `run_stacy()` — wraps LangGraph in `asyncio.to_thread`, returns `(response_text, severity, points)`
- `_handle_forum_message()` — manages per-thread buffers and Stacy's participation cadence
- `_get_violations_forum()` / `_post_violation_thread()` — forum channel management
- `_forum_interval()` — calculates response cadence based on unique participant count
- In-memory caches: `_guild_cache` (policy), `_sensitivity_cache` (sensitivity), `thread_buffers`, `thread_message_counts`
- `_ensure_guild()` — initialises guild in DB on first message, populates caches

### `stacy_graph.py`
LangGraph `StateGraph` compiled to `app`, plus standalone LLM helpers:
- `stacy_router` — classifies messages using `_ROUTER_PROMPTS[sensitivity]`
- `hr_node` — answers policy questions
- `report_node` — scores the violation as JSON `{"severity", "points", "violator"}`
- `apply_infraction_node` — writes to DB via tool, generates Stacy's reply
- `silent_ignore_node` — sentinel for ignored messages
- `get_appeal_decision(username, context, severity, points, reason)` — reviews appeals, returns `{"decision", "points_removed", "message"}`
- `get_pardon_response(member_name)` — LLM pardon message
- `get_resolve_response(thread_name)` — LLM thread closing message
- `get_forum_response(thread_name, conversation, directly_addressed)` — contextual forum reply

### `stacy_graph_tools.py`
- `StacyState` TypedDict: `messages`, `user_id`, `guild_id`, `target_user_id`, `username`, `target_username`, `hr_policy`, `sensitivity`, `severity`, `points`, `participants`, `violator_name`
- `lookup_hr_policy` — queries `guilds.hr_policy_text`
- `warn_user`, `upload_minor_infraction`, `upload_severe_infraction` — DB write tools

### `database_functions.py`
All MySQL. Key functions:
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

DB credentials are hardcoded in `db_config` at the top of the file.

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

```env
DISCORD_TOKEN=        # Discord bot token
OPENAI_API_KEY=       # OpenAI API key
NGROK_AUTHTOKEN=      # ngrok auth token (free tier works)
```

---

## LLM Usage

Uses **OpenAI `gpt-4o-mini`** via `langchain-openai`. Do not reintroduce Ollama.

- Passive message: 1 LLM call (router)
- Violation: 2 more calls (report_node + apply_infraction_node)
- HR question: 1 more call (hr_node)
- Forum message: 1 call (get_forum_response)
- Appeal: 1 call (get_appeal_decision)
- Pardon / resolve: 1 call each

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
- **FastAPI + ngrok** — `PUBLIC_URL` is set at startup. If ngrok is slow, the first report request may 404. Hasn't been an issue in practice.
- **Forum thread buffers are in-memory** — if the bot restarts, `thread_buffers` and `thread_message_counts` reset. Stacy loses conversation context for open threads but continues to function.
- **`!resolve` deletes threads** — there is no undo. The infraction record remains in the DB.
