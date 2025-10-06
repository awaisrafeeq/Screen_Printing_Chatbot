from models.session_state import SessionState, ConversationState, Intent
from services.intent_classifier import IntentClassifier
# from flows.rag_system import generate_answer
from flows.rag_system import retrieve_answer


_classifier = IntentClassifier()

def _keyword_fallback(text: str) -> Intent | None:
    t = (text or "").lower()
    
    # Check for product-related keywords
    if any(w in t for w in ["product", "price", "pricing", "cost", "shirt", "hoodie", 
                            "embroidery", "screen print", "minimum", "delivery", 
                            "turnaround", "rush", "payment", "design", "logo",
                            "dtf", "transfer", "ink", "color", "size"]):
        return Intent.HAS_QUESTIONS_ABOUT_PRODUCT
    
    if any(w in t for w in ["order", "quote", "place order", "get a quote"]):
        return Intent.PLACE_ORDER
    if any(w in t for w in ["human", "agent", "representative", "call"]):
        return Intent.WANTS_HUMAN
    if any(w in t for w in ["end", "cancel", "stop", "goodbye", "bye"]):
        return Intent.END_CONVERSATION
    return None

async def main_menu_node(state: SessionState) -> SessionState:
    # First time in: prompt and pause
    if not state.context_data.get("main_menu_prompted"):
        state.add_message(
            role="assistant",
            content=(
                "Hey there! I'd love to help with any questions you have. "
                "I can also help you place a quote request if you want pricing.\n\n"
                "How can I help you?"
            ),
        )
        state.context_data["main_menu_prompted"] = True
        state.last_user_message = ""  # consume
        return state

    # If user spoke, classify with LLM; if it fails, use keyword fallback
    if state.last_user_message:
        intent = None
        confidence = 0.0
        reasoning = ""
        try:
            result = await _classifier.classify_intent(
                state.last_user_message,
                context={"current_state": state.current_state.value}
            )
            # result is a dict: {"intent": "...", "confidence": float, "reasoning": "..."}
            name = result.get("intent") or "No match"
            # Map to enum; if invalid, treat as NO_MATCH
            intent = Intent(name) if name in Intent.__members__.values() else None
            # Enum lookup by value if needed
            if intent is None:
                # Try to match by value → Enum(value) may throw, so fallback
                try:
                    intent = Intent(result.get("intent", "No match"))
                except Exception:
                    intent = Intent.NO_MATCH
            confidence = float(result.get("confidence", 0.0) or 0.0)
            reasoning = result.get("reasoning", "") or ""
        except Exception:
            intent = _keyword_fallback(state.last_user_message) or Intent.NO_MATCH
            confidence = 0.1
            reasoning = "fallback keyword routing"

        state.classified_intent = intent
        state.context_data["intent_confidence"] = confidence
        state.context_data["intent_reasoning"] = reasoning

        if intent == Intent.HAS_QUESTIONS_ABOUT_PRODUCT:
            state.current_state = ConversationState.HAS_QUESTIONS_ABOUT_PRODUCT
            state.add_message(
                role="assistant",
                content="I can help answer your product questions!",
                metadata={"intent": intent.value, "confidence": confidence},
            )
            state.last_user_message = ""
            return state

        if intent == Intent.PLACE_ORDER:
            state.current_state = ConversationState.ORDER_CONTACT
            state.add_message(
                role="assistant",
                content="Great — let's start your quote request.",
                metadata={"intent": intent.value, "confidence": confidence},
            )
            state.last_user_message = ""  # consume
            return state

        if intent == Intent.WANTS_HUMAN:
            state.current_state = ConversationState.WANTS_HUMAN
            state.add_message(
                role="assistant",
                content="Sure — I’ll connect you with a person.",
                metadata={"intent": intent.value, "confidence": confidence},
            )
            state.last_user_message = ""
            return state

        if intent == Intent.END_CONVERSATION:
            state.current_state = ConversationState.END
            state.add_message(
                role="assistant",
                content="Okay, ending our chat now. Thanks!",
                metadata={"intent": intent.value, "confidence": confidence},
            )
            state.last_user_message = ""
            return state
        
        # NO_MATCH / GREETING / product-questions stay in menu
        reply = (
            "Hi! Ask me anything about apparel, printing, pricing, or say **I want to order** to start a quote."
            if intent != Intent.HAS_QUESTIONS_ABOUT_PRODUCT
            else "Sure—what product are you curious about? T-shirts, hoodies, caps, polos…"
        )
        state.add_message(
            role="assistant",
            content=reply,
            metadata={"intent": intent.value, "confidence": confidence},
        )
        state.current_state = ConversationState.MAIN_MENU
        state.last_user_message = ""  # consume
        return state

    # No new text → pause (router will return "end")
    state.current_state = ConversationState.MAIN_MENU
    state.last_user_message = ""
    return state

def route_from_main_menu(state: SessionState) -> str:
    if state.current_state == ConversationState.ORDER_CONTACT:
        return "order_contact"
    if state.current_state == ConversationState.HAS_QUESTIONS_ABOUT_PRODUCT:
        return "product_questions"  # NEW ROUTE
    if state.current_state == ConversationState.WANTS_HUMAN:
        return "wants_human"
    if state.current_state == ConversationState.END:
        return "end_conversation"
    return "end"  # pause when waiting in main menu