# main.py — order_router hub so we never execute the wrong step first
import os
import asyncio
from typing import Dict, Any

from langgraph.graph import StateGraph, END
from models.session_state import SessionState, ConversationState
from services.session_manager import SessionManager
from flows.welcome import welcome_node
from flows.main_menu import main_menu_node, route_from_main_menu
from flows.wants_human import wants_human_node, route_from_wants_human
from flows.end_conversation import end_node
from flows.product_questions import product_questions_node, route_from_product_questions  # NEW IMPORT

from flows.order_flow import (
    order_contact_node, order_organization_node, order_type_node,
    order_budget_node, order_service_node, order_apparel_node,
    order_product_node, order_logo_node, order_quantity_node, order_sizes_node,
    order_delivery_node,
    order_delivery_address_node,
    order_summary_node,
    route_order_flow,order_decoration_location_node,order_decoration_colors_node
)

# ---------------------------
# In-memory session manager
# ---------------------------
session_manager = SessionManager()

# ---------------------------
# Dispatcher (“resume”) node
# ---------------------------
def resume_node(state: SessionState) -> SessionState:
    return state

def _is_order_state(state: SessionState) -> bool:
    try:
        name = getattr(state.current_state, "name", str(state.current_state))
    except Exception:
        name = str(state.current_state)
    return isinstance(name, str) and name.startswith("ORDER_")

def route_from_resume(state: SessionState) -> str:
    cs = state.current_state

    # Fresh welcome
    if cs == ConversationState.WELCOME:
        return "main_menu" if state.last_user_message else "welcome"

    # Any order substate → enter via router hub
    if _is_order_state(state):
        return "order_router"

    # Macro states
    if cs == ConversationState.MAIN_MENU:
        return "main_menu"
    if cs == ConversationState.HAS_QUESTIONS_ABOUT_PRODUCT:
        return "product_questions"
    if cs == ConversationState.WANTS_HUMAN:
        return "wants_human"
    if cs == ConversationState.END:
        # Check if user wants to restart after END
        if state.last_user_message:
            text = state.last_user_message.lower()
            if any(word in text for word in ["order", "quote", "restart", "start", "begin"]):
                # User wants to order after ending - check if there's saved data
                if state.context_data.get("order_interrupted") and state.interrupted_from:
                    # Resume incomplete order
                    return "order_router"
                else:
                    # Fresh order
                    return "main_menu"
        return "end_conversation"

    # Fallback
    return "main_menu"

# ---------------------------
# Order router hub
# ---------------------------
def order_router_node(state: SessionState) -> SessionState:
    """No-op; routing is handled by conditional edges with route_order_flow."""
    return state

# ---------------------------
# Graph builder
# ---------------------------
def create_chatbot_graph():
    print("🔧 Creating complete chatbot graph...")
    g = StateGraph(SessionState)

    # Nodes
    # Nodes
    g.add_node("resume", resume_node)
    g.add_node("welcome", welcome_node)
    g.add_node("main_menu", main_menu_node)
    g.add_node("product_questions", product_questions_node)  # NEW NODE
    g.add_node("wants_human", wants_human_node)
    g.add_node("end_conversation", end_node)

    # Order router + order steps
    g.add_node("order_router", order_router_node)
    g.add_node("order_contact", order_contact_node)
    g.add_node("order_organization", order_organization_node)
    g.add_node("order_type", order_type_node)
    g.add_node("order_budget", order_budget_node)
    g.add_node("order_service", order_service_node)
    g.add_node("order_apparel", order_apparel_node)
    g.add_node("order_product", order_product_node)
    g.add_node("order_logo", order_logo_node)
    g.add_node("order_decoration_location", order_decoration_location_node)
    g.add_node("order_decoration_colors", order_decoration_colors_node)
    g.add_node("order_quantity", order_quantity_node)
    g.add_node("order_sizes", order_sizes_node)
    g.add_node("order_delivery", order_delivery_node)
    # If you don't have this function defined, comment out both the add_node and mappings mentioning it
    g.add_node("order_delivery_address", order_delivery_address_node)
    g.add_node("order_summary", order_summary_node)

    # Entry point → dispatcher
    g.set_entry_point("resume")

    # Resume routing
    g.add_conditional_edges(
        "resume",
        route_from_resume,
        {
            "welcome": "welcome",
            "main_menu": "main_menu",
            "product_questions": "product_questions",  # NEW
            "wants_human": "wants_human",
            "end_conversation": "end_conversation",
            "order_router": "order_router",
        },
    )

    # After welcome, go to main menu (greeting already sent by welcome node)
    g.add_edge("welcome", "main_menu")

    # Main menu routing → enter order via router (not directly to a step)
    g.add_conditional_edges(
        "main_menu",
        route_from_main_menu,
        {
            "product_questions": "product_questions",  # NEW
            "wants_human": "wants_human",
            "end_conversation": "end_conversation",
            "order_contact": "order_router",
            "end": END,
        },
    )

    # Product questions routing
    g.add_conditional_edges(
        "product_questions",
        route_from_product_questions,
        {
            "main_menu": "main_menu",
            "order_router": "order_router",  # NEW: Allow returning to order
            "end": END,
        },
    )

    # Wants human
    g.add_conditional_edges(
        "wants_human",
        route_from_wants_human,
        {
            "wants_human": "wants_human",
            "main_menu": "main_menu",
            "end_conversation": "end_conversation",
        },
    )

    # Order router decides the next step or pause
    flow_mapping = {
        "order_contact": "order_contact",
        "order_organization": "order_organization",
        "order_type": "order_type",
        "order_budget": "order_budget",
        "order_service": "order_service",
        "order_apparel": "order_apparel",
        "order_product": "order_product",
        "order_logo": "order_logo",
        "order_decoration_location": "order_decoration_location",  # ADD
        "order_decoration_colors": "order_decoration_colors",        
        "order_quantity": "order_quantity",
        "order_sizes": "order_sizes",
        "order_delivery": "order_delivery",
        "order_delivery_address": "order_delivery_address",
        "order_summary": "order_summary",
        "wants_human": "wants_human",
        "end_conversation": "end_conversation",
        "end": END,  # pause & wait for next user message
    }
    g.add_conditional_edges("order_router", route_order_flow, flow_mapping)

    # After each order node executes, jump back to the router hub
    for step in [
        "order_contact", "order_organization", "order_type", "order_budget",
        "order_service", "order_apparel", "order_product","order_logo", "order_decoration_location", 
        "order_decoration_colors","order_quantity", "order_sizes", 
        "order_delivery", "order_delivery_address",
    ]:
        g.add_edge(step, "order_router")

    # Terminal: end_conversation → END
    g.add_edge("order_summary", END)

    app = g.compile()
    print("✅ Complete chatbot graph created successfully!")
    return app

