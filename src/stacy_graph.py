import json
import logging
from typing import Literal

from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import StateGraph, START, END
from pydantic import BaseModel, Field

import config  # noqa: F401 — importing loads/validates env vars (incl. OPENAI_API_KEY) once
from stacy_graph_tools import StacyState, lookup_hr_policy, warn_user, upload_minor_infraction, upload_severe_infraction

logger = logging.getLogger(__name__)

# INITIALIZE LLM ONCE (Global)
llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.7, timeout=10, max_retries=2)


def _safe_invoke(messages: list, fallback: str) -> str:
    """Invokes the LLM and returns its text, or `fallback` if the call fails
    (timeout, API error, etc.) so a bad request never leaves the user with silence."""
    try:
        return llm.invoke(messages).content
    except Exception as e:
        logger.error(f"Stacy LLM call failed: {e}")
        return fallback


def _text(msg) -> str:
    c = msg.content
    if isinstance(c, list):
        return " ".join(p.get("text", "") for p in c if isinstance(p, dict) and p.get("type") == "text")
    return c


# ROUTER

class RouterDecision(BaseModel):
    action: Literal["ignore", "hr_question", "report_violation"] = Field(
        description="How to handle the most recent message."
    )


_router_llm = llm.with_structured_output(RouterDecision)

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
        f"You are an HR routing system. Decide how to handle the most recent message.{policy_block}\n\n{rules}"
    )

    try:
        decision = _router_llm.invoke([SystemMessage(content=system_prompt)] + state["messages"])
        return decision.action
    except Exception as e:
        logger.error(f"Stacy router error (timeout or API issue): {e}")
        return "ignore"


# HR QUESTIONS

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

    content = _safe_invoke(
        prompt,
        "Sorry — I'm having a little trouble pulling that up right now. Can you try asking again in a moment?"
    )
    return {"messages": [AIMessage(content=content)]}


# VIOLATIONS — one LLM call classifies severity AND writes Stacy's reply,
# instead of two sequential calls. The DB write itself needs no LLM at all.

class InfractionAssessment(BaseModel):
    severity: Literal["warning", "minor", "severe"] = Field(
        description="warning = borderline/first-offense (0 points); "
                     "minor = clear violation (1-3 points); "
                     "severe = hate speech/threats/serious harassment (4-10 points)"
    )
    points: int = Field(ge=0, le=10, description="Points to add. Must be 0 for 'warning'.")
    violator: str = Field(
        default="",
        description="Exact display name of the violating participant. Only set in batch/conversation mode."
    )
    message: str = Field(description="Stacy's in-character reply to post, matching the chosen severity's tone.")


_infraction_llm = llm.with_structured_output(InfractionAssessment)

_FALLBACK_INFRACTION_MESSAGE = (
    "I flagged something here but I'm having trouble putting it into words right now — it's on record, "
    "we'll sort the details out later."
)


def report_and_reply_node(state: StacyState):
    hr_policy = state.get("hr_policy", "")
    participants = state.get("participants") or {}
    is_batch = bool(participants)
    policy_block = f"\n\nThis server's HR policy:\n{hr_policy}" if hr_policy else ""

    if is_batch:
        names = ", ".join(participants.keys())
        context = "You are reviewing a conversation log."
        batch_instructions = (
            f"\n\nIdentify which participant (if any) violated policy and put their exact display "
            f"name in the 'violator' field (one of: {names}). Address them by name in your reply if relevant."
        )
    else:
        context = "You are reviewing a single message."
        batch_instructions = ""

    system_prompt = f"""You are Stacy, an HR enforcement bot. {context}{policy_block}

First decide the severity, then write Stacy's in-character reply to match it:

- "warning" (0 points): borderline content, mild directed insults, first-offense tone.
  Reply as Stacy: sincere, a little anxious, not trying to be heavy-handed but can't quite let it go either. She cares about consistency, not punishment. Speak like a real person, not a policy document. 2 sentences.
- "minor" (1-3 points): clear policy violations, repeated targeted rudeness, low-grade slurs.
  Reply as Stacy: a little apologetic, she genuinely believes in the rules but isn't enjoying this part. Direct but not cold. Mention the point total somewhere naturally. 2-3 sentences.
- "severe" (4-10 points): explicit hate speech, slurs, threats, serious harassment.
  Reply as Stacy: genuinely uncomfortable having had to do this, but clear and direct because the situation calls for it. Not robotic or cold. Mention the points somewhere naturally. 3 sentences.

Be CONSERVATIVE. Default to "warning" if unsure. If the content violates a specific server HR rule, treat it as at least "minor". Consider conversation context — escalating patterns are more severe than isolated messages.{batch_instructions}

Vary your wording — don't repeat the same structure every time. Do not use any emojis. Do NOT write an email subject line or sign-off — just the reply body."""

    try:
        assessment = _infraction_llm.invoke([SystemMessage(content=system_prompt)] + state["messages"])
        severity = assessment.severity
        points = assessment.points if severity != "warning" else 0
        violator_name = assessment.violator if is_batch else ""
        reply_text = assessment.message
    except Exception as e:
        logger.error(f"Stacy report/reply error: {e}")
        severity, points, violator_name = "warning", 0, ""
        reply_text = _FALLBACK_INFRACTION_MESSAGE

    uid = state.get("user_id", "UnknownUser")
    gid = state.get("guild_id", "UnknownGuild")
    last_msg = _text(state["messages"][-1]) or "[image only]"

    # Batch mode: resolve the violating user from the participants map
    if participants and violator_name and violator_name in participants:
        target = participants[violator_name]
    else:
        target = state.get("target_user_id", uid)

    if severity == "warning":
        warn_user.invoke({"user_id": target, "guild_id": gid, "message": last_msg})
    elif severity == "minor":
        upload_minor_infraction.invoke({"user_id": target, "guild_id": gid, "message": last_msg, "points": points})
    else:
        upload_severe_infraction.invoke({"user_id": target, "guild_id": gid, "message": last_msg, "points": points})

    return {
        "severity": severity,
        "points": points,
        "violator_name": violator_name,
        "messages": [AIMessage(content=reply_text)],
    }


