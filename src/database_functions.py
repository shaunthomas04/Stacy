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
                guild_name = VALUES(guild_name),
                hr_policy_text = VALUES(hr_policy_text);
        """
        cursor.execute(sql, (str(guild_id), guild_name, policy_text))
        conn.commit()
        print(f"HR Department initialized: {guild_name}")
    except Error as e:
        print(f"Error initializing guild: {e}")
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