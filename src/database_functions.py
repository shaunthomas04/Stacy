"""
All MySQL access for Stacy — single source of truth for DB queries.

get_cursor() owns the connection lifecycle (connect, commit-on-success,
always close) so individual functions only ever write the query itself.
"""
import logging
from contextlib import contextmanager

import mysql.connector
from mysql.connector import Error

import config

logger = logging.getLogger(__name__)


@contextmanager
def get_cursor(dictionary: bool = False):
    """
    Yields a cursor for one unit of work against a fresh connection.
    Commits automatically if the block completes without raising, and
    always closes the cursor/connection afterward.

    Yields None if the connection itself fails — callers should check
    `if cursor:` before using it and fall back to a default value.
    Errors raised while the block is executing are logged here and
    swallowed (matching the bot's "log and degrade gracefully" style),
    so callers never need their own try/except around queries.
    """
    try:
        conn = mysql.connector.connect(
            host=config.DB_HOST,
            port=config.DB_PORT,
            user=config.DB_USER,
            password=config.DB_PASSWORD,
            database=config.DB_NAME,
        )
    except Error as e:
        logger.error(f"Error connecting to MySQL: {e}")
        yield None
        return

    cursor = conn.cursor(dictionary=dictionary)
    try:
        yield cursor
        conn.commit()
    except Error as e:
        logger.error(f"Database error: {e}")
    finally:
        cursor.close()
        conn.close()


def initialize_guild(guild_id, guild_name, policy_text):
    """
    Registers a new Discord server, or updates its name if it already
    exists. Never overwrites hr_policy_text on duplicate.
    """
    with get_cursor() as cursor:
        if cursor:
            cursor.execute("""
                INSERT INTO guilds (guild_id, guild_name, hr_policy_text)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE guild_name = VALUES(guild_name)
            """, (str(guild_id), guild_name, policy_text))
            logger.info(f"HR Department initialized: {guild_name}")


def set_decay_interval(guild_id: str, minutes: int):
    """Updates how often (in minutes) score decay runs for a guild."""
    with get_cursor() as cursor:
        if cursor:
            cursor.execute(
                "UPDATE guilds SET decay_interval_minutes = %s WHERE guild_id = %s",
                (minutes, str(guild_id))
            )


def set_decay_amount(guild_id: str, amount: int):
    """Updates how many points are removed per decay tick for a guild."""
    with get_cursor() as cursor:
        if cursor:
            cursor.execute(
                "UPDATE guilds SET decay_amount = %s WHERE guild_id = %s",
                (amount, str(guild_id))
            )


def decay_scores_due() -> list[str]:
    """
    Checks all guilds and applies score decay to any that are due.
    Returns a list of guild_ids that were decayed.
    """
    decayed_ids = []
    with get_cursor(dictionary=True) as cursor:
        if cursor:
            cursor.execute("""
                SELECT guild_id, decay_amount
                FROM guilds
                WHERE last_decay_at IS NULL
                   OR TIMESTAMPDIFF(SECOND, last_decay_at, NOW()) >= decay_interval_minutes * 60
            """)
            due_guilds = cursor.fetchall()

            for guild in due_guilds:
                gid = guild["guild_id"]
                amount = guild["decay_amount"]
                cursor.execute("""
                    UPDATE users
                    SET social_credit_score = GREATEST(0, social_credit_score - %s)
                    WHERE guild_id = %s
                """, (amount, gid))
                cursor.execute(
                    "UPDATE guilds SET last_decay_at = NOW() WHERE guild_id = %s",
                    (gid,)
                )

            decayed_ids = [g["guild_id"] for g in due_guilds]
    return decayed_ids


def get_guild_policy(guild_id: str) -> str:
    """Returns the HR policy text for a guild, or an empty string if not found."""
    policy = ""
    with get_cursor() as cursor:
        if cursor:
            cursor.execute("SELECT hr_policy_text FROM guilds WHERE guild_id = %s", (str(guild_id),))
            row = cursor.fetchone()
            policy = row[0] if row else ""
    return policy


def get_guild_sensitivity(guild_id: str) -> str:
    """Returns the sensitivity setting for a guild ('low', 'medium', 'high'). Defaults to 'low'."""
    sensitivity = "low"
    with get_cursor() as cursor:
        if cursor:
            cursor.execute("SELECT sensitivity FROM guilds WHERE guild_id = %s", (str(guild_id),))
            row = cursor.fetchone()
            sensitivity = row[0] if row else "low"
    return sensitivity


