from models.session_state import SessionState, ConversationState

async def wants_human_node(state: SessionState) -> SessionState:
    """Handle human escalation request"""
    print("🤖 Wants Human Node - Showing Contact Info")
    
    # First time - show contact info and options
    if not state.context_data.get("human_contact_shown"):
        # Combine both messages into one
        combined_message = """Sure! You can reach a human agent for assistance:

📞 Phone: 425.303.3381
📧 Email: info@screenprintingnw.com
🕐 Hours: Monday to Friday from 8 a.m. to 5 p.m.

What would you like to do next?

- **Continue chatting** - Ask more questions or place an order
- **End** - Finish our conversation"""
        
        state.add_message("assistant", combined_message)
        
        state.context_data["human_contact_shown"] = True
        state.last_user_message = ""
        return state
    
    # Handle user's choice
    if state.last_user_message:
        txt = state.last_user_message.strip().lower()
        
        # User wants to continue chatting
        if any(word in txt for word in ["continue", "chat", "question", "order", "main", "yes"]):
            # Check if they were in the middle of an order
            if state.context_data.get("order_interrupted") and state.interrupted_from:
                # Ask if they want to resume order or go to main menu
                state.add_message(
                    "assistant",
                    content="Would you like to **continue your order** where you left off, or go to the **main menu**?"
                )
                state.context_data["awaiting_resume_choice"] = True
                state.last_user_message = ""
                return state
            else:
                # Go to main menu
                state.current_state = ConversationState.MAIN_MENU
                state.context_data = {}  # Clear all context
                state.add_message(
                    "assistant",
                    content="Great! I'm here to help. What would you like to do?"
                )
                state.context_data["main_menu_prompted"] = True
                state.last_user_message = ""
                return state
        
        # User wants to end
        elif any(word in txt for word in ["end", "bye", "goodbye", "done", "finish", "no"]):
            state.current_state = ConversationState.END
            state.add_message(
                "assistant",
                content="Thank you for chatting with us! Feel free to come back anytime. Have a great day! 👋"
            )
            state.last_user_message = ""
            return state
        
        # Check if choosing between order resume or main menu
        elif state.context_data.get("awaiting_resume_choice"):
            if "order" in txt:
                # Resume order
                resume_state = state.interrupted_from or ConversationState.ORDER_CONTACT
                state.context_data["order_interrupted"] = False
                state.context_data["awaiting_resume_choice"] = False
                state.context_data = {}  # Clear context
                state.current_state = resume_state
                state.last_user_message = "__RESUME__"
                return state
            elif "menu" in txt or "main" in txt:
                # Go to main menu
                state.current_state = ConversationState.MAIN_MENU
                state.context_data = {}  # Clear all context
                state.add_message(
                    "assistant",
                    content="Great! I'm here to help. What would you like to do?"
                )
                state.context_data["main_menu_prompted"] = True
                state.last_user_message = ""
                return state
        
        else:
            # Didn't understand
            state.add_message(
                "assistant",
                content="Please reply:\n• **Continue** to keep chatting\n• **End** to finish our conversation"
            )
            state.last_user_message = ""
            return state
    
    # Waiting for user input
    state.last_user_message = ""
    return state


def route_from_wants_human(state: SessionState) -> str:
    """Route from wants_human"""
    if state.current_state == ConversationState.MAIN_MENU:
        return "main_menu"
    elif state.current_state == ConversationState.END:
        return "end_conversation"
    elif state.current_state != ConversationState.WANTS_HUMAN:
        # Resuming order
        return "order_router"
    
    # If human_contact_shown is True and no message, wait for input
    if state.context_data.get("human_contact_shown") and not state.last_user_message:
        return "end"  # This means END the current processing, wait for input
    
    return "wants_human"  # Stay in node to process