# ---------------------------
# Orchestrator
# ---------------------------
class ScreenPrintingChatbot:
    def __init__(self):
        self.app = create_chatbot_graph()

    async def chat(self, session_id: str, user_message: str) -> Dict[str, Any]:
        state = session_manager.get_session(session_id)

        if user_message:
            state.add_message("user", user_message)
            state.last_user_message = user_message

        print(f"\n💬 Processing message: '{user_message}'")
        print(f"   Session: {session_id}")
        print(f"   Current state: {state.current_state}")

        try:
            result = await self.app.ainvoke(
                state,
                config={
                    "configurable": {"thread_id": session_id},
                    "recursion_limit": 50,  # optional safety margin
                },
            )
            final_state = SessionState(**dict(result)) if not isinstance(result, SessionState) else result
            session_manager.update_session(final_state)

            replies = [m for m in final_state.conversation_history if m["role"] == "assistant"]
            latest = replies[-1]["content"] if replies else "..."
            return {
                "success": True,
                "response": latest,
                "session_id": session_id,
                "current_state": getattr(final_state.current_state, "value", str(final_state.current_state)),
                "classified_intent": getattr(final_state.classified_intent, "value", None) if final_state.classified_intent else None,
                "conversation_ended": final_state.current_state == ConversationState.END,
            }
        except Exception as e:
            print(f"❌ Error processing message: {e}")
            return {
                "success": False,
                "response": "I'm experiencing technical difficulties. Please try again.",
                "error": str(e),
                "session_id": session_id,
            }

# ---------------------------
# CLI for manual testing
# ---------------------------
async def interactive_chat():
    print("🚀 Screen Printing NW Chatbot - Interactive Mode")
    print("=" * 50)
    print("Type 'quit' to exit\n")

    bot = ScreenPrintingChatbot()

    # Kick off a welcome session (auto-greeting)
    print("🤖 Starting conversation...")
    session_id = "welcome_session"
    res = await bot.chat(session_id, "")
    if res["success"]:
        print(f"🤖 Bot: {res['response']}\n")

    # Main test session
    test_session_id = "test_session_001"
    while True:
        try:
            user_input = input("👤 You: ").strip()
            if user_input.lower() in {"quit", "exit", "stop"}:
                print("👋 Goodbye!")
                break

            if not user_input:
                print("   (Please type something)")
                continue

            out = await bot.chat(test_session_id, user_input)
            if out["success"]:
                print(f"🤖 Bot: {out['response']}")
                print(f"   📊 State: {out['current_state']}")
                if out["classified_intent"]:
                    print(f"   🎯 Intent: {out['classified_intent']}")
            else:
                print(f"❌ Error: {out['error']}")
        except KeyboardInterrupt:
            print("\n👋 Goodbye!")
            break

if __name__ == "__main__":
    if not os.getenv("OPENAI_API_KEY"):
        print("⚠️  Please set OPENAI_API_KEY environment variable")
        raise SystemExit(1)
    asyncio.run(interactive_chat())