def set_guild_sensitivity(guild_id: str, sensitivity: str):
    """Updates the moderation sensitivity level for a guild."""
    with get_cursor() as cursor:
        if cursor:
            cursor.execute(
                "UPDATE guilds SET sensitivity = %s WHERE guild_id = %s",
                (sensitivity, str(guild_id))
            )


def update_guild_policy(guild_id: str, policy_text: str):
    """Overwrites the HR policy for a guild."""
    with get_cursor() as cursor:
        if cursor:
            cursor.execute(
                "UPDATE guilds SET hr_policy_text = %s WHERE guild_id = %s",
                (policy_text, str(guild_id))
            )


def upsert_user(user_id, guild_id, username, initial_score=0):
    """
    Adds a user to a specific server if they don't already exist.
    Default score is 0 (Clean Record).
    """
    with get_cursor() as cursor:
        if cursor:
            cursor.execute("""
                INSERT INTO users (user_id, guild_id, username, social_credit_score)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE username = VALUES(username)
            """, (str(user_id), str(guild_id), username, initial_score))


def log_stacy_inference(user_id, guild_id, message, inference, severity, penalty):
    """
    Logs an infraction and adds the penalty to the user's social credit
    score. Score is "social debt" — the penalty is ADDED to the total.
    """
    with get_cursor() as cursor:
        if cursor:
            cursor.execute("""
                INSERT INTO infractions (guild_id, user_id, violation_context, stacy_inference, severity_level, score_penalty)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (str(guild_id), str(user_id), message, inference, severity, penalty))
            cursor.execute("""
                UPDATE users
                SET social_credit_score = social_credit_score + %s
                WHERE user_id = %s AND guild_id = %s
            """, (penalty, str(user_id), str(guild_id)))
            logger.info(f"Social Debt Increased for {user_id}. Added: +{penalty}")


def reset_user_score(user_id: str, guild_id: str):
    """Resets a user's social credit score to 0 (full pardon)."""
    with get_cursor() as cursor:
        if cursor:
            cursor.execute(
                "UPDATE users SET social_credit_score = 0 WHERE user_id = %s AND guild_id = %s",
                (str(user_id), str(guild_id))
            )


def get_latest_infraction(user_id: str, guild_id: str) -> dict | None:
    """Returns the most recent unappealed infraction for a user, or None."""
    infraction = None
    with get_cursor(dictionary=True) as cursor:
        if cursor:
            cursor.execute("""
                SELECT infraction_id, violation_context, severity_level, score_penalty
                FROM infractions
                WHERE user_id = %s AND guild_id = %s AND appealed = FALSE
                ORDER BY timestamp DESC
                LIMIT 1
            """, (str(user_id), str(guild_id)))
            infraction = cursor.fetchone()
    return infraction


def mark_infraction_appealed(infraction_id: int):
    """Marks an infraction as appealed so it cannot be appealed again."""
    with get_cursor() as cursor:
        if cursor:
            cursor.execute(
                "UPDATE infractions SET appealed = TRUE WHERE infraction_id = %s",
                (infraction_id,)
            )


def deduct_user_score(user_id: str, guild_id: str, points: int):
    """Subtracts points from a user's score, floored at 0."""
    with get_cursor() as cursor:
        if cursor:
            cursor.execute("""
                UPDATE users
                SET social_credit_score = GREATEST(0, social_credit_score - %s)
                WHERE user_id = %s AND guild_id = %s
            """, (points, str(user_id), str(guild_id)))


def get_hr_report_data(user_id, guild_id):
    """Fetches all data needed for a user's HR report — profile + full infraction history."""
    data = None
    with get_cursor(dictionary=True) as cursor:
        if cursor:
            cursor.execute("""
                SELECT u.username, u.social_credit_score, u.current_status_role, i.*
                FROM users u
                LEFT JOIN infractions i ON u.user_id = i.user_id AND u.guild_id = i.guild_id
                WHERE u.user_id = %s AND u.guild_id = %s
                ORDER BY i.timestamp DESC
            """, (str(user_id), str(guild_id)))
            data = cursor.fetchall()
    return data


def get_user_score(user_id: str, guild_id: str) -> int:
    """Returns the user's current social credit score. Returns 0 if user not found."""
    score = 0
    with get_cursor() as cursor:
        if cursor:
            cursor.execute(
                "SELECT social_credit_score FROM users WHERE user_id = %s AND guild_id = %s",
                (str(user_id), str(guild_id))
            )
            row = cursor.fetchone()
            score = row[0] if row else 0
    return score
