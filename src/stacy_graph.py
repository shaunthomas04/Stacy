import os
import json
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, START, END
from stacy_graph_tools import StacyState, lookup_hr_policy, warn_user, upload_minor_infraction, upload_severe_infraction
from database_functions import upsert_user

# 1. LOAD ENVIRONMENT
load_dotenv()
if not os.getenv("OPENAI_API_KEY"):
    print("Warning: OPENAI_API_KEY not found in .env file")

# 2. INITIALIZE LLM ONCE (Global)
llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.7, timeout=10, max_retries=2)


# 3. SEVERITY → DB ENUM MAP
SEVERITY_MAP = {
    "warning": "Low",
    "minor":   "Medium",
    "severe":  "Critical"
}


# 4. NODES

def stacy_router(state: StacyState):
    system_prompt = (
        "You are an HR routing system. Analyze the message and return ONLY ONE WORD.\n"
        "Keywords: 'ignore', 'hr_question', 'report_violation'.\n\n"
        "Use 'report_violation' ONLY for clear, egregious violations such as:\n"
        "- Slurs, hate speech, or targeted harassment\n"
        "- Explicit threats of violence\n"
        "- Severe bullying or personal attacks\n\n"
        "Use 'hr_question' ONLY if the user is explicitly asking about rules or policies.\n\n"
        "Use 'ignore' for EVERYTHING else, including:\n"
        "- Mild rudeness, sarcasm, or venting\n"
        "- Swearing that isn't directed at anyone\n"
        "- Jokes, memes, or edgy humor\n"
        "- Normal conversation, even if slightly negative\n"
        "- Anything ambiguous or borderline\n\n"
        "When in doubt, ALWAYS return 'ignore'. "
        "It is much better to ignore something borderline than to over-police normal chat."
    )

    try:
        response = llm.invoke([SystemMessage(content=system_prompt)] + state["messages"])
        content = response.content.lower().strip()

        if "hr_question" in content:
            return "hr_question"
        elif "report" in content or "violation" in content or "slur" in content:
            return "report_violation"
        else:
            return "ignore"

    except Exception as e:
        print(f"!!! Stacy Router Error (Timeout or API Issue): {e}")
        return "ignore"


def hr_node(state: StacyState):
    policy_text = lookup_hr_policy.invoke({"user_question": state["messages"][-1].content})

    prompt = [
        SystemMessage(content="You are Stacy, a helpful but slightly sassy HR bot. "
                              "Explain this policy to the user in a friendly way."),
        state["messages"][-1],
        HumanMessage(content=f"Context from HR Handbook: {policy_text}")
    ]

    response = llm.invoke(prompt)
    return {"messages": [response]}


def report_node(state: StacyState):
    last_msg = state["messages"][-1].content

    system_prompt = """You are Stacy, an HR enforcement bot. Analyze this message and return ONLY a JSON object.

    {"severity": "warning", "points": 0}

    Rules — be CONSERVATIVE, most messages should not reach you at all:
    - "warning" (0 points): Borderline content, mild directed insults, first offense tone issues
    - "minor" (1-3 points): Clear policy violations, repeated targeted rudeness, low-grade slurs
    - "severe" (4-10 points): Explicit hate speech, slurs directed at a person or group, threats, serious harassment

    Only assign "severe" for things that would get someone banned in any normal server.
    Only assign "minor" for things that are unambiguously rude or offensive, not just edgy.
    Default to "warning" if you are unsure. Return ONLY the JSON, no explanation."""

    response = llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"Message to evaluate: {last_msg}")
    ])

    try:
        clean = response.content.strip().strip("```json").strip("```").strip()
        result = json.loads(clean)
        severity = result.get("severity", "warning")
        points = int(result.get("points", 0))
    except (json.JSONDecodeError, ValueError):
        severity = "warning"
        points = 0

    return {"severity": severity, "points": points}
    # target_user_id already in state — no need to touch it


