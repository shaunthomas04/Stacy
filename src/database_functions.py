import mysql.connector
from mysql.connector import Error

# Configuration - Replace with your actual MySQL credentials
db_config = {
    "host": "localhost",
    "user": "root",
    "password": "password",
    "database": "stacy_hr_db"
}

def get_connection():
    """Returns a connection to the MySQL database."""
    try:
        conn = mysql.connector.connect(**db_config)
        if conn.is_connected():
            return conn
    except Error as e:
        print(f"Error connecting to MySQL: {e}")
    return None

def initialize_guild(guild_id, guild_name, policy_text):
    """
    Registers a new Discord server or updates existing HR policies.
    """
    conn = get_connection()
    if not conn: return

    cursor = conn.cursor()
    try:
        sql = """
            INSERT INTO guilds (guild_id, guild_name, hr_policy_text)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE
                guild_name = VALUES(guild_name);
        """
        cursor.execute(sql, (str(guild_id), guild_name, policy_text))
        conn.commit()
        print(f"HR Department initialized: {guild_name}")
    except Error as e:
        print(f"Error initializing guild: {e}")
    finally:
        cursor.close()
        conn.close()

def set_decay_interval(guild_id: str, minutes: int):
    """Updates how often (in minutes) score decay runs for a guild."""
    conn = get_connection()
    if not conn: return
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE guilds SET decay_interval_minutes = %s WHERE guild_id = %s",
            (minutes, str(guild_id))
        )
        conn.commit()
    except Error as e:
        print(f"Error setting decay interval: {e}")
    finally:
        cursor.close()
        conn.close()


def set_decay_amount(guild_id: str, amount: int):
    """Updates how many points are removed per decay tick for a guild."""
    conn = get_connection()
    if not conn: return
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE guilds SET decay_amount = %s WHERE guild_id = %s",
            (amount, str(guild_id))
        )
        conn.commit()
    except Error as e:
        print(f"Error setting decay amount: {e}")
    finally:
        cursor.close()
        conn.close()


def decay_scores_due() -> list[str]:
    """
    Checks all guilds and applies score decay to any that are due.
    Returns a list of guild_ids that were decayed.
    """
    conn = get_connection()
    if not conn: return []
    cursor = conn.cursor(dictionary=True)
    try:
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

        conn.commit()
        return [g["guild_id"] for g in due_guilds]
    except Error as e:
        print(f"Error during score decay: {e}")
        return []
    finally:
        cursor.close()
        conn.close()


def get_guild_policy(guild_id: str) -> str:
    """Returns the HR policy text for a guild, or an empty string if not found."""
    conn = get_connection()
    if not conn:
        return ""
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT hr_policy_text FROM guilds WHERE guild_id = %s", (str(guild_id),))
        row = cursor.fetchone()
        return row[0] if row else ""
    except Error as e:
        print(f"Error fetching guild policy: {e}")
        return ""
    finally:
        cursor.close()
        conn.close()


def get_guild_sensitivity(guild_id: str) -> str:
    """Returns the sensitivity setting for a guild ('low', 'medium', 'high'). Defaults to 'low'."""
    conn = get_connection()
    if not conn:
        return "low"
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT sensitivity FROM guilds WHERE guild_id = %s", (str(guild_id),))
        row = cursor.fetchone()
        return row[0] if row else "low"
    except Error as e:
        print(f"Error fetching guild sensitivity: {e}")
        return "low"
    finally:
        cursor.close()
        conn.close()


def set_guild_sensitivity(guild_id: str, sensitivity: str):
    """Updates the moderation sensitivity level for a guild."""
    conn = get_connection()
    if not conn:
        return
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE guilds SET sensitivity = %s WHERE guild_id = %s",
            (sensitivity, str(guild_id))
        )
        conn.commit()
    except Error as e:
        print(f"Error setting guild sensitivity: {e}")
    finally:
        cursor.close()
        conn.close()


def update_guild_policy(guild_id: str, policy_text: str):
    """Overwrites the HR policy for a guild."""
    conn = get_connection()
    if not conn: return
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE guilds SET hr_policy_text = %s WHERE guild_id = %s",
            (policy_text, str(guild_id))
        )
        conn.commit()
    except Error as e:
        print(f"Error updating guild policy: {e}")
    finally:
        cursor.close()
        conn.close()


def upsert_user(user_id, guild_id, username, initial_score=0):
    """
    Adds a user to a specific server. 
    Default score is 0 (Clean Record).
    """
    conn = get_connection()
    if not conn: return

    cursor = conn.cursor()
    try:
        sql = """
            INSERT INTO users (user_id, guild_id, username, social_credit_score)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE username = VALUES(username)
        """
        cursor.execute(sql, (str(user_id), str(guild_id), username, initial_score))
        conn.commit()
    except Error as e:
        print(f"Error upserting user: {e}")
    finally:
        cursor.close()
        conn.close()

