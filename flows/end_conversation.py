# end_conversation.py - FIXED VERSION
from models.session_state import SessionState, ConversationState

async def end_node(state: SessionState) -> SessionState:
    """Handles end conversation requests"""
    print("🤖 End Node - Conversation finished")
    
    # Check if we're coming from wants_human (already showed message)
    if state.context_data.get("human_contact_shown"):
        # Don't add another message, the contact info was already shown
        state.current_state = ConversationState.END
        return state
    
    # Only show end message if we haven't shown human contact info
    end_message = "Thank you for your time. If you need further assistance, feel free to reach out anytime."
    state.add_message("assistant", end_message)
    state.current_state = ConversationState.END
    return state