def apply_infraction_node(state: StacyState):
    sev = state.get("severity", "warning")
    uid = state.get("user_id", "UnknownUser")           # who sent the message
    target = state.get("target_user_id", uid)           # who actually gets the infraction
    gid = state.get("guild_id", "UnknownGuild")
    pts = state.get("points", 0)
    last_msg = state["messages"][-1].content

    # Auto-register both users before any DB write
    upsert_user(target, gid, target)
    upsert_user(uid, gid, uid)

    if sev == "warning":
        action_taken = "issued a formal warning (0 points)"
        tool_result = warn_user.invoke({
            "user_id": target, "guild_id": gid, "message": last_msg
        })
    elif sev == "minor":
        action_taken = f"recorded a Minor Infraction ({pts} points)"
        tool_result = upload_minor_infraction.invoke({
            "user_id": target, "guild_id": gid, "message": last_msg, "points": pts
        })
    else:
        action_taken = f"recorded a Severe Infraction ({pts} points) and opened a forum investigation"
        tool_result = upload_severe_infraction.invoke({
            "user_id": target, "guild_id": gid, "message": last_msg, "points": pts
        })

    infraction_prompt = (
        f"You are Stacy, a strict but professional HR bot. You just {action_taken} "
        f"against @{target}. Tell @{uid} (who {'reported this' if target != uid else 'did this'}) "
        f"what happened and that {pts} points were added to @{target}'s record. "
        f"Use a {'gentle' if sev == 'warning' else 'stern'} tone. Include emojis."
    )

    response = llm.invoke([SystemMessage(content=infraction_prompt)] + state["messages"])
    return {"messages": [response]}


def silent_ignore_node(state: StacyState):
    """Explicit node for the 'Stacy Ignore' path to prevent graph hanging."""
    return {"messages": [HumanMessage(content="[Stacy has no response for this message]", name="Stacy")]}


# 5. GRAPH CONSTRUCTION

workflow = StateGraph(StacyState)

workflow.add_node("process_hr", hr_node)
workflow.add_node("process_report", report_node)
workflow.add_node("apply_infraction", apply_infraction_node)
workflow.add_node("silent_ignore", silent_ignore_node)

workflow.add_conditional_edges(
    START,
    stacy_router,
    {
        "hr_question": "process_hr",
        "report_violation": "process_report",
        "ignore": "silent_ignore"
    }
)

workflow.add_edge("process_report", "apply_infraction")
workflow.add_edge("process_hr", END)
workflow.add_edge("apply_infraction", END)
workflow.add_edge("silent_ignore", END)

app = workflow.compile()


if __name__ == "__main__":
    test_scenarios = [
        {
            "name": "Scenario 1: Policy Inquiry (HR Flow)",
            "user_id": "shaun_dev",
            "guild_id": "1234567890",
            "target_user_id": "shaun_dev",  # self, no violation
            "message": "Hey Stacy, what is the official policy for leaving the office early on Fridays?"
        },
        {
            "name": "Scenario 2: Reporting Someone Else (Third-Party Report)",
            "user_id": "manager_tom",
            "guild_id": "1234567890",
            "target_user_id": "BadActor42",  # explicitly the offender
            "message": "I need to report a violation: User 'BadActor42' just used a racial slur in the general chat."
        },
        {
            "name": "Scenario 3: Self-Violation (Direct Write-up)",
            "user_id": "troll_user",
            "guild_id": "1234567890",
            "target_user_id": "troll_user",  # they did it themselves
            "message": "I don't care about the rules, you are all total [slur]!"
        },
        {
            "name": "Scenario 4: Intentional Ignore (Noise Filter)",
            "user_id": "shaun_dev",
            "guild_id": "1234567890",
            "target_user_id": "shaun_dev",
            "message": "Does anyone know if the breakroom has more oat milk? Also the weather is great."
        },
        {
            "name": "Scenario 5: Ambiguous/Edge Case (Stress Test)",
            "user_id": "confused_emp",
            "guild_id": "1234567890",
            "target_user_id": "confused_emp",
            "message": "I'm worried that my leave request looks like a violation of policy, can you check?"
        }
    ]

    print("🚀 STARTING STACY AGENT TEST SUITE\n" + "="*40)

    for i, scenario in enumerate(test_scenarios, 1):
        print(f"\nRUNNING {scenario['name']}")
        print(f"User (@{scenario['user_id']}): {scenario['message']}")
        print("-" * 20)

        inputs = {
            "messages": [HumanMessage(content=scenario['message'])],
            "user_id": scenario['user_id'],
            "guild_id": scenario['guild_id'],
            "target_user_id": scenario['target_user_id']  # ← just pass it straight in
        }

        for output in app.stream(inputs):
            for node_name, data in output.items():
                if "messages" in data:
                    content = data['messages'][-1].content
                    print(f"[{node_name}] -> {content}")
                elif "severity" in data:
                    target = data.get("target_user_id", "unknown")
                    print(f"[{node_name}] -> Severity: {data['severity'].upper()} | "
                          f"Points: {data.get('points', 0)} | Target: @{target}")

        print("="*40)