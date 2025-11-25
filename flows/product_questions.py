# product_questions.py
from models.session_state import SessionState, ConversationState, Intent
from flows.rag_system import retrieve_answer
import asyncio
import re

# At the top of product_questions.py, after imports

def _reset_question_flag_for_state(state: SessionState, conv_state: ConversationState):
    """Reset the question_shown flag for a given conversation state"""
    flag_map = {
        ConversationState.ORDER_CONTACT_FIRST_NAME: "contact_first_name_shown",
        ConversationState.ORDER_CONTACT_LAST_NAME: "contact_last_name_shown",
        ConversationState.ORDER_CONTACT_EMAIL: "contact_email_shown",
        ConversationState.ORDER_CONTACT_PHONE: "contact_phone_shown",
        ConversationState.ORDER_ORGANIZATION: ("org_type_shown", "org_name_shown"),  # Reset both for split question        ConversationState.ORDER_TYPE: "type_question_shown",
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
    
    flag = flag_map.get(conv_state)
    if flag:
        state.context_data[flag] = False

async def product_questions_node(state: SessionState) -> SessionState:
    """Handle product-related questions using RAG system"""
    print("🤖 Product Questions Node - Using RAG")
    
    # Check if we just entered this state without a specific question
    if not state.context_data.get("product_question_prompted"):
        # Check if interrupted from order flow
        if state.context_data.get("order_interrupted"):
            # ✅ Message already added in _check_interrupt, just mark as prompted
            state.context_data["product_question_prompted"] = True
        else:
            state.add_message(
                role="assistant",
                content=(
                    "I can help answer questions about our products and services! "
                    "What would you like to know about? For example:\n"
                    "• Pricing and quotes\n"
                    "• Shirt styles and recommendations\n"
                    "• Order minimums and turnaround times\n"
                    "• Screen printing vs embroidery\n"
                    "• Payment and delivery options\n\n"
                    "Just ask your question!"
                ),
            )
            state.context_data["product_question_prompted"] = True
        state.last_user_message = ""
        return state
    
    # If user has asked a question, use RAG to find answer
    if state.last_user_message:
        user_question = state.last_user_message.strip()
        
        # Check for exit keywords
        if any(word in user_question.lower() for word in ["done", "finished", "back", "menu" , "main menu"]):
            # If interrupted from order, ask about resuming
            if state.context_data.get("order_interrupted"):
                state.add_message(
                    role="assistant",
                    content=(
                        "Got it! Would you like to **continue your order** where you left off, "
                        "or return to the **main menu**?\n\n"
                        "Reply:\n"
                        "• **Continue order** - Resume your quote request\n"
                        "• **Main** - For Start fresh"
                    ),
                )
                state.context_data["awaiting_resume_decision"] = True
            else:
                state.add_message(
                    role="assistant",
                    content="Sure! Returning to the main menu. How else can I help you?",
                )
                state.current_state = ConversationState.MAIN_MENU
                state.context_data["product_question_prompted"] = False
            
            state.last_user_message = ""
            return state
        
    # Check if user wants to continue order (after answering questions)

        if state.context_data.get("awaiting_resume_decision"):
            if "continue" in user_question.lower() or "order" in user_question.lower():
                # Get the interrupted state
                resume_state = state.interrupted_from or ConversationState.ORDER_CONTACT
                
                # ✅ Reset the question flag
                _reset_question_flag_for_state(state, resume_state)
                
                # Clear interrupt flags
                state.context_data["order_interrupted"] = False
                state.context_data["awaiting_resume_decision"] = False
                state.context_data["product_question_prompted"] = False
                
                # Set the current state
                state.current_state = resume_state
                state.interrupted_from = None
                
                # ✅ Set a special trigger message that order nodes will ignore
                # This ensures the router sends us to the order node
                state.last_user_message = "__RESUME__"
                
                return state
            
            elif re.search(r"(main\s*menu|menu|main)", user_question.lower()):
                # Ensure we clear all flags and move to main menu
                if state.context_data.get("awaiting_resume_decision"):
                    # Clear any decision flags
                    state.context_data["order_interrupted"] = False
                    state.context_data["awaiting_resume_decision"] = False
                    state.context_data["product_question_prompted"] = False
                    state.interrupted_from = None

                    # Transition to main menu directly
                    state.current_state = ConversationState.MAIN_MENU
                    state.add_message(
                        role="assistant",
                        content="Okay, back to the main menu. How can I help you?",
                    )
                    state.last_user_message = ""  # Clear last message to prevent loops
                    return state

                # If not in the 'awaiting_resume_decision' state, the user is likely still in product questions
                state.add_message(
                    role="assistant",
                    content=(
                        "It seems you want to go back to the **main menu**. Please confirm:\n"
                        "• **Continue order** - Resume your quote request\n"
                        "• **Main ** - For Start fresh"
                    )
                )
                state.context_data["awaiting_resume_decision"] = True  # Set flag to await decision
                state.last_user_message = ""  # Consume the message
                return state
            
        try:
            # Use RAG system to retrieve answer
            answer = await asyncio.to_thread(retrieve_answer, user_question)
            
            # If interrupted from order, remind them they can continue
            follow_up = (
                "\n\nDo you have other questions? "
                "Or say **done** when you're ready for order."
                if state.context_data.get("order_interrupted")
                else "\n\nDo you have any other questions? Or type 'done' to return to the main menu."
            )
            
            state.add_message(
                role="assistant",
                content=f"{answer}{follow_up}",
                metadata={"source": "rag_system", "query": user_question}
            )
        except Exception as e:
            print(f"RAG system error: {e}")
            state.add_message(
                role="assistant",
                content=(
                    "I'm having trouble accessing our FAQ database right now. "
                    "You can ask another question, or I can connect you with a human agent. "
                    "Just say 'human' if you'd prefer that."
                ),
            )
        
        state.last_user_message = ""
        return state
    
    # No message, stay in product questions state
    state.last_user_message = ""
    return state

def route_from_product_questions(state: SessionState) -> str:
    """Route from product questions state"""
    if state.current_state == ConversationState.MAIN_MENU:
        return "main_menu"
    # If resuming order, route back to order_router
    if state.current_state != ConversationState.HAS_QUESTIONS_ABOUT_PRODUCT:
        return "order_router"
    return "end"
