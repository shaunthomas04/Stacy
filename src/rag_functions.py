from langchain_ollama import ChatOllama
from langchain.tools import tool
from langchain_ollama import ChatOllama

@tool
def upload_infraction(user: str, infraction: str) -> str:
    """Upload a user infraction when they commit and hr violation to the database when they use a swear word."""
    # Normally you'd write to your DB here.
    # For the example we just return a string.
    return f"Uploaded infraction for {user}: {infraction}"


llm = ChatOllama(
    model="qwen3:0.6b",
    temperature=0,
).bind_tools([upload_infraction])

# messages = [
#     (
#         "system",
#         "You are a moderator bot that detects if someone is using swear words, please upload them to the database when they swear",
#     ),
#     ("human", "hello there"),
# ]
# ai_msg = llm.invoke(messages)

def ask_stacy_llm(user_message: str) -> str:
    """
    Takes a user message, sends it to the LLM, and returns Stacy's response.
    """
    messages = [
        (
            "system",
            """
            You are Stacy, a helpful HR assistant. Most employees do not like you, 
            but you are not aware of it. Always provide polite, professional, and helpful HR guidance.
            """
        ),
        ("human", user_message),
    ]

    ai_msg = llm.invoke(messages)
    return ai_msg.content