def log_stacy_inference(user_id, guild_id, message, inference, severity, penalty):
    """
    Stacy adds 'Social Debt' to the user. 
    Penalty is ADDED to the total score.
    """
    conn = get_connection()
    if not conn: return

    cursor = conn.cursor()
    try:
        # 1. Log the infraction
        cursor.execute("""
            INSERT INTO infractions (guild_id, user_id, violation_context, stacy_inference, severity_level, score_penalty)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (str(guild_id), str(user_id), message, inference, severity, penalty))

        # 2. Add to the user's social credit score (Debt increases)
        cursor.execute("""
            UPDATE users 
            SET social_credit_score = social_credit_score + %s 
            WHERE user_id = %s AND guild_id = %s
        """, (penalty, str(user_id), str(guild_id)))

        conn.commit()
        print(f"Social Debt Increased for {user_id}. Added: +{penalty}")
    except Error as e:
        print(f"Error logging infraction: {e}")
    finally:
        cursor.close()
        conn.close()

def reset_user_score(user_id: str, guild_id: str):
    """Resets a user's social credit score to 0 (full pardon)."""
    conn = get_connection()
    if not conn:
        return
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE users SET social_credit_score = 0 WHERE user_id = %s AND guild_id = %s",
            (str(user_id), str(guild_id))
        )
        conn.commit()
    except Error as e:
        print(f"Error resetting user score: {e}")
    finally:
        cursor.close()
        conn.close()


def get_latest_infraction(user_id: str, guild_id: str) -> dict | None:
    """Returns the most recent unappealed infraction for a user, or None."""
    conn = get_connection()
    if not conn:
        return None
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT infraction_id, violation_context, severity_level, score_penalty
            FROM infractions
            WHERE user_id = %s AND guild_id = %s AND appealed = FALSE
            ORDER BY timestamp DESC
            LIMIT 1
        """, (str(user_id), str(guild_id)))
        return cursor.fetchone()
    except Error as e:
        print(f"Error fetching latest infraction: {e}")
        return None
    finally:
        cursor.close()
        conn.close()


def mark_infraction_appealed(infraction_id: int):
    """Marks an infraction as appealed so it cannot be appealed again."""
    conn = get_connection()
    if not conn:
        return
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE infractions SET appealed = TRUE WHERE infraction_id = %s",
            (infraction_id,)
        )
        conn.commit()
    except Error as e:
        print(f"Error marking infraction appealed: {e}")
    finally:
        cursor.close()
        conn.close()


def deduct_user_score(user_id: str, guild_id: str, points: int):
    """Subtracts points from a user's score, floored at 0."""
    conn = get_connection()
    if not conn:
        return
    cursor = conn.cursor()
    try:
        cursor.execute("""
            UPDATE users
            SET social_credit_score = GREATEST(0, social_credit_score - %s)
            WHERE user_id = %s AND guild_id = %s
        """, (points, str(user_id), str(guild_id)))
        conn.commit()
    except Error as e:
        print(f"Error deducting user score: {e}")
    finally:
        cursor.close()
        conn.close()


def get_hr_report_data(user_id, guild_id):
    """Fetches data needed for your !HRReport HTML generation."""
    conn = get_connection()
    if not conn: return None

    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT u.username, u.social_credit_score, u.current_status_role, i.*
            FROM users u
            LEFT JOIN infractions i ON u.user_id = i.user_id AND u.guild_id = i.guild_id
            WHERE u.user_id = %s AND u.guild_id = %s
            ORDER BY i.timestamp DESC
        """, (str(user_id), str(guild_id)))
        return cursor.fetchall()
    finally:
        cursor.close()
        conn.close()


def get_user_score(user_id: str, guild_id: str) -> int:
    """Returns the user's current social credit score. Returns 0 if user not found."""
    conn = get_connection()
    if not conn: return 0

    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT social_credit_score FROM users WHERE user_id = %s AND guild_id = %s",
            (str(user_id), str(guild_id))
        )
        row = cursor.fetchone()
        return row[0] if row else 0
    except Error as e:
        print(f"Error fetching user score: {e}")
        return 0
    finally:
        cursor.close()
        conn.close()


# --- Execution Block ---

if __name__ == "__main__":
    # 1. Initialize Server
    server_rules = (
        "1. No PHP. It's 2026, let it go.\n"
        "2. All memes must be high-quality. Low-effort brainrot will be penalized.\n"
        "3. Stacy's word is law."
    )
    
    MY_GUILD_ID = "1234567890"
    initialize_guild(MY_GUILD_ID, "Stacy's Testing Lab", server_rules)

    # 2. Add User (Starts at 0)
    MY_USER_ID = "987654321"
    upsert_user(MY_USER_ID, MY_GUILD_ID, "Shaun_Dev")

    # 3. Stacy catches a violation and ADDS to the score
    log_stacy_inference(
        user_id=MY_USER_ID,
        guild_id=MY_GUILD_ID,
        message="Actually, PHP 8.3 is pretty fast...",
        inference="User attempted to normalize legacy syntax. This is a direct attack on the future.",
        severity="Medium",
        penalty=50  # Adds 50 to the debt
    )

    # 4. Preview Report
    report = get_hr_report_data(MY_USER_ID, MY_GUILD_ID)
    if report:
        print(f"\n--- HR Report Preview for {report[0]['username']} ---")
        print(f"Total Social Debt: {report[0]['social_credit_score']}") # Higher is worse!
        print(f"Latest Inference: {report[0]['stacy_inference']}")