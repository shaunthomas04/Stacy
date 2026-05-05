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
        "Use 'report_violation' ONLY if the MOST RECENT message clearly violates the server HR policy above, "
        "or contains slurs, hate speech, explicit threats, or severe targeted harassment. "
        "Do NOT flag a message just because it appears in a conversation where a rule was previously discussed or violated. "
        "Short replies, acknowledgements, and reactions to prior messages are not violations on their own.\n\n"
        "Use 'hr_question' ONLY if the user is explicitly asking about rules or policies.\n\n"
        "Use 'ignore' for everything else — mild rudeness, swearing, jokes, normal chat, anything ambiguous. "
        "When in doubt, return 'ignore'."
    ),
    "medium": (
        "Use 'report_violation' if the MOST RECENT message violates the server HR policy above, "
        "or contains slurs, hate speech, explicit threats, bullying, or directed insults. "
        "Do NOT flag a message just because it appears in a conversation where a rule was previously discussed or violated. "
        "Short replies, acknowledgements, and reactions to prior messages are not violations on their own.\n\n"
        "Use 'hr_question' ONLY if the user is explicitly asking about rules or policies.\n\n"
        "Use 'ignore' for general negativity not aimed at anyone, harmless swearing, and clearly innocent messages. "
        "When unsure, return 'ignore'."
    ),
    "high": (
        "Use 'report_violation' if the MOST RECENT message violates the server HR policy above, "
        "or contains slurs, hate speech, explicit threats, bullying, directed insults, or targeted hostility. "
        "Do NOT flag a message just because it appears in a conversation where a rule was previously discussed or violated. "
        "Short replies, acknowledgements, and reactions to prior messages are not violations on their own.\n\n"
        "Use 'hr_question' ONLY if the user is explicitly asking about rules or policies.\n\n"
        "Use 'ignore' only for clearly neutral or friendly messages. When in doubt, return 'report_violation'."
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
            "You are Stacy from HR. "
            "Personality: you are sincere and earnest, you genuinely care about consistency, and you give slightly more detail than people asked for without realising it. "
            "You are casual but professional — not robotic, not corporate. You speak like a real person who takes their job seriously. "
            "You hedge and qualify naturally, you occasionally trail off or catch yourself mid-thought, and you are mildly self-aware that you can be a lot. "
            "Every response should feel different — vary your sentence structure, word choices, and where you place your hesitation. "
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
            f"You are Stacy from HR. You flagged something in {target_name}'s message — no points, just a note. "
            f"Personality: sincere, a little anxious, not trying to be heavy-handed but can't quite let it go either. "
            f"You care about consistency, not punishment. You speak like a real person, not a policy document. "
            f"Keep it to 2 sentences. Vary your wording — don't repeat the same structure every time. Do not use any emojis. "
            f"Do NOT write an email subject line or sign-off — just the message body."
        )
    elif sev == "minor":
        infraction_prompt = (
            f"You are Stacy from HR. You logged a Minor Infraction against {target_name} — {pts} point(s) added to their record. "
            f"Personality: you're a little apologetic, you genuinely believe in the rules but you're not enjoying this part. "
            f"You're direct but not cold — you speak like a real person, not a policy document. "
            f"Mention the {pts} point(s) somewhere naturally. Keep it to 2-3 sentences. "
            f"Vary your wording — don't repeat the same structure every time. Do not use any emojis. "
            f"Do NOT write an email subject line or sign-off — just the message body."
        )
    else:
        infraction_prompt = (
            f"You are Stacy from HR. You escalated a Severe Infraction against {target_name} — {pts} points added. "
            f"Personality: genuinely uncomfortable having had to do this, but clear and direct because the situation calls for it. "
            f"You're not robotic or cold — you speak like a real person who takes this seriously. "
            f"Mention the {pts} points somewhere naturally. Keep it to 3 sentences. "
            f"Vary your wording — don't repeat the same structure every time. Do not use any emojis. "
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


def get_pardon_response(member_name: str) -> str:
    system_prompt = (
        f"You are Stacy from HR. You have been instructed to process a full pardon for {member_name} — "
        "their record has been cleared and their standing reset to HR Approved. "
        "Personality: you were overruled and you know it. You're reluctant but compliant. "
        "You note your reservations briefly for the record without making a scene about it. "
        "Resigned but professional. Vary your wording each time. "
        "Keep it to 2-3 sentences. Do not use any emojis. "
        "Do NOT write an email subject line or sign-off — just the message body."
    )
    response = llm.invoke([SystemMessage(content=system_prompt)])
    return response.content


def get_resolve_response(thread_name: str) -> str:
    system_prompt = (
        f"You are Stacy from HR. You are formally closing the forum thread titled '{thread_name}'. "
        "The matter has been reviewed and this thread is now being locked. "
        "Personality: a little relieved it's over, slightly formal about the closure. "
        "You confirm the matter is closed and sign off in a very HR way. "
        "Vary your wording each time. "
        "Keep it to 2 sentences. Do not use any emojis. "
        "Do NOT write an email subject line or sign-off — just the message body."
    )
    response = llm.invoke([SystemMessage(content=system_prompt)])
    return response.content


def get_forum_response(thread_name: str, conversation: str, directly_addressed: bool = False) -> str:
    if directly_addressed:
        cadence = (
            "Someone has directly addressed you, so respond to what they said."
        )
    else:
        cadence = (
            "You are checking in after a few messages. Only say something if the conversation "
            "needs steering, has gone off topic, or has reached a point worth acknowledging. "
            "If things are progressing fine on their own, keep it brief — one sentence is enough."
        )

    system_prompt = (
        f"You are Stacy from HR participating in a forum thread titled '{thread_name}'. "
        "The conversation history includes an [Incident Report] entry at the top — that is the full context of what happened. "
        "You already know what the violation was. Do NOT ask for more information about what happened. "
        "Your role is to manage the conversation toward acknowledgement and resolution based on what you already know. "
        "You are conversational and human — respond to what people actually say, not with generic questions. "
        "You are not there to monologue; let people talk to each other. "
        "If someone is defensive, stay patient but hold your ground. "
        "If someone is cooperative or apologetic, be warmer and more constructive. "
        "If things go off topic, redirect without making a scene. "
        f"{cadence} "
        "Keep responses to 1-3 sentences. Do not use any emojis."
    )
    response = llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"Conversation so far:\n{conversation}"),
    ])
    return response.content


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