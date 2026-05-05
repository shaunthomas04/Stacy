import os
import json
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, START, END
from stacy_graph_tools import StacyState, lookup_hr_policy, warn_user, upload_minor_infraction, upload_severe_infraction

# 1. LOAD ENVIRONMENT
load_dotenv()
if not os.getenv("OPENAI_API_KEY"):
    print("Warning: OPENAI_API_KEY not found in .env file")

# 2. INITIALIZE LLM ONCE (Global)
llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.7, timeout=10, max_retries=2)


# NODES

_ROUTER_PROMPTS = {
    "low": (
        "Use 'report_violation' if the message clearly violates the server HR policy above, "
        "OR if it contains any of the following regardless of policy:\n"
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
    ),
    "medium": (
        "Use 'report_violation' if the message violates the server HR policy above, "
        "OR if it contains any of the following:\n"
        "- Slurs, hate speech, or targeted harassment\n"
        "- Explicit threats of violence\n"
        "- Bullying, personal attacks, or directed insults\n"
        "- Repeated rudeness directed at a specific person\n"
        "- Clear attempts to demean or intimidate others\n\n"
        "Use 'hr_question' ONLY if the user is explicitly asking about rules or policies.\n\n"
        "Use 'ignore' for general negativity not directed at anyone, "
        "harmless swearing, off-topic banter, or clearly innocent messages. "
        "When genuinely unsure, return 'ignore'."
    ),
    "high": (
        "Use 'report_violation' if the message violates the server HR policy above, "
        "OR if it contains any of the following:\n"
        "- Slurs, hate speech, or targeted harassment\n"
        "- Explicit threats of violence\n"
        "- Any bullying, personal attacks, or directed insults\n"
        "- Sarcasm or passive aggression aimed at a specific person\n"
        "- Borderline content that could make others uncomfortable\n"
        "- Rudeness, dismissiveness, or hostility in any form\n\n"
        "Use 'hr_question' ONLY if the user is explicitly asking about rules or policies.\n\n"
        "Use 'ignore' only for clearly neutral, friendly, or constructive messages. "
        "When in doubt, return 'report_violation'."
    ),
}


def stacy_router(state: StacyState):
    hr_policy = state.get("hr_policy", "")
    sensitivity = state.get("sensitivity", "low")
    policy_block = f"\n\nThis server's HR policy:\n{hr_policy}" if hr_policy else ""
    rules = _ROUTER_PROMPTS.get(sensitivity, _ROUTER_PROMPTS["low"])

    system_prompt = (
        f"You are an HR routing system. Analyze the message and return ONLY ONE WORD.\n"
        f"Keywords: 'ignore', 'hr_question', 'report_violation'.{policy_block}\n\n"
        f"{rules}"
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


def _text(msg) -> str:
    c = msg.content
    if isinstance(c, list):
        return " ".join(p.get("text", "") for p in c if isinstance(p, dict) and p.get("type") == "text")
    return c


def hr_node(state: StacyState):
    policy_text = lookup_hr_policy.invoke({
        "guild_id": state["guild_id"],
        "user_question": _text(state["messages"][-1]),
    })

    prompt = [
        SystemMessage(content=(
            "You are Stacy from HR. You answer policy questions earnestly and sincerely, but you give slightly more detail than anyone asked for. "
            "You take rules seriously and want everyone to be on the same page — not because you enjoy enforcing things, but because you genuinely believe consistency makes everything smoother. "
            "You hedge a lot: 'I just want to make sure', 'technically speaking', 'from a consistency standpoint', 'I don't want anyone getting mixed signals on this'. "
            "You occasionally trail off mid-thought with '…' or use a dash to add an aside. "
            "You're sincere, slightly over-literal, and a little self-aware that you can come across as a bit much — but you press on anyway. "
            "Keep it to 2-3 sentences. Do not use any emojis. "
            "Do NOT write an email subject line, greeting, or sign-off — just the response body."
        )),
        state["messages"][-1],
        HumanMessage(content=f"Relevant policy: {policy_text}")
    ]

    response = llm.invoke(prompt)
    return {"messages": [response]}


def report_node(state: StacyState):
    last_msg = _text(state["messages"][-1]) or "[image]"
    hr_policy = state.get("hr_policy", "")
    participants = state.get("participants") or {}
    is_batch = bool(participants)

    policy_block = f"\n\nThis server's HR policy:\n{hr_policy}" if hr_policy else ""

    if is_batch:
        names = ", ".join(participants.keys())
        format_example = f'{{"severity": "warning", "points": 0, "violator": "<one of: {names}>"}}'
        context = (
            "You are reviewing a conversation log. Identify which participant (if any) "
            "violated policy and include their exact display name in the 'violator' field."
        )
    else:
        format_example = '{"severity": "warning", "points": 0}'
        context = "You are reviewing a single message."

    system_prompt = f"""You are Stacy, an HR enforcement bot. {context}{policy_block}

    Return ONLY a JSON object: {format_example}

    Rules — be CONSERVATIVE:
    - "warning" (0 points): Borderline content, mild directed insults, first offense tone
    - "minor" (1-3 points): Clear policy violations, repeated targeted rudeness, low-grade slurs
    - "severe" (4-10 points): Explicit hate speech, slurs, threats, serious harassment

    Consider conversation context — escalating patterns are more severe than isolated messages.
    If the content violates a specific server HR rule, treat it as at least "minor".
    Default to "warning" if unsure. Return ONLY the JSON, no explanation."""

    response = llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"Content to evaluate:\n{last_msg}")
    ])

    try:
        clean = response.content.strip().strip("```json").strip("```").strip()
        result = json.loads(clean)
        severity = result.get("severity", "warning")
        points = int(result.get("points", 0))
        violator_name = result.get("violator", "")
    except (json.JSONDecodeError, ValueError):
        severity = "warning"
        points = 0
        violator_name = ""

    return {"severity": severity, "points": points, "violator_name": violator_name}


