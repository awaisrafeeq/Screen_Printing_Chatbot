
from models.session_state import SessionState, ConversationState

async def welcome_node(state: SessionState) -> SessionState:
    """Welcome node - shows greeting and transitions to main menu"""
    print("🤖 Welcome Node")
    
    welcome_message = "Hey there! I'd love to help with any questions you have. I can also help you place a quote request if you want pricing.\n\nHow can I help you?"
    
    state.add_message("assistant", welcome_message)
    state.current_state = ConversationState.MAIN_MENU
    
    return state

