# end_conversation.py
from models.session_state import SessionState, ConversationState

async def end_node(state: SessionState) -> SessionState:
    """Handles end conversation requests"""
    print("🤖 End Node - Conversation finished")
    
    # Check if we're ending after order completion
    if state.context_data.get("order_complete_awaiting_next"):
        # Already showed goodbye message in router, don't duplicate
        state.current_state = ConversationState.END
        return state
    
    # Check if we're coming from wants_human (already showed message)
    if state.context_data.get("human_contact_shown"):
        # Don't add another message, the contact info was already shown
        state.current_state = ConversationState.END
        return state
    
    # Only show end message if we haven't shown other end messages
    end_message = "Thank you for your time. If you need further assistance, feel free to reach out anytime."
    state.add_message("assistant", end_message)
    state.current_state = ConversationState.END
    return state