def silent_ignore_node(state: StacyState):
    """Explicit node for the 'Stacy Ignore' path to prevent graph hanging."""
    return {"messages": [HumanMessage(content="[Stacy has no response for this message]", name="Stacy")]}


# GRAPH CONSTRUCTION

workflow = StateGraph(StacyState)

workflow.add_node("process_hr", hr_node)
workflow.add_node("process_report", report_and_reply_node)
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

workflow.add_edge("process_hr", END)
workflow.add_edge("process_report", END)
workflow.add_edge("silent_ignore", END)

app = workflow.compile()


# STANDALONE LLM HELPERS (appeals, pardons, resolutions, forum participation)

def get_appeal_decision(
    username: str,
    infraction_context: str,
    severity: str,
    points: int,
    appeal_reason: str,
) -> dict:
    max_removable = points
    system_prompt = f"""You are Stacy from HR reviewing a formal appeal from {username}.

Original infraction: {severity} severity, {points} point(s).
Violation: {infraction_context}
Their appeal: {appeal_reason}

Decide whether to uphold or dismiss the appeal. Be fair but thorough.
Genuine remorse, valid context, or mitigating circumstances may reduce or clear points.
Weak, dismissive, or dishonest appeals should be upheld.

Return ONLY a JSON object:
{{"decision": "upheld|partial|dismissed", "points_removed": <0 to {max_removable}>, "message": "<Stacy response>"}}

Rules:
- "upheld": appeal rejected, points_removed must be 0
- "partial": valid mitigating factors, points_removed must be between 1 and {max(1, max_removable - 1)}
- "dismissed": fully accepted, points_removed must equal {max_removable}
- If points is 0, only "upheld" or "dismissed" apply (no partial)
- The message must be in Stacy's voice — earnest HR person, reacts to what they actually said
- 2-3 sentences, no emojis, no sign-off"""

    default = {
        "decision": "upheld",
        "points_removed": 0,
        "message": "Your appeal has been reviewed and the original infraction will stand.",
    }
    try:
        response = llm.invoke([SystemMessage(content=system_prompt)])
        clean = response.content.strip().strip("```json").strip("```").strip()
        result = json.loads(clean)
        return {
            "decision": result.get("decision", "upheld"),
            "points_removed": int(result.get("points_removed", 0)),
            "message": result.get("message", default["message"]),
        }
    except Exception as e:
        logger.error(f"Stacy appeal decision error: {e}")
        return default


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
    return _safe_invoke(
        [SystemMessage(content=system_prompt)],
        f"{member_name}'s record has been cleared and their standing reset, for the record."
    )


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
    return _safe_invoke([SystemMessage(content=system_prompt)], "This matter is now closed.")


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
    # Empty fallback is intentional: handle_forum_message() only replies if response is truthy,
    # so a failed call here just means Stacy stays quiet in the thread instead of erroring out.
    return _safe_invoke(
        [SystemMessage(content=system_prompt), HumanMessage(content=f"Conversation so far:\n{conversation}")],
        ""
    )


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

    print("STARTING STACY AGENT TEST SUITE\n" + "="*40)

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
