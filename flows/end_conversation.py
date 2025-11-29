from models.session_state import SessionState, ConversationState

async def end_node(state: SessionState) -> SessionState:
    print("🤖 End Node - Conversation finished")
    
    if state.context_data.get("order_complete_awaiting_next"):
        state.current_state = ConversationState.END
        return state
    
    if state.context_data.get("human_contact_shown"):
        state.current_state = ConversationState.END
        return state
    
    end_message = "Thank you for your time. If you need further assistance, feel free to reach out anytime."
    state.add_message("assistant", end_message)
    state.current_state = ConversationState.END
    return state
