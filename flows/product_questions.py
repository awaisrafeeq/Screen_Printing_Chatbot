# product_questions.py
from models.session_state import SessionState, ConversationState, Intent
from flows.rag_system import retrieve_answer
import asyncio

async def product_questions_node(state: SessionState) -> SessionState:
    """Handle product-related questions using RAG system"""
    print("🤖 Product Questions Node - Using RAG")
    
    # Check if we just entered this state without a specific question
    if not state.context_data.get("product_question_prompted"):
        # Check if interrupted from order flow
        if state.context_data.get("order_interrupted"):
            state.add_message(
                role="assistant",
                content=(
                    "Sure! I'll help answer your product questions. "
                    "What would you like to know?"
                ),
            )
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
        if any(word in user_question.lower() for word in ["done", "finished", "back", "menu"]):
            # If interrupted from order, ask about resuming
            if state.context_data.get("order_interrupted"):
                state.add_message(
                    role="assistant",
                    content=(
                        "Got it! Would you like to **continue your order** where you left off, "
                        "or return to the main menu?\n\n"
                        "Reply:\n"
                        "• **Continue order** - Resume your quote request\n"
                        "• **Main menu** - Start fresh"
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
                
                # Clear interrupt flags FIRST
                state.context_data["order_interrupted"] = False
                state.context_data["awaiting_resume_decision"] = False
                state.context_data["product_question_prompted"] = False
                state.context_data["just_resumed_from_interrupt"] = True  # NEW FLAG

                
                # Important: Reset the current state BEFORE adding message
                state.current_state = resume_state
                state.interrupted_from = None
                
                # Add transition message
                state.add_message(
                    role="assistant",
                    content="Perfect! Let's continue with your order where we left off.",
                )
                
                # DO NOT consume last_user_message here - let order node handle it
                # This allows the order node to re-prompt if needed
                state.last_user_message = ""  # Consume the "continue order" message
                
                return state
            
            elif "menu" in user_question.lower() or "main" in user_question.lower():
                state.current_state = ConversationState.MAIN_MENU
                state.context_data["order_interrupted"] = False
                state.context_data["awaiting_resume_decision"] = False
                state.context_data["product_question_prompted"] = False
                state.interrupted_from = None
                
                state.add_message(
                    role="assistant",
                    content="Okay, back to the main menu. How can I help you?",
                )
                state.last_user_message = ""
                return state
        
        try:
            # Use RAG system to retrieve answer
            answer = await asyncio.to_thread(retrieve_answer, user_question)
            
            # If interrupted from order, remind them they can continue
            follow_up = (
                "\n\nDo you have other questions? "
                "Or say **done** when you're ready to continue your order."
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