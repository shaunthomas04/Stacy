# Stacy HR Bot

A Discord HR enforcement bot powered by LangGraph and the OpenAI API. Stacy passively monitors server messages, logs infractions to a MySQL database, manages social credit scores, assigns Discord roles, and generates HTML compliance reports.

---

## Features

### Working Now

| Feature | Description |
|---|---|
| **Passive Moderation** | Every non-bot message is routed through LangGraph. Stacy ignores normal chat, answers HR questions when @mentioned, and files infractions for policy violations. |
| **Infraction Logging** | Violations are written to MySQL with severity (Low / Medium / Critical), point penalty, context message, and Stacy's inference. |
| **Social Credit Score** | Each user has a running score per guild. Points accumulate per infraction. Higher score = worse standing. |
| **Role Assignment** | Stacy automatically assigns one of four HR roles based on score: `HR Approved`, `Under Review`, `Suspended Pay`, `Blacklisted`. Roles are created if they don't exist. |
| **Cron Role Sync** | A scheduler runs every 5 minutes to sync all member roles against current DB scores. Roles are also updated immediately after any infraction. |
| **!History @user** | Generates a styled HTML report (dark theme, Chart.js debt-over-time graph, infraction table) and serves it via FastAPI + ngrok. |
| **Multi-guild Support** | Each guild has its own HR policy text, user records, and infractions — fully isolated. |

### Planned / TODO

#### 1. Custom HR Rules Upload
Allow server admins to set their own HR policy rules via a Discord command instead of the current hardcoded string in `bot.py`. The policy is already stored in the `guilds` table — it just needs an update command and the `lookup_hr_policy` tool needs to query the DB instead of returning a hardcoded string.

**Command:** `!SetPolicy <rules text>` (admin only)

**What needs to change:**
- Add `!SetPolicy` command in `bot.py` that calls `update_guild_policy(guild_id, text)` in `database_functions.py`
- Rewrite `lookup_hr_policy` in `stacy_graph_tools.py` to `SELECT hr_policy_text FROM guilds WHERE guild_id = %s` using the `guild_id` from LangGraph state
- Pass `guild_id` into the tool call in `hr_node` in `stacy_graph.py`

---

#### 2. Message Rolling Window (Passive Moderation Throttle)
Currently every single message sent in the server triggers a full OpenAI API call through LangGraph. This is expensive and unnecessary — most messages are normal chat that the router will `ignore` anyway.

**Proposed approach:** buffer the last N messages per channel in memory. Only run the LangGraph passive check when:
- The buffer hits a configurable size (e.g. every 10 messages), OR
- A message directly @mentions Stacy (always runs immediately regardless of buffer)

The buffered messages are concatenated and passed as a single context block to the router, which scans them in bulk and returns a list of any violations found.

**What needs to change:**
- Add a `channel_buffers: dict[int, list[str]]` in `bot.py` keyed by `channel_id`
- In `on_message`, append to the buffer instead of calling `run_stacy` immediately
- When buffer hits threshold, call a new `run_stacy_bulk(messages, guild_id, ...)` function
- `stacy_router` system prompt needs to be updated to handle a batch of messages and return a structured list of findings instead of a single keyword
- @mention always bypasses the buffer and runs immediately

---

#### 3. Per-Guild Sensitivity Parameter
Right now the router's sensitivity is controlled entirely by prompt engineering — the same aggressive "when in doubt, ignore" instructions apply to all servers. Some servers may want Stacy to be more aggressive, others more relaxed.

Store a `sensitivity` field in the `guilds` table (`low`, `medium`, `high`) and inject different router instructions based on the guild's setting.

**Command:** `!SetSensitivity low|medium|high` (admin only)

**Sensitivity behavior:**
| Level | Behavior |
|---|---|
| `low` | Only flag egregious violations (slurs, threats). Ignore everything else. Current default behavior. |
| `medium` | Flag clear violations and persistent rudeness. Issue soft warnings for borderline content. |
| `high` | Flag borderline content, issue friendly reminders for mild violations, escalate repeated issues. |

