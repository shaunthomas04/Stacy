import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, START, END
from stacy_graph_tools import StacyState, lookup_hr_policy, determine_severity, warn_user, upload_minor_infraction, upload_severe_infraction

# 1. LOAD ENVIRONMENT
load_dotenv() 
if not os.getenv("OPENAI_API_KEY"):
    print("Warning: OPENAI_API_KEY not found in .env file")

# 2. INITIALIZE LLM ONCE (Global)
# # Using gpt-4o-mini for speed and cost-efficiency
llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.7, timeout=10, max_retries=2)


# 5. NODES

def stacy_router(state: StacyState):
    system_prompt = (
        "You are an HR routing system. Analyze the message and return ONLY ONE WORD.\n"
        "Keywords: 'ignore', 'hr_question', 'report_violation'.\n"
        "If the user is asking about rules, use 'hr_question'.\n"
        "If the user is reporting bad behavior or being abusive, use 'report_violation'.\n"
        "Otherwise, use 'ignore'."
    )
    
    try:
        response = llm.invoke([SystemMessage(content=system_prompt)] + state["messages"])
        content = response.content.lower().strip()
        
        # Exact matching to prevent logic slips
        if "hr_question" in content:
            return "hr_question"
        elif "report" in content or "violation" in content or "slur" in content:
            return "report_violation"
        else:
            return "ignore"
            
    except Exception as e:
        print(f"!!! Stacy Router Error (Timeout or API Issue): {e}")
        return "ignore" # Default to ignore so the script keeps running

def hr_node(state: StacyState):
    policy_text = lookup_hr_policy.invoke({"user_question": state["messages"][-1].content})
    
    # Let the LLM "be" Stacy
    prompt = [
        SystemMessage(content="You are Stacy, a helpful but slightly sassy HR bot. "
                              "Explain this policy to the user in a friendly way."),
        state["messages"][-1], # The user's question
        HumanMessage(content=f"Context from HR Handbook: {policy_text}")
    ]
    
    response = llm.invoke(prompt)
    return {"messages": [response]}

def report_node(state: StacyState):
    """Determines how serious a violation is."""
    severity = determine_severity.invoke(state["messages"][-1].content)
    return {"severity": severity}

def apply_infraction_node(state: StacyState):
    """
    Applies the consequence and sends a formal bot response 
    to the user about their violation.
    """
    sev = state.get("severity", "warning")
    uid = state.get("user_id", "UnknownUser")
    last_msg = state["messages"][-1].content
    
    # 1. Execute the internal tool/DB upload
    if sev == "warning":
        action_taken = "issued a formal warning"
        tool_result = warn_user.invoke(last_msg)
    elif sev == "minor":
        action_taken = "recorded a Minor Infraction (1 point)"
        tool_result = upload_minor_infraction.invoke({"user_id": uid})
    else:
        action_taken = "recorded a Severe Infraction (5 points) and opened a forum investigation"
        tool_result = upload_severe_infraction.invoke({"user_id": uid})

    # 2. Craft the public-facing Bot response
    infraction_prompt = (
        f"You are Stacy, a strict but professional HR bot. You just {action_taken} "
        f"against @{uid}. Tell them what happened, why it's bad, and use a "
        f"{'gentle' if sev == 'warning' else 'stern'} tone. Include emojis."
    )

    response = llm.invoke([SystemMessage(content=infraction_prompt)] + state["messages"])
    return {"messages": [response]}

def silent_ignore_node(state: StacyState):
    """Explicit node for the 'Stacy Ignore' path to prevent graph hanging."""
    return {"messages": [HumanMessage(content="[Stacy has no response for this message]", name="Stacy")]}

# 6. GRAPH CONSTRUCTION

workflow = StateGraph(StacyState)

# Add functional nodes
workflow.add_node("process_hr", hr_node)
workflow.add_node("process_report", report_node)
workflow.add_node("apply_infraction", apply_infraction_node)
workflow.add_node("silent_ignore", silent_ignore_node)

# START logic with Conditional Branching
workflow.add_conditional_edges(
    START,
    stacy_router,
    {
        "hr_question": "process_hr",
        "report_violation": "process_report",
        "ignore": "silent_ignore"
    }
)

# Infraction sequence
workflow.add_edge("process_report", "apply_infraction")

# All nodes converge to END
workflow.add_edge("process_hr", END)
workflow.add_edge("apply_infraction", END)
workflow.add_edge("silent_ignore", END)

# Compile the application
app = workflow.compile()

if __name__ == "__main__":
    test_scenarios = [
        {
            "name": "Scenario 1: Policy Inquiry (HR Flow)",
            "user_id": "shaun_dev",
            "message": "Hey Stacy, what is the official policy for leaving the office early on Fridays?"
        },
        {
            "name": "Scenario 2: Reporting Someone Else (Third-Party Report)",
            "user_id": "manager_tom",
            "message": "I need to report a violation: User 'BadActor42' just used a racial slur in the general chat."
        },
        {
            "name": "Scenario 3: Self-Violation (Direct Write-up)",
            "user_id": "troll_user",
            "message": "I don't care about the rules, you are all total [slur]!"
        },
        {
            "name": "Scenario 4: Intentional Ignore (Noise Filter)",
            "user_id": "shaun_dev",
            "message": "Does anyone know if the breakroom has more oat milk? Also the weather is great."
        },
        {
            "name": "Scenario 5: Ambiguous/Edge Case (Stress Test)",
            "user_id": "confused_emp",
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
            "user_id": scenario['user_id']
        }

        # Stream the graph execution
        for output in app.stream(inputs):
            for node_name, data in output.items():
                if "messages" in data:
                    # Get the last message content
                    content = data['messages'][-1].content
                    print(f"[{node_name}] -> {content}")
                elif "severity" in data:
                    print(f"[{node_name}] -> Severity Determined: {data['severity'].upper()}")
        
        print("="*40)