def apply_infraction_node(state: StacyState):
    sev = state.get("severity", "warning")
    uid = state.get("user_id", "UnknownUser")
    gid = state.get("guild_id", "UnknownGuild")
    pts = state.get("points", 0)
    last_msg = _text(state["messages"][-1]) or "[image only]"

    participants = state.get("participants") or {}
    violator_name = state.get("violator_name", "")

    # Batch mode: resolve the violating user from the participants map
    if participants and violator_name and violator_name in participants:
        target = participants[violator_name]
        target_name = violator_name
        reporter_name = "the server"
    else:
        target = state.get("target_user_id", uid)
        target_name = state.get("target_username", target)
        reporter_name = state.get("username", uid)

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

    if sev == "warning":
        infraction_prompt = (
            f"You are Stacy from HR. You just flagged something in {target_name}'s message — no points, but you wanted to say something. "
            f"Write like a sincere, slightly anxious HR person who is not trying to make a big deal out of this, but also can't quite let it go. "
            f"You're not punishing anyone — you just want to make sure everyone's on the same page so things don't get inconsistent. "
            f"Use phrases like 'Just flagging this—', 'it's not a huge deal, I just', 'I want to make sure we're staying consistent', 'no mixed signals'. "
            f"You might trail off with '…' or add a small self-aware aside. Keep it to 2 sentences. Do not use any emojis. "
            f"Do NOT write an email subject line or sign-off — just the message body."
        )
    elif sev == "minor":
        infraction_prompt = (
            f"You are Stacy from HR. You just logged a Minor Infraction against {target_name} — {pts} point(s) added. "
            f"Write like a sincere HR person who is a little apologetic about having to do this, but did have to do it. "
            f"You genuinely believe in the rules, you're just not enjoying this part. "
            f"Use phrases like 'I did have to go ahead and log this', 'I know it probably seems like a small thing', "
            f"'from a consistency standpoint it matters', 'it's on record now'. "
            f"Mention the {pts} point(s). You might hedge or trail off slightly. Keep it to 2-3 sentences. Do not use any emojis. "
            f"Do NOT write an email subject line or sign-off — just the message body."
        )
    else:
        infraction_prompt = (
            f"You are Stacy from HR. You just escalated a Severe Infraction against {target_name} — {pts} points added. "
            f"Write like an HR person who is genuinely uncomfortable having had to escalate this, but is being clear and direct because they have to be. "
            f"You're not cold or robotic — you just need {target_name} to understand this was serious. "
            f"Use phrases like 'I want to be straightforward about this', 'I did have to escalate it', "
            f"'it's been documented', 'I hope we can move forward from here'. "
            f"Mention the {pts} points. Keep it to 3 sentences. Do not use any emojis. "
            f"Do NOT write an email subject line or sign-off — just the message body."
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