**What needs to change:**
- Add `sensitivity ENUM('low','medium','high') DEFAULT 'low'` column to `guilds` table
- Add `get_guild_sensitivity(guild_id)` and `set_guild_sensitivity(guild_id, level)` in `database_functions.py`
- Add `!SetSensitivity` command in `bot.py`
- Fetch sensitivity before calling `run_stacy` and pass it through the LangGraph state
- `StacyState` in `stacy_graph_tools.py` needs a `sensitivity` field
- `stacy_router` in `stacy_graph.py` switches between three different system prompts based on the value

---

#### 4. Social Credit Score Decay
The cron scheduler already runs every 5 minutes for role sync. Add a second scheduled job (daily or configurable) that applies a decay multiplier to all users' scores — "HR forgives but never forgets."

**Proposed logic:** `new_score = max(0, floor(score * 0.95))` — 5% decay per day, score floors at 0.

**What needs to change:**
- Add `decay_scores()` to `database_functions.py`: `UPDATE users SET social_credit_score = GREATEST(0, FLOOR(social_credit_score * 0.95))`
- Add a second `scheduler.add_job` in `on_ready` with `trigger='cron', hour=0` (midnight)

---

#### 5. Forum Thread Escalation (Future)
For Critical severity infractions, automatically create a Discord forum thread in a designated HR channel for open discussion. The `upload_severe_infraction` tool already marks these — it just needs the actual `guild.create_thread()` call wired in.

---

## Tech Stack

| Component | Technology |
|---|---|
| **Bot Framework** | `discord.py` (`discord` + `commands.Bot`) |
| **AI Agent** | LangGraph (`langgraph`) with OpenAI (`langchain-openai`) |
| **LLM** | OpenAI `gpt-4o-mini` — cheap, fast, solid tool-use support |
| **Database** | MySQL via `mysql-connector-python` |
| **Report Server** | FastAPI + uvicorn, tunneled via pyngrok |
| **Scheduler** | APScheduler (`apscheduler`) |
| **Config** | `python-dotenv` |

---

## Setup

### Prerequisites
- Python 3.10+
- MySQL 8.x running locally (or remote)
- An OpenAI API key
- A Discord bot token
- An ngrok auth token (free tier works)

### 1. Clone and create a virtual environment
```bash
git clone <repo>
cd Stacy
python -m venv venv
# Windows
venv\Scripts\activate
# macOS/Linux
source venv/bin/activate
```

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Set up the database
Run the SQL schema file against your MySQL instance:
```bash
mysql -u root -p < stacy_hr_db.sql
```

### 4. Configure environment variables
Create a `.env` file in the project root:
```env
DISCORD_TOKEN=your_discord_bot_token
OPENAI_API_KEY=your_openai_api_key
NGROK_AUTHTOKEN=your_ngrok_auth_token
GUILD_ID=your_discord_guild_id
USER_ID=your_discord_user_id
```

### 5. Run the bot
```bash
cd src
python bot.py
```

The bot will print the ngrok URL where reports are served on startup.

### Invite the Bot
```
https://discord.com/oauth2/authorize?client_id=1449588724265521154&scope=bot&permissions=586514329238594
```

---

## Commands

| Command | Description |
|---|---|
| `!ping` | Health check — responds with Pong |
| `!hello` | Greet HR |
| `!rules` | Print the default HR guidelines |
| `!helpme` | List all commands |
| `!History @user` | Generate and serve an HTML HR report for the user |
| `@Stacy <question>` | Ask Stacy an HR policy question |
| `@Stacy @user <reason>` | Report a user to Stacy for a violation |

---

## Database Schema (summary)

```sql
guilds      (guild_id PK, guild_name, hr_policy_text)
users       (user_id, guild_id, username, social_credit_score, current_status_role)
infractions (infraction_id, guild_id, user_id, violation_context, stacy_inference,
             severity_level, score_penalty, timestamp)
```

---

## Role Thresholds

| Score | Role |
|---|---|
| 0 | HR Approved |
| 1–5 | Under Review |
| 6–15 | Suspended Pay |
| 16+ | Blacklisted |
