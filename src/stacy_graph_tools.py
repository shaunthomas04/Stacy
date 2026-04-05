import random
from typing import Annotated, TypedDict
from langchain_core.messages import BaseMessage
from langchain_core.tools import tool
from langgraph.graph import add_messages

# 3. STATE DEFINITION
class StacyState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    user_id: str
    severity: str

# 4. TOOLS
@tool
def lookup_hr_policy(user_question: str) -> str:
    """Look up Discord HR policy to answer user questions."""
    return "According to Discord HR policy, you need prior approval for early leave."

@tool
def determine_severity(user_complaint: str) -> str:
    """Analyze a user's speech/message to determine the severity of a violation."""
    # Logic can be expanded here, but for now, we simulate the logic from your diagram
    return random.choice(["warning", "minor", "severe"])

@tool
def warn_user(message_context: str) -> str:
    """Draft a custom formal warning for a policy violation."""
    return f"Formal Warning Issued: Your message '{message_context}' violates policy."

@tool
def upload_minor_infraction(user_id: str, points: int = 1) -> str:
    """Upload small infraction points to the database."""
    return f"DB Update: {points} point added to {user_id}."

@tool
def upload_severe_infraction(user_id: str, points: int = 5) -> str:
    """Upload severe points and create a community forum discussion post."""
    return f"DB Update: {points} points added to {user_id}. Forum thread created."
