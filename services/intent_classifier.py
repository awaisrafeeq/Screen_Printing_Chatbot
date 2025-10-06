
import os
import json
from typing import Dict, Any
from openai import AsyncOpenAI
from models.session_state import Intent
from dotenv import load_dotenv
# Load environment variables from .env
load_dotenv()
class IntentClassifier:
    """OpenAI-powered intent classification"""
    
    def __init__(self):
        self.client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    
    SYSTEM_PROMPT = """You are an intent classifier for Screen Printing NW chatbot.

Classify the user's message into exactly ONE of these intents:

1. "Greeting" - User is saying hello, hi, good morning, etc.
2. "Has Questions about Product" - User asking about products, services, pricing, capabilities
3. "Place order" - User wants to place an order, get a quote, or start ordering process  
4. "End conversation" - User wants to end chat (bye, goodbye, quit, exit, done, etc.)
5. "Wants Human" - User wants to talk to a human, representative, or real person
6. "Yes" - Affirmative responses (yes, yeah, sure, ok, continue, etc.)
7. "No" - Negative responses (no, nope, stop, cancel, etc.)
8. "No match" - None of the above intents clearly match

Response format:
{
    "intent": "exact intent name from list above",
    "confidence": 0.95,
    "reasoning": "brief explanation"
}

Be very precise with intent names. Return only valid JSON."""

    async def classify_intent(self, user_message: str, context: Dict[str, Any] = None) -> Dict[str, Any]:
        """Classify user intent using OpenAI"""
        try:
            # Add context if provided
            system_prompt = self.SYSTEM_PROMPT
            if context and context.get("current_state"):
                system_prompt += f"\n\nCurrent conversation context: User is in {context['current_state']} state."
            
            response = await self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
                temperature=0.1,
                max_tokens=250
            )
            
            # Parse JSON response
            result_text = response.choices[0].message.content.strip()
            result = json.loads(result_text)
            
            # Validate intent is in our enum
            intent_name = result.get("intent", "No match")
            try:
                Intent(intent_name)
                return result
            except ValueError:
                return {
                    "intent": "No match",
                    "confidence": 0.0,
                    "reasoning": f"Invalid intent returned: {intent_name}"
                }
                
        except Exception as e:
            print(f"Intent classification error: {e}")
            return {
                "intent": "No match", 
                "confidence": 0.0,
                "reasoning": f"Classification failed: {str(e)}"
            }
        

    def _keyword_fallback(self, text: str) -> Intent | None:
        """Fallback function for keyword-based intent matching"""
        t = (text or "").lower()
        
        # Check if 'product' is mentioned in the text
        if "product" in t:
            return Intent.HAS_QUESTIONS_ABOUT_PRODUCT  # New fallback for product-related queries
        
        # Existing fallback logic for other intents
        if any(w in t for w in ["order", "quote", "pricing", "place order"]):
            return Intent.PLACE_ORDER
        if any(w in t for w in ["human", "agent", "representative", "call"]):
            return Intent.WANTS_HUMAN
        if any(w in t for w in ["end", "cancel", "stop", "goodbye", "bye"]):
            return Intent.END_CONVERSATION
        
        return None