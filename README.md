# Stacy HR Bot

A Discord HR enforcement bot powered by LangGraph and the OpenAI API. Stacy passively monitors server messages, logs infractions to a MySQL database, manages social credit scores, assigns Discord roles, and generates HTML compliance reports.

---

## Features

| Feature | Description |
|---|---|
| **Passive Moderation** | Every non-bot message is evaluated via a sliding context window (last 10 messages per channel). Stacy ignores normal chat, answers HR questions when @mentioned, and files infractions for policy violations. |
| **Infraction Logging** | Violations are written to MySQL with severity (Low / Medium / Critical), point penalty, context message, and Stacy's inference. |
| **Social Credit Score** | Each user has a running score per guild. Points accumulate per infraction. Higher score = worse standing. Score decays on a configurable schedule. |
| **Role Assignment** | Stacy automatically assigns one of four HR roles based on score: `HR Approved`, `Under Review`, `Suspended Pay`, `Blacklisted`. Roles are created if they don't exist. Updated immediately after any infraction and on member join. |
| **Score Decay** | Configurable automatic decay — removes N points from all users every M minutes. Defaults to 5 points every 24 hours. |
| **Per-Guild Policy** | Each server has its own HR policy text stored in the DB. Set via `!setPolicy`. Stacy enforces custom rules alongside built-in defaults. |
| **Per-Guild Sensitivity** | Configurable moderation threshold: `low` (egregious violations only), `medium` (clear violations + persistent rudeness), `high` (flags borderline content). |
| **`!history @user`** | Generates a styled HTML report (dark theme, Chart.js debt graph, infraction table) served via FastAPI + ngrok. |
| **`!pardon @user`** | Owner-only. Clears a user's record and resets their score to 0. Stacy is reluctant about it but complies. |
| **Forum thread escalation** | On Critical infractions, Stacy automatically finds or creates a `policy-violations` forum channel and opens a new incident thread with the violation details. |
| **Multi-guild Support** | Each guild has its own policy, sensitivity, decay settings, users, and infractions — fully isolated. |

---

## Planned

- **`!appeal`** — user submits a written appeal against an infraction; Stacy reviews it via LLM and either upholds or dismisses it in character. A compelling appeal can result in points being removed.
- **Infraction streaks** — if a user is flagged X times within Y minutes, Stacy automatically escalates regardless of individual severity. Configurable thresholds per guild.
- **Good behaviour bonus** — after N days with no infractions, the user's score decays at an accelerated rate as a reward for staying clean.

---

## Tech Stack

| Component | Technology |
|---|---|
| **Bot Framework** | `discord.py` (`discord` + `commands.Bot`) |
| **AI Agent** | LangGraph (`langgraph`) with OpenAI (`langchain-openai`) |
| **LLM** | OpenAI `gpt-4o-mini` |
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
```bash
mysql -u root -p < stacy_hr_db.sql
```

If upgrading an existing install, run the ALTER TABLE statements in `stacy_hr_db.sql` comments for any columns added after initial setup.

### 4. Configure environment variables
Create a `.env` file in the project root:
```env
DISCORD_TOKEN=your_discord_bot_token
OPENAI_API_KEY=your_openai_api_key
NGROK_AUTHTOKEN=your_ngrok_auth_token
```

### 5. Run the bot
```bash
cd src
python bot.py
```

The bot prints the ngrok report URL on startup.

### Invite the Bot
```
https://discord.com/oauth2/authorize?client_id=1449588724265521154&scope=bot&permissions=586514329238594
```

---

## Commands

### General
| Command | Description |
|---|---|
| `!stacyHelp` | Show the command menu |
| `!policy` | View this server's current HR policy |
| `!history @user` | Generate and serve an HTML HR report |
| `!ping` | Health check |
| `@Stacy <question>` | Ask Stacy an HR policy question |
| `@Stacy @user <reason>` | Report a user to Stacy |

### Server Owner Only
| Command | Description |
|---|---|
| `!setPolicy <text>` | Update the server's HR policy |
| `!setSensitivity <low\|medium\|high>` | Set moderation threshold (default: `low`) |
| `!setDecayInterval <minutes>` | How often scores decay (default: 1440) |
| `!setDecayAmount <points>` | Points removed per decay tick (default: 5) |
| `!pardon @user` | Clear a user's record and reset score to 0 |

---

## Database Schema

```sql
guilds (
    guild_id              VARCHAR(255) PRIMARY KEY,
    guild_name            VARCHAR(255),
    hr_policy_text        TEXT,
    decay_interval_minutes INT DEFAULT 1440,
    decay_amount          INT DEFAULT 5,
    last_decay_at         DATETIME DEFAULT NULL,
    sensitivity           ENUM('low','medium','high') DEFAULT 'low'
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
    infraction_id    INT AUTO_INCREMENT PRIMARY KEY,
    guild_id         VARCHAR(255),
    user_id          VARCHAR(255),
    violation_context TEXT,
    stacy_inference   TEXT,
    severity_level    ENUM('Low','Medium','Critical'),
    score_penalty     INT,
    timestamp         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
```

---

## Role Thresholds

| Score | Role |
|---|---|
| 0 | HR Approved |
| 1–5 | Under Review |
| 6–15 | Suspended Pay |
| 16+ | Blacklisted |

Thresholds are defined in `ROLE_THRESHOLDS` at the top of `src/social_credit.py`.
