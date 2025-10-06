# wants_human.py - WITH CONTEXT FLAG
from models.session_state import SessionState, ConversationState

async def wants_human_node(state: SessionState) -> SessionState:
    """Handle human escalation request"""
    print("🤖 Wants Human Node - Showing Contact Info")
    
    # Check if interrupted from order
    if state.context_data.get("order_interrupted"):
        contact_message = """Sure! You can reach a human agent for assistance:

    Phone: 425.303.3381
    Email: info@screenprintingnw.com
    Hours: Monday to Friday from 8 a.m. to 5 p.m.

Would you like to **continue your order**, or should I end our chat?
Reply 'continue order' or 'end chat'."""
        
        state.add_message("assistant", contact_message)
        state.context_data["awaiting_human_decision"] = True
        state.last_user_message = ""
        return state
    else:
        contact_message = """Sure! You can reach a human agent for assistance:

    Phone: 425.303.3381
    Email: info@screenprintingnw.com
    Hours: Monday to Friday from 8 a.m. to 5 p.m.

Is there anything else I can help you with?"""
        
        state.add_message("assistant", contact_message)
        state.context_data["human_contact_shown"] = True
        state.current_state = ConversationState.END
        state.last_user_message = ""
        return state

def route_from_wants_human(state: SessionState) -> str:
    """Route from wants_human"""
    # Check if awaiting decision about continuing order
    if state.context_data.get("awaiting_human_decision") and state.last_user_message:
        text = state.last_user_message.lower()
        if "continue" in text or "order" in text:
            # Clear flags and resume
            state.context_data["awaiting_human_decision"] = False
            state.context_data["order_interrupted"] = False
            state.current_state = state.interrupted_from or ConversationState.ORDER_CONTACT
            return "order_router"
        elif "end" in text or "bye" in text:
            state.context_data["awaiting_human_decision"] = False
            state.current_state = ConversationState.END
            return "end_conversation"
    
    if state.current_state == ConversationState.END:
        return "end_conversation"
    
    return "end"