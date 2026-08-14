"""
Central configuration for Stacy.

All environment variables are loaded and validated here, once, on import.
Every other module should import from this file instead of calling
os.getenv() directly — keeps the required/optional var list in one place
and fails loudly at startup instead of breaking mysteriously later.
"""
import os
from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            f"Copy .env.example to .env and fill it in."
        )
    return value


# --- Required secrets ---
DISCORD_TOKEN = _require("DISCORD_TOKEN")
OPENAI_API_KEY = _require("OPENAI_API_KEY")
NGROK_AUTHTOKEN = _require("NGROK_AUTHTOKEN")

# --- Database ---
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "stacy_hr_db")

# --- Report server ---
REPORT_PORT = int(os.getenv("REPORT_PORT", "5000"))

# --- App-wide constants ---
VIOLATIONS_FORUM_NAME = "policy-violations"

DEFAULT_POLICY = (
    "Be respectful to all members. No harassment, hate speech, slurs, or targeted abuse. "
    "Keep discussions civil, avoid spamming, and do not share NSFW content outside designated channels. "
    "Repeated or severe violations will be escalated. Stacy is always watching."
)
