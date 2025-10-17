# wants_human.py
from models.session_state import SessionState, ConversationState

async def wants_human_node(state: SessionState) -> SessionState:
    """Handle human escalation request"""
    print("🤖 Wants Human Node - Showing Contact Info")
    
    # First time - show contact info and options
    if not state.context_data.get("human_contact_shown"):
        contact_message = """Sure! You can reach a human agent for assistance:

📞 Phone: 425.303.3381
📧 Email: info@screenprintingnw.com
🕐 Hours: Monday to Friday from 8 a.m. to 5 p.m."""
        
        if state.context_data.get("order_interrupted"):
            combined_message = contact_message + "\n\nWould you like to **continue your order** where you left off, or **end** the conversation?"
        else:
            combined_message = contact_message + "\n\nWould you like to **continue chatting**, or **end** the conversation?"
        
        state.add_message("assistant", combined_message)
        
        state.context_data["human_contact_shown"] = True
        state.last_user_message = ""
        return state
    
    # Handle user's choice
    if state.last_user_message:
        txt = state.last_user_message.strip().lower()
        
        if state.context_data.get("order_interrupted"):
            # Interrupted case: continue order or end
            if any(word in txt for word in ["continue", "order", "resume", "yes", "left", "off"]):
                # Resume order
                resume_state = state.interrupted_from or ConversationState.ORDER_CONTACT
                
                # Reset the question flag
                flag_map = {
                    ConversationState.ORDER_CONTACT: "contact_question_shown",
                    ConversationState.ORDER_ORGANIZATION: "org_question_shown",
                    ConversationState.ORDER_TYPE: "type_question_shown",
                    ConversationState.ORDER_BUDGET: "budget_question_shown",
                    ConversationState.ORDER_SERVICE: "service_question_shown",
                    ConversationState.ORDER_APPAREL: "apparel_question_shown",
                    ConversationState.ORDER_PRODUCT: "product_question_shown",
                    ConversationState.ORDER_LOGO: "logo_question_shown",
                    ConversationState.ORDER_DECORATION_LOCATION: "decoration_location_shown",
                    ConversationState.ORDER_DECORATION_COLORS: "decoration_colors_shown",
                    ConversationState.ORDER_QUANTITY: "qty_question_shown",
                    ConversationState.ORDER_SIZES: "sizes_question_shown",
                    ConversationState.ORDER_DELIVERY: "delivery_question_shown",
                }
                
                flag = flag_map.get(resume_state)
                if flag:
                    state.context_data[flag] = False
                
                # Clear interrupt flags
                state.context_data["order_interrupted"] = False
                state.interrupted_from = None
                
                state.current_state = resume_state
                state.last_user_message = "__RESUME__"
                
                return state
            
            elif any(word in txt for word in ["end", "bye", "goodbye", "done", "finish", "no"]):
                state.current_state = ConversationState.END
                state.add_message(
                    "assistant",
                    content="Thank you for chatting with us! Feel free to come back anytime. Have a great day! 👋"
                )
                state.last_user_message = ""
                return state
            
            else:
                # Didn't understand
                state.add_message(
                    "assistant",
                    content="Please reply:\n• **Continue** to resume your order\n• **End** to finish our conversation"
                )
                state.last_user_message = ""
                return state
        
        else:
            # Non-interrupted case
            if any(word in txt for word in ["continue", "chat", "question", "order", "main", "yes"]):
                state.current_state = ConversationState.MAIN_MENU
                state.context_data = {}  # Clear all context
                state.add_message(
                    "assistant",
                    content="Great! I'm here to help. What would you like to do?"
                )
                state.context_data["main_menu_prompted"] = True
                state.last_user_message = ""
                return state
            
            elif any(word in txt for word in ["end", "bye", "goodbye", "done", "finish", "no"]):
                state.current_state = ConversationState.END
                state.add_message(
                    "assistant",
                    content="Thank you for chatting with us! Feel free to come back anytime. Have a great day! 👋"
                )
                state.last_user_message = ""
                return state
            
            else:
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
