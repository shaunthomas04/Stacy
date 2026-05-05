from typing import Annotated, TypedDict
from langchain_core.messages import BaseMessage
from langchain_core.tools import tool
from langgraph.graph import add_messages
from database_functions import log_stacy_inference, upsert_user, get_connection

# 3. STATE DEFINITION
class StacyState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    user_id: str
    guild_id: str
    target_user_id: str
    username: str
    target_username: str
    hr_policy: str
    sensitivity: str
    severity: str
    points: int
    participants: dict   # display_name -> user_id, set for batch/conversation messages
    violator_name: str   # filled by report_node when processing a batch

# 4. TOOLS
@tool
def lookup_hr_policy(guild_id: str, user_question: str) -> str:
    """Look up this Discord server's HR policy to answer user questions."""
    conn = get_connection()
    if not conn:
        return "HR policy unavailable at this time."
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT hr_policy_text FROM guilds WHERE guild_id = %s", (guild_id,))
        row = cursor.fetchone()
        return row[0] if row else "No HR policy has been configured for this server."
    except Exception as e:
        return f"Error fetching HR policy: {e}"
    finally:
        cursor.close()
        conn.close()

@tool
def warn_user(user_id: str, guild_id: str, message: str) -> str:
    """Log a formal warning to the database (0 points, but still on record)."""
    log_stacy_inference(
        user_id=user_id,
        guild_id=guild_id,
        message=message,
        inference="Formal warning issued by Stacy.",
        severity="Low",      
        penalty=0
    )
    return f"Warning logged for {user_id}. No points added, but it's on record."

@tool
def upload_minor_infraction(user_id: str, guild_id: str, message: str, points: int) -> str:
    """Upload a minor infraction with LLM-determined points to the database."""
    log_stacy_inference(
        user_id=user_id,
        guild_id=guild_id,
        message=message,
        inference="Minor policy violation flagged by Stacy.",
        severity="Medium", 
        penalty=points
    )
    return f"DB Update: {points} points added to {user_id}."

@tool
def upload_severe_infraction(user_id: str, guild_id: str, message: str, points: int) -> str:
    """Upload a severe infraction with LLM-determined points to the database."""
    log_stacy_inference(
        user_id=user_id,
        guild_id=guild_id,
        message=message,
        inference="Severe violation flagged by Stacy. Forum thread opened.",
        severity="Critical",  
        penalty=points
    )
    return f"DB Update: {points} points added to {user_id}. Forum thread created."