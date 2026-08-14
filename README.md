# Stacy — Discord HR Bot

Stacy is a satirical HR enforcement bot for Discord servers. She passively monitors chat, logs policy violations, tracks a social credit score per user, assigns roles based on standing, escalates severe incidents to a dedicated forum channel, and generates styled compliance reports on demand. The tone is corporate-satirical — Stacy sounds like a real HR rep, just slightly more anxious and insufferable than necessary.

---

## What Stacy Does

**Passive moderation** — Every message in your server is quietly evaluated. Normal chat is ignored. Policy violations get flagged and logged. Severe violations open a forum thread automatically.

**Social credit score** — Each user accumulates points for violations. Higher score = worse standing. Scores decay on a configurable schedule so people can work their way back.

**Discord roles** — Stacy assigns and updates roles automatically based on score:

| Score | Role |
|---|---|
| 0 | HR Approved |
| 1–5 | Under Review |
| 6–15 | Suspended Pay |
| 16+ | Blacklisted |

**Forum escalation** — Critical infractions automatically open a thread in a `#policy-violations` forum channel. Stacy participates in the conversation, backs off as more people join, and responds immediately if directly addressed.

**HTML reports** — `!history @user` generates a dark-themed HTML report with a Chart.js score graph and full infraction history, served via a live ngrok URL.

**Appeals** — Users can appeal their most recent infraction with a written reason. Stacy reviews it via LLM and may reduce or clear points.

---

## Tech Stack

| Component | Technology |
|---|---|
| Bot framework | `discord.py` |
| AI agent | LangGraph + OpenAI `gpt-4o-mini` via `langchain-openai` |
| Database | MySQL via `mysql-connector-python` |
| Report server | FastAPI + uvicorn + pyngrok |
| Scheduler | APScheduler |
| Config | python-dotenv |

---

## Setup

### Prerequisites (either path)
- OpenAI API key
- Discord bot token
- ngrok auth token (free tier works)

### 1. Clone and configure `.env`
```bash
git clone https://github.com/shaunthomas04/Stacy-From-Human-Resources.git
cd Stacy
cp .env.example .env
```
Fill in `DISCORD_TOKEN`, `OPENAI_API_KEY`, and `NGROK_AUTHTOKEN`. Leave the `DB_*` values as-is unless you have a reason to change them — see `.env.example` for what each one does.

Then pick one of the two paths below.

---

### Path A — Docker (recommended: zero local MySQL/Python setup)
Requires only Docker and Docker Compose.
```bash
docker compose up -d
```
This builds the bot image, starts MySQL, auto-loads `stacy_hr_db.sql` on first boot, waits for the DB to be healthy, then starts the bot. Watch logs with:
```bash
docker compose logs -f bot
```
DB data lives in a named volume and survives `docker compose down` / rebuilds — only `docker compose down -v` wipes it. To pick up code changes, rebuild the bot image:
```bash
docker compose up -d --build bot
```

### Path B — Native (venv + local MySQL, best for active development)
- Python 3.10+
- MySQL 8.x running locally

```bash
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
mysql -u root -p < stacy_hr_db.sql
cd src
python bot.py
```
Faster iteration than Docker — no image rebuild between code changes, just restart `python bot.py`.

---

Stacy prints the ngrok report URL on startup either way.

### Invite Stacy to your server
```
https://discord.com/oauth2/authorize?client_id=1449588724265521154&scope=bot&permissions=586514329238594
```

---

## Using Stacy in Discord

### Passive moderation
Stacy reads every message automatically. You don't need to do anything — she'll flag violations, log infractions, and reply in the channel when she has something to say.

### Asking Stacy a question
Mention her directly to ask about server rules:
```
@Stacy what's the policy on spam?
```

### Reporting another user
Mention Stacy and tag the user you're reporting:
```
@Stacy @username just insulted me
```

---

## Commands

### Available to everyone
| Command | What it does |
|---|---|
| `!stacyHelp` | Show the full command list |
| `!policy` | View this server's HR policy |
| `!history @user` | Generate and open an HTML HR report for a user |
| `!appeal <reason>` | Appeal your most recent infraction — Stacy reviews it and may reduce or clear points |
| `!ping` | Check that Stacy is online |

### Server owner only
| Command | What it does |
|---|---|
| `!setPolicy <text>` | Set a custom HR policy for your server |
| `!setSensitivity <low\|medium\|high>` | Control how strictly Stacy enforces rules (default: `low`) |
| `!setDecayInterval <minutes>` | How often scores decay — default is 1440 (24 hours) |
| `!setDecayAmount <points>` | Points removed per decay tick — default is 5 |
| `!pardon @user` | Clear a user's record and reset their score to 0 |
| `!resolve` | Close and delete the current `policy-violations` forum thread |

---

## Sensitivity Levels

| Level | Behaviour |
|---|---|
| `low` | Only flags egregious violations — hateful speech, threats, serious harassment. Ignores everything else. |
| `medium` | Flags clear violations and persistent directed rudeness. |
| `high` | Flags borderline content, directed sarcasm, and anything hostile. |
