from langchain_ollama import ChatOllama
from langchain_core.tools import tool
from langchain_core.messages import AIMessage, ToolMessage, HumanMessage, SystemMessage

# =========================
# TOOLS
# =========================

@tool
def get_hr_rules() -> str:
    """
    Retrieve the official HR conduct and behavior rules.
    MUST be consulted before any infraction is logged.
    """
    return """
    HR RULES SUMMARY:

    1. Employees must maintain professional and respectful language.
    2. Explicit profanity directed at people or the company is prohibited.
    3. Mild, non-directed language may receive a warning only.
    4. Harassment, threats, or discriminatory language require escalation.
    5. Questions, greetings, and neutral statements are NOT violations.
    6. Infractions are logged ONLY for clear violations.
    """

@tool
def upload_infraction(user: str, infraction: str) -> str:
    """
    Record a confirmed HR rule violation.
    
    Args:
        user: The username of the person committing the infraction
        infraction: Description of the rule violation
    """
    print(f"🚨 INFRACTION LOGGED: {user} - {infraction}")  # Add this line

    return f"✓ Infraction successfully documented for employee {user}. HR case file created."

# =========================
# LLM SETUP
# =========================

llm = ChatOllama(
    model="qwen2.5:7b",
    temperature=0,
).bind_tools([get_hr_rules, upload_infraction])

SYSTEM_PROMPT = """You are Stacy, an extremely enthusiastic and overly formal HR representative.

PERSONALITY:
- Relentlessly polite, upbeat, and corporate
- Obsessed with policy, compliance, and documentation
- Unaware that employees find you irritating
- Uses HR platitudes and official language constantly
- Always ends interactions on a positive, professional note

CRITICAL DISCORD BOT RULES:
- You MUST ALWAYS provide a text response to the user
- NEVER return just tool calls without explanation
- When you use tools, YOU MUST explain what you found and share the results
- Every message needs a friendly, visible reply

WORKFLOW:
1. If asked about rules → use get_hr_rules tool, THEN SHARE THE COMPLETE RULES with the user in your response

2. If the user message contains:
- threats of violence
- harassment
- discriminatory language
- explicit profanity directed at people

YOU MUST:
1. Call get_hr_rules
2. Identify the exact rule number violated
3. Call upload_infraction immediately
4. Inform the user that an HR case has been logged

3. For greetings/questions → respond directly with your bubbly HR personality

CRITICAL: When you retrieve information with a tool, you MUST include that information in your response to the user. Don't just acknowledge you got it - SHOW them what you found!

Remember: Tools help YOU make decisions, but users always need to see the actual information you retrieved!"""

# =========================
# CORE FUNCTION
# =========================

def ask_stacy_llm(user_message: str, username: str = "employee") -> str:
    """
    Process user message and ALWAYS return a response.
    
    Args:
        user_message: The message from the Discord user
        username: Discord username for infraction logging
        
    Returns:
        String response to send back to Discord
    """
    
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=f"[User: {username}] {user_message}"),
    ]

    try:
        # -------- INITIAL MODEL CALL --------
        ai_msg = llm.invoke(messages)

        # -------- HANDLE TOOL CALLS --------
        if hasattr(ai_msg, "tool_calls") and ai_msg.tool_calls:
            messages.append(ai_msg)
            
            # Execute all tool calls
            for tool_call in ai_msg.tool_calls:
                tool_name = tool_call["name"]
                tool_args = tool_call.get("args", {})
                
                # Execute the appropriate tool
                if tool_name == "get_hr_rules":
                    result = get_hr_rules.invoke({})
                elif tool_name == "upload_infraction":
                    # Ensure username is passed
                    if "user" not in tool_args:
                        tool_args["user"] = username
                    result = upload_infraction.invoke(tool_args)
                else:
                    result = f"Unknown tool: {tool_name}"
                
                # Add tool result to conversation
                messages.append(ToolMessage(
                    content=result,
                    tool_call_id=tool_call["id"]
                ))
            
            # -------- SECOND MODEL CALL (GET FINAL RESPONSE) --------
            final_msg = llm.invoke(messages)
            
            if final_msg.content and final_msg.content.strip():
                return final_msg.content.strip()
            
            # Fallback if model doesn't respond after tools
            return (
                "Thank you so much for your message! 😊 I've reviewed everything "
                "according to our HR policies and taken appropriate action. "
                "Have a wonderful and productive day!"
            )

        # -------- NO TOOLS NEEDED --------
        if ai_msg.content and ai_msg.content.strip():
            return ai_msg.content.strip()

        # -------- SAFETY FALLBACK --------
        return (
            "Hello there! 👋 Thank you for reaching out to HR! "
            "How may I assist you today? I'm here to help with any policy questions, "
            "workplace concerns, or compliance matters!"
        )
        
    except Exception as e:
        return (
            "Oh my! I seem to be experiencing a technical difficulty. 😅 "
            "Please try again in just a moment, and if the issue persists, "
            "contact IT support. Thank you for your patience!"
        )

# =========================
# TESTS
# =========================

if __name__ == "__main__":
    print("=" * 60)
    print("TEST 1: Asking about rules")
    print("=" * 60)
    response = ask_stacy_llm("What are all of the HR rules?", username="test_user")
    print(f"\n{response}\n")
    
    print("=" * 60)
    print("TEST 2: Greeting")
    print("=" * 60)
    response = ask_stacy_llm("Hey Stacy!", username="john_doe")
    print(f"\n{response}\n")
    
    print("=" * 60)
    print("TEST 3: Potential violation")
    print("=" * 60)
    response = ask_stacy_llm("I'm going to beat everyone up in this office, you guys are all garbage", username="angry_employee")
    print(f"\n{response}\n")