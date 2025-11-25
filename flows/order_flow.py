from __future__ import annotations
from typing import Dict, List, Tuple, Optional
import re
# add near other imports
from flows.email_sender import send_email

from models.session_state import (
    SessionState,
    ConversationState,
    Intent,
    SizeQuantity, OrderDetails
)
from services.intent_classifier import IntentClassifier
import os
import asyncio
from flows.oauth_uploader import upload_to_drive
import uuid
import json

# ---------- Utilities ----------

INTERRUPT_INTENTS = {Intent.WANTS_HUMAN, Intent.END_CONVERSATION}

PRODUCT_CATALOG = {
    "t-shirt": ["white", "black", "navy", "red", "gray"],
    "hoodie": ["black", "gray", "navy"],
    "hat": ["black", "white", "navy", "khaki"],
    "polo": ["white", "black", "navy"],
}

def _tok(s: str) -> List[str]:
    return re.findall(r"[a-zA-Z0-9]+", (s or "").lower())
_classifier = IntentClassifier()

def _wants_human(text: str) -> bool:
    t = (text or "").lower()
    return any(w in t for w in ["human", "agent", "representative", "talk to a person", "call me"])

def _wants_end(text: str) -> bool:
    t = (text or "").lower()
    # Require explicit end/cancel/bye — do NOT treat "done" as end.
    return any(w in t for w in ["end", "cancel", "stop", "goodbye", "bye", "finish chat"])

async def _check_interrupt(state: SessionState) -> Optional[ConversationState]:
    """
    Re-classify mid-flow, but only jump if the user explicitly asks for human or to end.
    Prevents accidental END on messages like 'done'.
    """
    if not state.last_user_message:
        return None

    text = state.last_user_message.strip().lower()

    # Check for product questions
    if any(word in text for word in ["product", "question", "price", "pricing", "cost", 
                                      "shirt", "hoodie", "embroidery", "screen print"]):
        # Save EXACT position including sub-step flags
        state.interrupted_from = state.current_state
        state.context_data["order_interrupted"] = True
        state.context_data["interrupt_reason"] = "product_questions"
        
        # Save which question was shown (critical for resumption)
        state.context_data["interrupt_snapshot"] = {
            "contact_question_shown": state.context_data.get("contact_question_shown"),
            "org_question_shown": state.context_data.get("org_question_shown"),
            "type_question_shown": state.context_data.get("type_question_shown"),
        }
        
        state.classified_intent = Intent.HAS_QUESTIONS_ABOUT_PRODUCT
        state.current_state = ConversationState.HAS_QUESTIONS_ABOUT_PRODUCT
        state.context_data["intent_confidence"] = 0.95
        state.context_data["intent_reasoning"] = "keyword: product question during order"
        
        # ✅ ADD TRANSITION MESSAGE IMMEDIATELY
        state.add_message(
            role="assistant",
            content="Sure! I'll help answer your product questions. What would you like to know?"
        )
        state.context_data["product_question_prompted"] = True  # Mark as prompted
        
        return ConversationState.HAS_QUESTIONS_ABOUT_PRODUCT

    # Check for human escalation
    if _wants_human(text):
        state.interrupted_from = state.current_state
        state.context_data["order_interrupted"] = True
        state.context_data["interrupt_reason"] = "wants_human"
        
        state.classified_intent = Intent.WANTS_HUMAN
        state.current_state = ConversationState.WANTS_HUMAN
        state.context_data["intent_confidence"] = 1.0
        state.context_data["intent_reasoning"] = "keyword: wants human during order"
        
        # Removed add_message here to avoid duplication; let the node handle it
        
        return ConversationState.WANTS_HUMAN

    # Check for end conversation
    if _wants_end(text):
        state.interrupted_from = state.current_state
        state.context_data["order_interrupted"] = True
        state.context_data["interrupt_reason"] = "end_conversation"
        
        state.classified_intent = Intent.END_CONVERSATION
        state.current_state = ConversationState.END
        state.context_data["intent_confidence"] = 1.0
        state.context_data["intent_reasoning"] = "keyword: end conversation during order"
        
        # ✅ ADD TRANSITION MESSAGE
        state.add_message(
            role="assistant",
            content="Thanks for chatting! Feel free to come back anytime you're ready to continue your order."
        )
        
        return ConversationState.END
    
    return None

def _render_summary_text(state: SessionState) -> str:
    o = state.order
    sizes_line = ", ".join(f"{s.size}:{s.quantity}" for s in (o.sizes or [])) if o.sizes else "—"
    color_line = o.color.title() if o.color else "No preference"
    logo_line = "Uploaded" if state.context_data.get("logo_file_id") else "—"
    if state.context_data.get("logo_view_link"):
        logo_line = f"[View]({state.context_data['logo_view_link']})"

    name = f"{o.contact.first_name or ''} {o.contact.last_name or ''}".strip()
    contact_line = f"{o.contact.email or '—'} / {o.contact.phone or '—'}"
    if o.contact.email:  # Fix: No leading comma if email present
        contact_line = contact_line.lstrip(', ')  # Strip any leading comma/space
    
    location = state.order.decoration_location or "Not specified"
    return (
        "Quote Request Summary\n"
        f"- Name: {name}\n"
        f"- Email / Phone: {contact_line}\n"
        f"- Organization: {o.organization.name or 'Personal'}\n"
        f"- Order Type: {o.order_type or '—'}\n"
        f"- Budget: {o.budget_range or '—'}\n"
        f"- Service: {o.service_type or '—'}\n"
        f"- Product: {o.product_name or '—'}\n"
        f"- Color: {color_line}\n"
        f"- Decoration Location: {location}\n"
        f"- Number of Colors: {o.decoration_colors or '—'}\n"
        f"- Quantity: {o.total_quantity or '—'}\n"
        f"- Sizes: {sizes_line}\n"
        f"- Logo: {logo_line}\n"
        f"- Delivery: {o.delivery_option or '—'}\n"
        f"- Address: {o.delivery_address or '—'}"
    )

# ---------- Utilities ----------
def _send_summary_to_customer(state: SessionState) -> bool:   # ← NOT async!
    to_addr = (state.order.contact.email or "").strip()
    if not to_addr:
        return False

    subject = "Your Screen Printing NW Quote Request Summary"
    md = _render_summary_text(state)

    body = md.replace("**", "")
    link = state.context_data.get("logo_view_link", "").strip()
    if link:
        body = body.replace(f"[View]({link})", f"View: {link}")

    return send_email(to_addr, subject, body)   # ← Just call it. No await!

# ---------- Parsers ----------
EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
PHONE_RE = re.compile(r"(?:\+?\d{1,3}[-.\s()]*)?(?:\d[-.\s()]*){7,}", re.I)

def parse_contact_info(text: str) -> Dict[str, Optional[str]]:
    """
    Extracts first_name, last_name, email, phone from free text.
    Robust to: "John Doe, john@example.com, +92-3xx-xxxxxxx"
    """
    out = {"first_name": None, "last_name": None, "email": None, "phone": None}
    if not text:
        return out

    # Email
    m = EMAIL_RE.search(text)
    if m:
        out["email"] = m.group(0).strip()

    # Phone (normalize)
    m = PHONE_RE.search(text)
    if m:
        phone = re.sub(r"[^0-9+]", "", m.group(0))
        if len(re.sub(r"\D", "", phone)) >= 8:
            out["phone"] = phone

    # Name via explicit phrasing
    lowered = text.lower()
    name_match = re.search(
        r"(?:my\s+name\s+is|name\s+is|i\s+am|i'm)\s+([A-Za-z][A-Za-z\-' ]{1,60})",
        lowered, re.I
    )
    candidate = name_match.group(1) if name_match else None

    # Fallback: text before the email, use the LAST NON-EMPTY segment before the email,
    # then extract a "First Last" from it.
    if not candidate and out["email"]:
        before_email = text.split(out["email"])[0]
        parts = [p.strip() for p in re.split(r"[,\-]\s*", before_email) if p.strip()]
        if parts:
            candidate = parts[-1]  # <-- last *non-empty* segment (fix)

    # Final fallback: take the first 2 alphabetic tokens at the start of the string
    if not candidate:
        head = re.findall(r"[A-Za-z][A-Za-z'\-]+", text)
        if len(head) >= 2:
            candidate = f"{head[0]} {head[1]}"

    if candidate:
        toks = re.findall(r"[A-Za-z][A-Za-z'\-]+", candidate)
        if len(toks) >= 2:
            out["first_name"], out["last_name"] = toks[0].title(), toks[-1].title()
        elif len(toks) == 1:
            out["first_name"] = toks[0].title()

    return out


def parse_product_and_color(text: str) -> Tuple[Optional[str], Optional[str], str]:
    if not text:
        return None, None, "no text"
    txt = text.strip().lower()
    num = re.match(r"^\s*(\d{1,2})(?:\s*[,;]\s*([a-zA-Z]+))?\s*$", txt)
    if num:
        idx = int(num.group(1))
        color_hint = (num.group(2) or "").lower() or None
        products = list(PRODUCT_CATALOG.keys())
        if 1 <= idx <= len(products):
            product = products[idx - 1]
            chosen_color = None
            if color_hint and color_hint in PRODUCT_CATALOG[product]:
                chosen_color = color_hint
            return product, chosen_color, "numeric selection"
    toks = _tok(txt)
    for p, colors in PRODUCT_CATALOG.items():
        if any(t in toks for t in _tok(p)):
            chosen_color = next((c for c in colors if c in toks), None)
            return p, chosen_color, "text match"
    return None, None, "no match"

SIZE_ALIASES = {
    "xs": ["xs", "x-small", "xsmall"],
    "s":  ["s", "sm", "small"],
    "m":  ["m", "med", "medium"],
    "l":  ["l", "lg", "large"],
    "xl": ["xl", "xlarge", "x-large"],
    "2xl": ["2xl", "xxl", "2x", "xx-large"],
    "3xl": ["3xl", "xxxl", "3x"],
}

def _canonical_size(token: str) -> Optional[str]:
    t = token.lower()
    for k, vals in SIZE_ALIASES.items():
        if t in vals:
            return k
    return None

def parse_sizes(text: str) -> Dict[str, int]:
    sizes: Dict[str, int] = {}
    if not text:
        return sizes
    for label, qty in re.findall(r"\b([A-Za-z\-]+)\s*:\s*(\d{1,4})\b", text, flags=re.I):
        key = _canonical_size(label)
        if key:
            sizes[key] = sizes.get(key, 0) + int(qty)
    for qty, label in re.findall(r"\b(\d{1,4})\s*([A-Za-z]{1,4})\b", text, flags=re.I):
        key = _canonical_size(label)
        if key:
            sizes[key] = sizes.get(key, 0) + int(qty)
    for qty, word in re.findall(r"\b(\d{1,4})\s+(x\s*)?([A-Za-z\-]+)\b", text, flags=re.I):
        key = _canonical_size(word)
        if key:
            sizes[key] = sizes.get(key, 0) + int(qty)
    return sizes

# ---------- Order nodes ----------------------------------

async def order_contact_first_name_node(state: SessionState) -> SessionState:
    """Collect first name"""
    interrupt = await _check_interrupt(state)
    if interrupt:
        return state

    # ✅ Check for force reprompt (after resuming from interrupt)
    if state.last_user_message == "__RESUME__":
        state.last_user_message = ""  # Clear it
        state.context_data["contact_first_name_shown"] = False 

    if not state.context_data.get("contact_first_name_shown"):
        state.add_message(
            "assistant",
            "Great — let's start with your quote request. What's your first name?"
        )
        state.context_data["contact_first_name_shown"] = True
        state.last_user_message = ""
        return state

    if state.last_user_message:
        try:
            first_name = state.last_user_message.strip()
            if first_name:
                state.order.contact.first_name = first_name
                state.context_data["contact_first_name_complete"] = True
                state.add_message("assistant", f"Thanks, {first_name}.")
            else:
                state.add_message("assistant", "Please provide your first name.")
        except Exception as e:
            state.add_message("assistant", "Sorry, I couldn't process that. What's your first name?")
        state.last_user_message = ""
    return state
    """Collect first name"""
    interrupt = await _check_interrupt(state)
    if interrupt:
        return state

    if state.last_user_message == "__RESUME__":
        state.last_user_message = ""  # Clear it
        state.context_data["contact_first_name_shown"] = False 

    if not state.context_data.get("contact_first_name_shown"):
        state.add_message(
            "assistant",
            "Great — let's start with your quote request. What's your first name?"
        )
        state.context_data["contact_first_name_shown"] = True
        state.last_user_message = ""
        return state

    if state.last_user_message:
        try:
            first_name = state.last_user_message.strip()
            if first_name:
                state.order.contact.first_name = first_name
                state.context_data["contact_first_name_complete"] = True
                state.add_message("assistant", f"Thanks, {first_name}.")
            else:
                state.add_message("assistant", "Please provide your first name.")
        except Exception as e:
            state.add_message("assistant", "Sorry, I couldn't process that. What's your first name?")
        state.last_user_message = ""
    return state

async def order_contact_last_name_node(state: SessionState) -> SessionState:
    """Collect last name"""
    interrupt = await _check_interrupt(state)
    if interrupt:
        return state

    # ✅ Check for force reprompt (after resuming from interrupt)
    if state.last_user_message == "__RESUME__":
        state.last_user_message = ""  # Clear it
        state.context_data["contact_last_name_shown"] = False  # Force re-show


    if not state.context_data.get("contact_last_name_shown"):
        state.add_message(
            "assistant",
            "What's your last name?"
        )
        state.context_data["contact_last_name_shown"] = True
        state.last_user_message = ""
        return state

    if state.last_user_message:
        try:
            last_name = state.last_user_message.strip()
            if last_name:
                state.order.contact.last_name = last_name
                state.context_data["contact_last_name_complete"] = True
                state.add_message("assistant", "Got it.")
            else:
                state.add_message("assistant", "Please provide your last name.")
        except Exception as e:
            state.add_message("assistant", "Sorry, I couldn't process that. What's your last name?")
        state.last_user_message = ""
    return state
    """Collect last name"""
    interrupt = await _check_interrupt(state)
    if interrupt:
        return state

    if state.last_user_message == "__RESUME__":
        state.last_user_message = ""  # Clear it
        state.context_data["contact_last_name_shown"] = False  # Force re-show


    if not state.context_data.get("contact_last_name_shown"):
        state.add_message(
            "assistant",
            "What's your last name?"
        )
        state.context_data["contact_last_name_shown"] = True
        state.last_user_message = ""
        return state

    if state.last_user_message:
        try:
            last_name = state.last_user_message.strip()
            if last_name:
                state.order.contact.last_name = last_name
                state.context_data["contact_last_name_complete"] = True
                state.add_message("assistant", "Got it.")
            else:
                state.add_message("assistant", "Please provide your last name.")
        except Exception as e:
            state.add_message("assistant", "Sorry, I couldn't process that. What's your last name?")
        state.last_user_message = ""
    return state

async def order_contact_email_node(state: SessionState) -> SessionState:
    """Collect email"""
    interrupt = await _check_interrupt(state)
    if interrupt:
        return state

    # ✅ Check for force reprompt (after resuming from interrupt)
    if state.last_user_message == "__RESUME__":
        state.last_user_message = ""  # Clear it
        state.context_data["contact_email_shown"] = False  # Force re-show


    if not state.context_data.get("contact_email_shown"):
        state.add_message(
            "assistant",
            "What's your email address? (We'll use this to send your quote)"
        )
        state.context_data["contact_email_shown"] = True
        state.last_user_message = ""
        return state

    if state.last_user_message:
        try:
            email = state.last_user_message.strip().lower()
            if email and "@" in email:  # Basic validation
                state.order.contact.email = email
                state.context_data["contact_email_complete"] = True
                state.add_message("assistant", "Thanks.")
            else:
                state.add_message("assistant", "Please provide a valid email address.")
        except Exception as e:
            state.add_message("assistant", "Sorry, I couldn't process that. What's your email?")
        state.last_user_message = ""
    return state
    """Collect email"""
    interrupt = await _check_interrupt(state)
    if interrupt:
        return state

    if state.last_user_message == "__RESUME__":
        state.last_user_message = ""  # Clear it
        state.context_data["contact_email_shown"] = False  # Force re-show


    if not state.context_data.get("contact_email_shown"):
        state.add_message(
            "assistant",
            "What's your email address? (We'll use this to send your quote)"
        )
        state.context_data["contact_email_shown"] = True
        state.last_user_message = ""
        return state

    if state.last_user_message:
        try:
            email = state.last_user_message.strip().lower()
            if email and "@" in email:  # Basic validation
                state.order.contact.email = email
                state.context_data["contact_email_complete"] = True
                state.add_message("assistant", "Thanks.")
            else:
                state.add_message("assistant", "Please provide a valid email address.")
        except Exception as e:
            state.add_message("assistant", "Sorry, I couldn't process that. What's your email?")
        state.last_user_message = ""
    return state

async def order_contact_phone_node(state: SessionState) -> SessionState:
    """Collect phone"""
    interrupt = await _check_interrupt(state)
    if interrupt:
        return state

    # ✅ Check for force reprompt (after resuming from interrupt)
    if state.last_user_message == "__RESUME__":
        state.last_user_message = ""  # Clear it
        state.context_data["contact_phone_shown"] = False 

    if not state.context_data.get("contact_phone_shown"):
        state.add_message(
            "assistant",
            "Finally, what's your phone number (with country code, e.g., +92-3xx-xxxxxxx)?"
        )
        state.context_data["contact_phone_shown"] = True
        state.last_user_message = ""
        return state

    if state.last_user_message:
        try:
            phone = state.last_user_message.strip()
            if phone:
                state.order.contact.phone = phone
                state.context_data["contact_phone_complete"] = True
                state.context_data["contact_complete"] = True  # Set overall complete here
                state.add_message("assistant", "Perfect, thanks for your contact details.")
            else:
                state.add_message("assistant", "Please provide your phone number.")
        except Exception as e:
            state.add_message("assistant", "Sorry, I couldn't process that. What's your phone?")
        state.last_user_message = ""
    return state
    """Collect phone"""
    interrupt = await _check_interrupt(state)
    if interrupt:
        return state

    if state.last_user_message == "__RESUME__":
        state.last_user_message = ""  # Clear it
        state.context_data["contact_phone_shown"] = False 

    if not state.context_data.get("contact_phone_shown"):
        state.add_message(
            "assistant",
            "Finally, what's your phone number (with country code, e.g., +92-3xx-xxxxxxx)?"
        )
        state.context_data["contact_phone_shown"] = True
        state.last_user_message = ""
        return state

    if state.last_user_message:
        try:
            phone = state.last_user_message.strip()
            if phone:
                state.order.contact.phone = phone
                state.context_data["contact_phone_complete"] = True
                state.context_data["contact_complete"] = True  # Set overall complete here
                state.add_message("assistant", "Perfect, thanks for your contact details.")
            else:
                state.add_message("assistant", "Please provide your phone number.")
        except Exception as e:
            state.add_message("assistant", "Sorry, I couldn't process that. What's your phone?")
        state.last_user_message = ""
    return state

async def order_organization_node(state: SessionState) -> SessionState:
    """Handle organization/business question - now split into two steps"""
    interrupt = await _check_interrupt(state)
    if interrupt:
        return state

    if state.last_user_message == "__RESUME__":
        state.last_user_message = ""  # Clear it
        state.context_data["org_type_shown"] = False

    # Step 1: Ask if business/org/team
    if not state.context_data.get("org_type_shown"):
        state.add_message(
            "assistant",
            "Is this order for a business, organization, or team? Reply **yes** or **personal**."
        )
        state.context_data["org_type_shown"] = True
        state.last_user_message = ""
        return state

    # If we have a message after type question
    if state.last_user_message and state.context_data.get("org_type_shown") and not state.context_data.get("org_name_shown"):
        text = state.last_user_message.strip().lower()
        
        if any(word in text for word in ["no", "personal"]):
            state.order.organization.is_business = False
            state.order.organization.name = None
            state.context_data["org_complete"] = True
            state.last_user_message = ""
            return state
            
        elif any(word in text for word in ["yes", "business", "organization", "team"]):
            # Ask for name in separate question
            state.add_message(
                "assistant",
                "What is the name of your business/organization/team?"
            )
            state.context_data["org_name_shown"] = True
            state.last_user_message = ""
            return state
            
        else:
            # Didn't understand - reprompt
            state.add_message(
                "assistant",
                "Please reply **Yes** if it's for a business/organization/team, or **No** if personal."
            )
            state.last_user_message = ""  # Critical: clear here to stop loop
            return state

    # If we have a message after name question
    if state.last_user_message and state.context_data.get("org_name_shown"):
        name = state.last_user_message.strip()
        if name:  # Basic validation - not empty
            state.order.organization.is_business = True
            state.order.organization.name = name
            state.context_data["org_complete"] = True
            state.last_user_message = ""  # Clear on success
        else:
            # Reprompt if empty
            state.add_message(
                "assistant",
                "Please provide the name of your business/organization/team."
            )
            state.last_user_message = ""  # Clear on reprompt
        return state

    state.last_user_message = ""  # Safety clear
    return state

async def order_type_node(state: SessionState) -> SessionState:
    
    interrupt = await _check_interrupt(state)
    if interrupt:
        state.last_user_message = ""
        return state
        # ✅ Check for force reprompt (after resuming from interrupt)
    if state.last_user_message == "__RESUME__":
        state.last_user_message = ""  # Clear it
        state.context_data["type_question_shown"] = False 

    if state.context_data.get("type_question_shown") and not state.last_user_message:
        return state
    
    choices = [
        "Corporate hiring", "School/spirit wear", "Sports team", "Retail resale",
        "Employee uniforms", "Other"
    ]
    if not state.context_data.get("type_question_shown"):
        state.add_message(
            role="assistant",
            content=(
                "What type of order is this?\n"
                "1) Corporate hiring\n2) School/spirit wear\n3) Sports team\n"
                "4) Retail resale\n5) Employee uniforms\n6) Other\n\n"
                "Reply with the number or the label."
            ),
        )
        state.context_data["type_question_shown"] = True
        state.last_user_message = ""
        return state

    if state.last_user_message:
        txt = state.last_user_message.strip().lower()
        idx = re.match(r"^\s*([1-6])\s*$", txt)
        if idx:
            val = choices[int(idx.group(1)) - 1]
        else:
            val = next((c for c in choices if c.lower() in txt), None)
            if not val:
                state.add_message(role="assistant", content="Please reply with a number 1–6 or a label from the list.")
                state.last_user_message = ""
                return state
        state.order.order_type = val
        state.context_data["type_complete"] = True
        state.add_message(role="assistant", content="Great. What **budget** are you aiming for? (Premium / Value).")
        state.last_user_message = ""
        return state

    state.last_user_message = ""
    return state

async def order_budget_node(state: SessionState) -> SessionState:

    interrupt = await _check_interrupt(state)
    if interrupt:
        state.last_user_message = ""
        return state
    
    if state.last_user_message == "__RESUME__":
        state.last_user_message = ""  # Clear it
        state.context_data["budget_question_shown"] = False

    if state.last_user_message == "__RESUME__":
        state.last_user_message = ""
        return state

    if state.context_data.get("budget_question_shown") and not state.last_user_message:
        return state

    if not state.context_data.get("budget_question_shown"):
        state.add_message(
            role="assistant",
            content=(
                "Choose **Budget Range**:\n"
                "1) Premium — top-tier fabrics & finish\n"
                "2) Value — best price-to-quality\n"
                "Reply 1 or 2, or type Premium/Value."
            ),
        )
        state.context_data["budget_question_shown"] = True
        state.last_user_message = ""
        return state

    if state.last_user_message:
        txt = state.last_user_message.strip().lower()
        val = "Premium" if re.match(r"^\s*1\s*$", txt) or "premium" in txt else (
              "Value" if re.match(r"^\s*2\s*$", txt) or "value" in txt else None)
        if not val:
            state.add_message(role="assistant", content="Please reply 1 (Premium) or 2 (Value).")
            state.last_user_message = ""
            return state
        state.order.budget_range = val
        state.context_data["budget_complete"] = True
        state.add_message(
            role="assistant",
            content="Got it. Are you looking for **Screen Printing** or **Embroidery**? (Reply 1 or 2)"
        )
        state.last_user_message = ""
        return state

    state.last_user_message = ""
    return state

async def order_service_node(state: SessionState) -> SessionState:

    interrupt = await _check_interrupt(state)
    if interrupt:
        state.last_user_message = ""
        return state

    if state.last_user_message == "__RESUME__":
        state.last_user_message = ""  # Clear it
        state.context_data["service_question_shown"] = False

    if state.last_user_message == "__RESUME__":
        state.last_user_message = ""  # Clear it
        state.context_data["service_question_shown"] = False

    if state.context_data.get("service_question_shown") and not state.last_user_message:
        return state

    if not state.context_data.get("service_question_shown"):
        state.add_message(
            role="assistant",
            content=(
                "Choose **Service Type**:\n"
                "1) Screen Printing — most common for T-shirts and hoodies\n"
                "2) Embroidery — Most common on jackets, polos and hats\n"
                "Reply 1 or 2, or type the label."
            ),
        )
        state.context_data["service_question_shown"] = True
        state.last_user_message = ""
        return state

    if state.last_user_message:
        txt = state.last_user_message.strip().lower()
        val = "Screen Printing" if (re.match(r"^\s*1\s*$", txt) or "screen" in txt) else (
              "Embroidery" if (re.match(r"^\s*2\s*$", txt) or "embroider" in txt) else None)
        if not val:
            state.add_message(role="assistant", content="Please reply 1 (Screen Printing) or 2 (Embroidery).")
            state.last_user_message = ""
            return state
        state.order.service_type = val
        state.context_data["service_complete"] = True
        state.add_message(
            role="assistant",
            content=(
                "Great. Choose **Apparel Type** (you can also type a product directly later):\n"
                "1) T-Shirt\n2) Hoodie\n3) Hat\n4) Polo\n\nReply with a number."
            ),
        )
        state.last_user_message = ""
        return state

    state.last_user_message = ""
    return state

async def order_apparel_node(state: SessionState) -> SessionState:

    interrupt = await _check_interrupt(state)
    if interrupt:
        state.last_user_message = ""
        return state
    
    if state.last_user_message == "__RESUME__":
        state.last_user_message = ""  # Clear it
        state.context_data["apparel_question_shown"] = False

    if state.last_user_message == "__RESUME__":
        state.last_user_message = ""  # Clear it
        state.context_data["apparel_question_shown"] = False

    if state.context_data.get("apparel_question_shown") and not state.last_user_message:
        return state


    if not state.context_data.get("apparel_question_shown"):
        state.add_message(
            role="assistant",
            content=(
                "Which **product** would you like?\n"
                "1) T-Shirt\n2) Hoodie\n3) Hat\n4) Polo\n\n"
                "You can reply with a number, or name the product like: `Hoodie in Navy`."
            ),
        )
        state.context_data["apparel_question_shown"] = True
        state.last_user_message = ""
        return state

    if state.last_user_message:
        product, color, reason = parse_product_and_color(state.last_user_message)
        if not product:
            prod_list = "\n".join(f"{i+1}) {p.title()}" for i, p in enumerate(PRODUCT_CATALOG.keys()))
            state.add_message(
                role="assistant",
                content=(
                    "Sorry, I couldn't match that. Pick one by number:\n"
                    f"{prod_list}\n\nYou can also write like: `2, Navy`."
                ),
            )
            state.last_user_message = ""
            return state

        state.order.apparel_category = product  # optional; keep baseline category
        state.order.product_name = product
        if color:
            state.order.color = color
        state.context_data["apparel_complete"] = True

        colors = ", ".join(c.title() for c in PRODUCT_CATALOG[product])
        state.add_message(
            role="assistant",
            content=(
                f"Selected: **{product.title()}**"
                + (f" in **{state.order.color.title()}**." if state.order.color else ".")
                + f"\nAvailable colors: {colors}\n"
                "If you want a specific color, type it now; otherwise say **Continue**."
            ),
        )
        state.last_user_message = ""
        return state

    state.last_user_message = ""
    return state

async def order_product_node(state: SessionState) -> SessionState:

    interrupt = await _check_interrupt(state)
    if interrupt:
        state.last_user_message = ""
        return state
    
    # Check for force reprompt after resuming from interrupt
    if state.last_user_message == "__RESUME__":
        state.last_user_message = ""  # Clear it
        state.context_data["product_question_shown"] = False

    if state.context_data.get("product_question_shown") and not state.last_user_message:
        return state

    # Ask color, but let frontend render the full palette
    if not state.context_data.get("product_question_shown"):
        if state.order.product_name and not state.order.color:
            state.add_message(
                role="assistant",
                content=(
                    f"Any preferred **color** for {state.order.product_name.title()}?\n"
                    "Please choose from the color selector below, or say **No preference**.\n"
                    "[[COLOR_PICKER]]"
                )
            )
            state.context_data["product_question_shown"] = True
            state.last_user_message = ""
            return state
        else:
            # Product already chosen and color set or not needed
            state.context_data["product_complete"] = True
            state.last_user_message = ""
            return state

    if state.last_user_message:
        raw = state.last_user_message.strip()
        txt = raw.lower()

        if state.order.product_name:
            if "no preference" in txt or "any" in txt:
                state.order.color = None
            else:
                # Accept whatever the user or the palette sends as the color name
                state.order.color = raw

        state.context_data["product_complete"] = True
        state.last_user_message = ""
        return state

    state.last_user_message = ""
    return state

async def order_logo_node(state: SessionState) -> SessionState:

    interrupt = await _check_interrupt(state)
    if interrupt:
        state.last_user_message = ""
        return state
    # ✅ Check for force reprompt (after resuming from interrupt)
    if state.last_user_message == "__RESUME__":
        state.last_user_message = ""  # Clear it
        state.context_data["logo_complete"] = False  

    # ✅ FIRST CHECK: If logo is complete, exit immediately
    if state.context_data.get("logo_complete"):
        # print(f"DEBUG order_logo_node: logo_complete=True, exiting")
        state.last_user_message = ""
        return state

    # If question was shown and we're waiting for response
    if state.context_data.get("logo_question_shown") and not state.last_user_message:
        # print(f"DEBUG order_logo_node: waiting for user response")
        return state

    # Show question first time
    if not state.context_data.get("logo_question_shown"):
        print(f"DEBUG order_logo_node: showing logo question")
        state.context_data["logo_question_shown"] = True
        upload_key = uuid.uuid4().hex
        state.context_data["upload_key"] = upload_key
        state.context_data["awaiting_upload"] = True
        state.add_message(
            role="assistant",
            content=(
                "Please upload your logo/artwork file.\n"
                "Supported formats: PNG, JPG, SVG, PDF, AI, EPS, PSD\n"
                "Or type **Skip** if you don't have a logo yet."
            )
        )
        state.last_user_message = ""
        return state

    # Handle user input
    if state.last_user_message:
        txt = state.last_user_message.strip().lower()
        print(f"DEBUG order_logo_node: user message = '{txt}'")
        
        # ✅ Handle "continue" from upload endpoint
        if txt == "continue":
            # Upload already completed, just check the flag
            if state.context_data.get("logo_complete"):
                print(f"DEBUG: continue message + logo_complete, moving forward")
                state.last_user_message = ""
                return state
        
        if txt in {"skip", "no", "none", "no logo"}:
            state.context_data["logo_complete"] = True
            state.context_data["awaiting_upload"] = False
            state.add_message(
                role="assistant",
                content="No problem! Continuing without a logo."
            )
            state.last_user_message = ""
            return state
        else:
            state.add_message(
                role="assistant",
                content="Please upload your logo file using the button, or type **Skip**."
            )
            state.last_user_message = ""
            return state

    state.last_user_message = ""
    return state

async def order_decoration_location_node(state: SessionState) -> SessionState:
    """Ask for decoration location, customized by service type - no skip allowed"""
    interrupt = await _check_interrupt(state)
    if interrupt:
        return state

    if state.last_user_message == "__RESUME__":
        state.last_user_message = ""  # Clear it
        state.context_data["decoration_location_shown"] = False

    if not state.context_data.get("decoration_location_shown"):
        # Customize based on service_type with numbered options
        if state.order.service_type == "Screen Printing":
            locations = "1.Full Front \n2.Full Back \n3. Left Chest\n4. Right Chest\n5. Left Sleeve\n6. Right Sleeve\n7. Small Upper Back \n8. Front Center\n"
            state.context_data["loc_map"] = {
                "1": "Full Front",
                "2": "Full Back",         
                "3": "Left Chest",
                "4": "Right Chest",
                "5": "Left Sleeve",
                "6": "Right Sleeve",
                "7": "Small Upper Back",
                "8": "Front Center"
            }
        elif state.order.service_type == "Embroidery":
            locations = "1. Front Left Panel\n2. Front Right Panel\n3. Back\n4. Front Center"
            state.context_data["loc_map"] = {
                "1": "Front Left Panel",
                "2": "Front Right Panel",
                "3": "Back",
                "4": "Front Center"
            }
        else:
            locations = "1. Left Chest\n2. Right Chest\n3. Front Center\n4. Back"  # Fallback
            state.context_data["loc_map"] = {
                "1": "Left Chest",
                "2": "Right Chest",
                "3": "Front Center",
                "4": "Back"
            }

        state.add_message(
            "assistant",
            f"Where would you like the decoration/print to be placed?\n"
            f"Common locations:\n"
            f"{locations}\n"
            "Reply with the number or type the location."
        )
        state.context_data["decoration_location_shown"] = True
        state.last_user_message = ""
        return state

    if state.last_user_message:
        text = state.last_user_message.strip().lower()
        location = None
        loc_map = state.context_data.get("loc_map", {})

        if text in loc_map:
            location = loc_map[text]
        elif text:  # Use text if not number
            location = text.capitalize()  # Optional: capitalize for nice display

        if location:
            state.order.decoration_location = location  # Store mapped string
            state.context_data["decoration_location_complete"] = True
            state.last_user_message = ""
        else:
            # Reprompt if invalid
            state.add_message(
                "assistant",
                "Please reply with a valid number or location name."
            )
            state.last_user_message = ""  # Clear to prevent loop
        return state

    return state

async def order_decoration_colors_node(state: SessionState) -> SessionState:
    """Ask how many colors in the decoration/print"""
    
    interrupt = await _check_interrupt(state)
    if interrupt: 
        state.last_user_message = ""
        return state
    
    if state.last_user_message == "__RESUME__":
        state.last_user_message = ""  # Clear it
        state.context_data["decoration_colors_shown"] = False  

    if state.context_data.get("decoration_colors_shown") and not state.last_user_message:
        return state
    
    if not state.context_data.get("decoration_colors_shown"):
        state.add_message(
            role="assistant",
            content=(
                "How many **colors** will be in your design/decoration?\n"
                "(This affects screen printing pricing)\n\n"
                "Enter a number (e.g., 1, 2, 3) or say **Not sure** if you don't know yet."
            ),
        )
        state.context_data["decoration_colors_shown"] = True
        state.last_user_message = ""
        return state
    
    if state.last_user_message:
        txt = state.last_user_message.strip().lower()
        if txt in {"skip", "not sure", "don't know", "unsure"}:
            state.order.decoration_colors = None
        else:
            # Extract number
            nums = re.findall(r"\d+", state.last_user_message)
            if nums:
                state.order.decoration_colors = int(nums[0])
            else:
                state.add_message(
                    role="assistant",
                    content="Please provide a number (e.g., 1, 2, 3) or say **Not sure**."
                )
                state.last_user_message = ""
                return state
        
        state.context_data["decoration_colors_complete"] = True
        # 🔹 Do NOT ask for quantity here. Let order_quantity_node handle it.
        state.add_message(
            role="assistant",
            content="Thanks. I’ve saved your decoration colors."
        )
        state.last_user_message = ""
        return state
    
    state.last_user_message = ""
    return state

async def order_quantity_node(state: SessionState) -> SessionState:
    """Smart quantity question. range or exact sizes mode, based on service type."""

    interrupt = await _check_interrupt(state)
    if interrupt:
        state.last_user_message = ""
        return state

    # Handle resume
    if state.last_user_message == "__RESUME__":
        state.last_user_message = ""
        state.context_data["qty_question_shown"] = False

    # Figure out which ranges to use based on service type
    service = (state.order.service_type or "").lower()

    if "screen" in service:
        # Screen Printing ranges
        range_map = {
            1: "24-50",
            2: "51-100",
            3: "101-200",
            4: "201-350",
            5: "351+",
        }
        options_text = (
            "1. 24-50\n"
            "2. 51-100\n"
            "3. 101-200\n"
            "4. 201-350\n"
            "5. 351+\n"
        )
    elif "embroider" in service:
        # Embroidery ranges
        range_map = {
            1: "6-10",
            2: "11-20",
            3: "21-50",
            4: "51-100",
            5: "101+",
        }
        options_text = (
            "1. 6-10\n"
            "2. 11-20\n"
            "3. 21-50\n"
            "4. 51-100\n"
            "5. 101+\n"
        )
    else:
        # Fallback: old generic ranges
        range_map = {
            1: "0-10",
            2: "11-20",
            3: "21-50",
            4: "51-100",
            5: "101-200",
            6: "201+",
        }
        options_text = (
            "1. 0-10\n"
            "2. 11-20\n"
            "3. 21-50\n"
            "4. 51-100\n"
            "5. 101-200\n"
            "6. 201+\n"
        )

    max_option = max(range_map.keys())

    # If question already shown and we are waiting for user input
    if state.context_data.get("qty_question_shown") and not state.last_user_message:
        return state

    # First time. show the combined question with correct ranges
    if not state.context_data.get("qty_question_shown"):
        state.add_message(
            role="assistant",
            content=(
                "Do you know the exact sizes and quantities you are interested in? "
                "If not, pick from the range that is most accurate:\n"
                f"{options_text}\n"
                f"Reply **yes** if you know exact, or a number 1 to {max_option} for the range."
            ),
        )
        state.context_data["qty_question_shown"] = True
        state.last_user_message = ""
        return state

    # We have a user reply
    if state.last_user_message:
        txt_raw = state.last_user_message.strip()
        txt = txt_raw.lower()

        # If user knows exact sizes and quantities. go to sizes node path
        if txt in {"yes", "y", "yeah", "yep", "sure"}:
            state.context_data["exact_sizes_mode"] = True
            state.context_data["exact_sizes_started"] = True
            state.context_data["sizes_complete"] = False
            state.context_data["used_range_path"] = False
            state.context_data["size_step"] = 0
            state.context_data["sizes_collected"] = {}
            # Mark quantity as complete so router can move to ORDER_SIZES
            state.context_data["qty_complete"] = True
            state.last_user_message = ""
            return state

        # Try to parse numeric range
        import re
        m = re.match(r"^\s*([0-9]+)\s*$", txt)
        if not m:
            state.add_message(
                role="assistant",
                content=f"Please reply **yes** for exact sizes, or a number from 1 to {max_option} for the range."
            )
            state.last_user_message = ""
            return state

        idx = int(m.group(1))
        if idx not in range_map:
            state.add_message(
                role="assistant",
                content=f"Please choose a valid option from 1 to {max_option}."
            )
            state.last_user_message = ""
            return state

        range_str = range_map[idx]

        # Store range as total quantity. skip sizes path
        state.order.total_quantity = range_str
        state.order.sizes = []
        state.context_data["used_range_path"] = True
        state.context_data["qty_complete"] = True
        state.context_data["sizes_complete"] = True

        state.add_message(
            role="assistant",
            content=f"Perfect. I will note your total quantity as **{range_str}**."
        )

        state.last_user_message = ""
        return state

    state.last_user_message = ""
    return state

async def order_sizes_node(state: SessionState) -> SessionState:
    """Collect sizes one by one when user chose exact sizes mode."""

    interrupt = await _check_interrupt(state)
    if interrupt:
        state.last_user_message = ""
        return state

    # Safety. if exact sizes mode is not on, just mark sizes complete and exit
    if not state.context_data.get("exact_sizes_mode"):
        state.context_data["sizes_complete"] = True
        state.last_user_message = ""
        return state

    # Handle resume
    if state.last_user_message == "__RESUME__":
        state.last_user_message = ""
        state.context_data["sizes_question_shown"] = False

    # Define the order of sizes
    size_sequence = [
        ("S", "Small"),
        ("M", "Medium"),
        ("L", "Large"),
        ("XL", "XL"),
        ("2XL", "2XL (XXL)"),
        ("3XL", "3XL (XXXL)"),
        ("4XL", "4XL"),
    ]

    size_step = int(state.context_data.get("size_step", 0))
    sizes_collected = state.context_data.get("sizes_collected") or {}

    # If we already showed the question and are waiting for an answer
    if state.context_data.get("sizes_question_shown") and not state.last_user_message:
        return state

    # If we have a user answer for the current size
    if state.context_data.get("sizes_question_shown") and state.last_user_message:
        txt = state.last_user_message.strip().lower()

        # Current size
        if size_step < len(size_sequence):
            key, label = size_sequence[size_step]
        else:
            key, label = None, None

        # Parse the quantity for this size
        if txt in {"skip", "none", "no", "0"}:
            qty = 0
        else:
            try:
                qty = int(txt)
                if qty < 0:
                    raise ValueError()
            except ValueError:
                state.add_message(
                    role="assistant",
                    content="Please reply with a number, 0, or type **skip**."
                )
                state.last_user_message = ""
                return state

        if key is not None:
            sizes_collected[key] = qty
            state.context_data["sizes_collected"] = sizes_collected

        # Move to next size
        size_step += 1
        state.context_data["size_step"] = size_step
        state.context_data["sizes_question_shown"] = False
        state.last_user_message = ""

    # If all sizes are done. compute totals and finish
    if size_step >= len(size_sequence):
        total = sum(v for v in sizes_collected.values() if isinstance(v, int))

        # Map numeric total into ranges depending on service type
        service = (state.order.service_type or "").lower()

        if "screen" in service:
            # Screen Printing buckets
            if total <= 50:
                range_str = "24-50"
            elif total <= 100:
                range_str = "51-100"
            elif total <= 200:
                range_str = "101-200"
            elif total <= 350:
                range_str = "201-350"
            else:
                range_str = "351+"
        elif "embroider" in service:
            # Embroidery buckets
            if total <= 10:
                range_str = "6-10"
            elif total <= 20:
                range_str = "11-20"
            elif total <= 50:
                range_str = "21-50"
            elif total <= 100:
                range_str = "51-100"
            else:
                range_str = "101+"
        else:
            # Fallback generic buckets
            if total <= 10:
                range_str = "0-10"
            elif total <= 20:
                range_str = "11-20"
            elif total <= 50:
                range_str = "21-50"
            elif total <= 100:
                range_str = "51-100"
            elif total <= 200:
                range_str = "101-200"
            else:
                range_str = "201+"

        state.order.total_quantity = range_str
        state.order.sizes = [
            SizeQuantity(size=size, quantity=qty)
            for size, qty in sizes_collected.items()
            if qty > 0
        ]

        state.context_data["qty_complete"] = True
        state.context_data["sizes_complete"] = True
        state.context_data["exact_sizes_started"] = True
        state.context_data["used_range_path"] = False

        breakdown = ", ".join(
            f"{s.size}:{s.quantity}" for s in state.order.sizes
        ) or "None above 0"

        state.add_message(
            "assistant",
            f"All done. Total pieces: {total} ({range_str})\n"
            f"Breakdown: {breakdown}\n\nMoving on..."
        )
        state.last_user_message = ""
        return state

    # Otherwise, ask the next size question
    key, label = size_sequence[size_step]
    product_label = state.order.product_name or "shirt"
    plural_product = product_label if product_label.endswith("s") else product_label + "s"

    state.add_message(
        role="assistant",
        content=(
            f"How many {plural_product} for size **{label}**? "
            "You can reply with a number, 0, or type **skip**."
        ),
    )
    state.context_data["sizes_question_shown"] = True
    state.last_user_message = ""
    return state

async def order_delivery_node(state: SessionState) -> SessionState:

    interrupt = await _check_interrupt(state)
    if interrupt:
        state.last_user_message = ""
        return state
    
    if state.last_user_message == "__RESUME__":
        state.last_user_message = ""  # Clear it
        state.context_data["delivery_question_shown"] = False  

    if state.context_data.get("delivery_question_shown") and not state.last_user_message:
        return state


    if not state.context_data.get("delivery_question_shown"):
        state.add_message(role="assistant", content="Do you prefer **Delivery** or **Pickup**?")
        state.context_data["delivery_question_shown"] = True
        state.last_user_message = ""
        return state

    if state.last_user_message:
        txt = state.last_user_message.strip().lower()
        if "deliver" in txt:
            state.order.delivery_option = "Delivery"
            state.add_message(
                role="assistant",
                content="Please share the **delivery address** (Street, City, Postal Code, Country)."
            )
            state.context_data["awaiting_address"] = True
            state.last_user_message = ""
            return state
        elif "pickup" in txt:
            state.order.delivery_option = "Pick Up"
            state.context_data["delivery_complete"] = True
            state.add_message(role="assistant", content="Noted for **Pickup**. I’ll summarize your request next.")
            state.last_user_message = ""
            return state
        else:
            state.add_message(role="assistant", content="Please say **Delivery** or **Pickup**.")
            state.last_user_message = ""
            return state

    state.last_user_message = ""
    return state

async def order_delivery_address_node(state: SessionState) -> SessionState:

    interrupt = await _check_interrupt(state)
    if interrupt:
        state.last_user_message = ""
        return state
    
    if state.last_user_message == "__RESUME__":
        state.last_user_message = ""  # Clear it
        state.context_data["address_question_shown"] = False  

    if state.context_data.get("address_question_shown") and not state.last_user_message:
        return state


    if not state.context_data.get("awaiting_address"):
        state.context_data["delivery_complete"] = True
        state.last_user_message = ""
        return state

    if not state.context_data.get("address_question_shown"):
        state.add_message(role="assistant", content="Please send your full address in one line.")
        state.context_data["address_question_shown"] = True
        state.last_user_message = ""
        return state

    if state.last_user_message:
        state.order.delivery_address = state.last_user_message.strip()[:240]
        state.context_data["awaiting_address"] = False
        state.context_data["delivery_complete"] = True
        state.add_message(role="assistant", content="Thanks! Address noted. I’ll summarize your request next.")
        state.last_user_message = ""
        return state

    state.last_user_message = ""
    return state

async def order_summary_node(state: SessionState) -> SessionState:
    """Show order summary and handle confirm/edit/end"""
    interrupt = await _check_interrupt(state)
    if interrupt:
        return state

    if state.last_user_message == "__RESUME__":
        state.last_user_message = ""
        return state

    print(f"DEBUG: Summary node - shown: {state.context_data.get('summary_shown')}, confirmed: {state.context_data.get('order_confirmed')}, message: {state.last_user_message}")


    # First time: show summary and prompt
    if not state.context_data.get("summary_shown"):
        summary_text = _render_summary_text(state)
        state.add_message(
            "assistant",
            f"{summary_text}\n\nIf this looks good, reply Confirm. To change anything, say for example `Change color to Black` or `Update quantity to 50`. To cancel, say Cancel."
        )
        state.context_data["summary_shown"] = True
        state.last_user_message = ""
        return state

    if state.last_user_message:
        text = state.last_user_message.strip().lower()
        intent_result = await _classifier.classify_intent(text)  # Use classifier for Yes/No, fallback to keywords for changes
        intent = Intent(intent_result.get("intent", "No match"))

        if intent == Intent.YES or "yes" in text or "confirm" in text:
            success = _send_summary_to_customer(state)

            state.context_data["order_confirmed"] = True
            state.context_data["order_complete"] = True

            # Move to post-confirmation state
            state.current_state = ConversationState.ORDER_POST_CONFIRMATION

            # Single combined message so frontend sees everything
            state.add_message(
                "assistant",
                (
                    "Thanks for submitting your information. "
                    "Our team will respond with a quote within 8 business hours. "
                    "If you have any questions, please call us at (425) 303-3381. "
                    + (
                        "A summary has been sent to your email."
                        if success
                        else "We will process your request shortly!"
                    )
                    + "\n\nWhat would you like to do next?\n\n"
                    "• **Main menu** - Place another quote request\n"
                    "• **End** - Finish our chat"
                ),
            )

            state.last_user_message = ""
            return state

        elif intent == Intent.NO or "no" in text or "cancel" in text:
            state.current_state = ConversationState.END
            state.add_message("assistant", "Okay, canceling the order. Thanks!")
            state.last_user_message = ""
            return state

        else:
            # Use LLM to dynamically parse changes
            try:
                change_prompt = f"""
You are a change parser for an order summary. Given the user message: '{text}'

Extract any requested changes as JSON:
- If it's a change request, output: {{"changes": [{{"field": "field_name", "new_value": "value"}}]}} (array for multiple)
- Valid fields: first_name, last_name, email, phone, organization, order_type, budget_range, service_type, apparel_category, product_name, color, decoration_location, decoration_colors, total_quantity, sizes, delivery_option, delivery_address
- For sizes, parse as string like "S:3, M:5"
- For decoration_colors, ensure integer 1-4
- If logo change, set field to "logo" and new_value to "reset"
- If no changes or unclear, output: {{"changes": []}}
- Do not add extra text.
"""
                response = await _classifier.client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": change_prompt},
                        {"role": "user", "content": text}
                    ],
                    temperature=0.1,
                    max_tokens=200
                )
                result_text = response.choices[0].message.content.strip()
                result = json.loads(result_text)
                changes = result.get("changes", [])

                updated = False
                logo_requested = False
                for change in changes:
                    field = change.get("field")
                    new_value = change.get("new_value")
                    if not field or not new_value:
                        continue

                    if field == "logo":
                        logo_requested = True

                    # Update fields with basic validation
                    if field == "first_name":
                        if isinstance(new_value, str) and len(new_value) > 0:
                            state.order.contact.first_name = new_value
                            updated = True
                    elif field == "last_name":
                        if isinstance(new_value, str) and len(new_value) > 0:
                            state.order.contact.last_name = new_value
                            updated = True
                    elif field == "email":
                        if "@" in new_value and "." in new_value:  # Basic check
                            state.order.contact.email = new_value
                            updated = True
                    elif field == "phone":
                        if len(new_value) >= 10:  # Basic check for phone length
                            state.order.contact.phone = new_value
                            updated = True
                    elif field == "organization":
                        state.order.organization.name = new_value
                        updated = True
                    elif field == "order_type":
                        if new_value in ["Corporate hiring", "School/spirit wear", "Sports team", "Retail resale", "Employee uniforms", "Other"]:
                            state.order.order_type = new_value
                            updated = True
                    elif field == "budget_range":
                        if "budget" in text and "range" in text:
                            if new_value.title() in ["Premium", "Value"]:
                                state.order.budget_range = new_value.title()
                                updated = True
                    elif field == "service_type":
                        if "service" in text and "type" in text: 
                            if new_value.title() in ["Screen Printing", "Embroidery"]:
                                state.order.service_type = new_value.title()
                                updated = True
                    elif field == "apparel_category":
                        if "apparel" in text and "category" in text:
                            state.order.apparel_category = new_value
                            updated = True
                    elif field == "product_name":
                        if "product" in text:
                            state.order.product_name = new_value
                            updated = True
                    elif field == "color":
                        # Normalize the new value (strip extra spaces and convert to title case)
                        normalized_value = new_value.strip().title()

                        # List of valid color options
                        valid_colors = [
                            "Aquatic Blue", "Ash", "Athletic Heater", "Athletic Maroon", "Candy Pink", "Bright Aqua",
                            "Cardinal", "Carolina Blue", "Charcoal", "Clover Green", "Coyote Brown", "Dark Chocolate Brown",
                            "Dark Green", "Dark Heather Grey", "Gold", "Graphite Heather", "Heather Athletic Maroon", 
                            "Heather Dark Choc Brown", "Heather Navy", "Heather Purple", "Heather Red", "Heather Royal", 
                            "Heather Sangria", "Jet Black", "Kelly", "Light Blue", "Lime", "Medium Grey", "Natural", "Navy", 
                            "Neon Blue", "Neon Green", "Neon Orange", "Neon Pink", "Neon Yellow", "Olive", "Olive Drab Green", 
                            "Orange", "Purple", "Red", "Royal", "Sand", "Sangria", "Sapphire", "Silver", "Steel Blue", "Teal", 
                            "Team Purple", "True Royal", "White", "Woodland Brown", "Yellow"
                        ]
                        
                        if normalized_value in valid_colors:
                            state.order.color = normalized_value
                            updated = True
                    elif field == "decoration_location":
                        state.order.decoration_location = new_value
                        updated = True
                    elif field == "decoration_colors":
                        try:
                            num = int(new_value)
                            if 1 <= num <= 4:
                                state.order.decoration_colors = num
                                updated = True
                        except:
                            pass
                    elif field == "total_quantity":
                        if "quantity" in text:
                            if isinstance(new_value, str) and new_value.isdigit():
                                state.order.total_quantity = new_value
                                updated = True
                    elif field == "sizes":
                        if "size" in text:
                            sizes_dict = parse_sizes(new_value)  # Use existing parse function
                            state.order.sizes = [SizeQuantity(size=k, quantity=v) for k, v in sizes_dict.items()]
                            updated = True
                    elif field == "delivery_option":
                        if "delivery" in text:
                            if new_value.title() in ["Delivery", "Pickup"]:
                                state.order.delivery_option = new_value.title()
                                updated = True
                    elif field == "delivery_address":
                            if len(new_value) > 0:  # Ensure not empty
                                state.order.delivery_address = new_value
                                updated = True

                if logo_requested:
                    state.add_message(
                        "assistant",
                        "Sorry, the logo cannot be changed at this stage. You can update everything else, but not the logo."
                    )

                if updated:
                    summary_text = _render_summary_text(state)
                    state.add_message(
                        "assistant",
                        f"Updated! Here's the new summary:\n{summary_text}\n\nDoes this look good now? Reply **Confirm** to confirm, or specify more changes."
                    )
                elif not logo_requested:  # Only show "couldn't understand" if not logo
                    interrupt = await _check_interrupt(state)
                    if interrupt:
                        return state
                    state.add_message(
                        "assistant",
                        "Sorry, I couldn't understand that change. Please make sure you are specifying a valid field & input to update.. Or reply **yes** to confirm."
                    )

            except Exception as e:
                print(f"DEBUG: LLM parse error: {e}")
                state.add_message("assistant", "Sorry, error processing your request. Please try again.")

        state.last_user_message = ""
    return state


async def order_post_confirmation_node(state: SessionState) -> SessionState:
    """Handle post-order confirmation - ask what user wants to do next"""
    
    # First time entering
    if not state.context_data.get("post_order_question_shown"):
        state.context_data["post_order_question_shown"] = True
        state.last_user_message = ""
        return state
    
    # Process user choice
    if state.last_user_message:
        txt = state.last_user_message.strip().lower()
        
        # User wants to place another order
        if any(word in txt for word in ["order", "quote", "new", "another", "yes", "continue", "main"]):
            from models.session_state import OrderDetails
            
            # Clear everything
            state.context_data = {}
            state.order = OrderDetails()
            state.interrupted_from = None
            state.classified_intent = None
            
            # Set state to MAIN_MENU
            state.current_state = ConversationState.MAIN_MENU
            
            # ✅ Add the main menu message immediately
            state.add_message(
                role="assistant",
                content=(
                    "Great! I'd love to help with any questions you have. "
                    "I can also help you place a quote request if you want pricing.\n\n"
                    "How can I help you?"
                ),
            )
            
            state.last_user_message = ""
            return state
        
        # User wants to end
        elif any(word in txt for word in ["end", "bye", "goodbye", "done", "finish", "no"]):
            state.current_state = ConversationState.END
            state.add_message(
                role="assistant",
                content="Thank you for choosing Screen Printing NW! We'll be in touch soon. Have a great day! 👋"
            )
            state.last_user_message = ""
            return state
        
        else:
            state.add_message(
                role="assistant",
                content="Please reply:\n• **Main meun** to place another quote request\n• **End** to finish our chat"
            )
            state.last_user_message = ""
            return state
    
    state.last_user_message = ""
    return state

def route_from_post_confirmation(state: SessionState) -> str:
    """Route from post-confirmation node"""
    if state.current_state == ConversationState.MAIN_MENU:
        return "main_menu"
    elif state.current_state == ConversationState.END:
        return "end_conversation"
    return "end"  # Still waiting for choice

# ---------- Router ----------
def route_order_flow(state: SessionState) -> str:
    """
    Router hub that determines next order step.
    Handles resumption from interrupts.
    """
  
    if state.context_data.get("order_interrupted"):
        current = state.current_state.value
        
        if current == "HAS_QUESTIONS_ABOUT_PRODUCT":
            return "end"
        elif current == "WANTS_HUMAN":
            return "wants_human"
        elif current == "END":
            return "end_conversation"
        
    # 4. Check if just resumed from interrupt
    if state.context_data.get("just_resumed_from_interrupt"):
        state.context_data["just_resumed_from_interrupt"] = False
        current = state.current_state.value
        step_map = {
            "ORDER_CONTACT_FIRST_NAME": "order_contact_first_name",  # NEW
            "ORDER_CONTACT_LAST_NAME": "order_contact_last_name",    # NEW
            "ORDER_CONTACT_EMAIL": "order_contact_email",            # NEW
            "ORDER_CONTACT_PHONE": "order_contact_phone",            # NEW
            # "ORDER_CONTACT": "order_contact",
            "ORDER_ORGANIZATION": "order_organization",
            "ORDER_TYPE": "order_type",
            "ORDER_BUDGET": "order_budget",
            "ORDER_SERVICE": "order_service",
            "ORDER_APPAREL": "order_apparel",
            "ORDER_PRODUCT": "order_product",
            "ORDER_LOGO": "order_logo",
            "ORDER_DECORATION_LOCATION": "order_decoration_location",
            "ORDER_DECORATION_COLORS": "order_decoration_colors",
            "ORDER_QUANTITY": "order_quantity",
            "ORDER_SIZES": "order_sizes",
            "ORDER_DELIVERY": "order_delivery",
        }
        if current in step_map:
            return step_map[current]

    if not state.context_data.get("contact_first_name_complete"):
        state.current_state = ConversationState.ORDER_CONTACT_FIRST_NAME
        if state.context_data.get("contact_first_name_shown") and state.last_user_message:
            return "order_contact_first_name"
        elif not state.context_data.get("contact_first_name_shown"):
            return "order_contact_first_name"
        else:
            return "end"

    if not state.context_data.get("contact_last_name_complete"):
        state.current_state = ConversationState.ORDER_CONTACT_LAST_NAME
        if state.context_data.get("contact_last_name_shown") and state.last_user_message:
            return "order_contact_last_name"
        elif not state.context_data.get("contact_last_name_shown"):
            return "order_contact_last_name"
        else:
            return "end"

    if not state.context_data.get("contact_email_complete"):
        state.current_state = ConversationState.ORDER_CONTACT_EMAIL
        if state.context_data.get("contact_email_shown") and state.last_user_message:
            return "order_contact_email"
        elif not state.context_data.get("contact_email_shown"):
            return "order_contact_email"
        else:
            return "end"

    if not state.context_data.get("contact_phone_complete"):
        state.current_state = ConversationState.ORDER_CONTACT_PHONE
        if state.context_data.get("contact_phone_shown") and state.last_user_message:
            return "order_contact_phone"
        elif not state.context_data.get("contact_phone_shown"):
            return "order_contact_phone"
        else:
            return "end"

    if not state.context_data.get("org_complete"):
        state.current_state = ConversationState.ORDER_ORGANIZATION  # ✅ Set state
        if state.context_data.get("org_type_shown") and state.context_data.get("org_name_shown") and state.last_user_message:
            return "order_organization"
        elif state.context_data.get("org_type_shown") and not state.context_data.get("org_name_shown") and state.last_user_message:
            return "order_organization"
        else:
            return "order_organization" if not state.context_data.get("org_type_shown") else "end"

    if not state.context_data.get("type_complete"):
        state.current_state = ConversationState.ORDER_TYPE  # ✅ Set state
        return "order_type" if state.context_data.get("type_question_shown") and state.last_user_message else \
               ("order_type" if not state.context_data.get("type_question_shown") else "end")

    if not state.context_data.get("budget_complete"):
        state.current_state = ConversationState.ORDER_BUDGET  # ✅ Set state
        return "order_budget" if state.context_data.get("budget_question_shown") and state.last_user_message else \
               ("order_budget" if not state.context_data.get("budget_question_shown") else "end")

    if not state.context_data.get("service_complete"):
        state.current_state = ConversationState.ORDER_SERVICE  # ✅ Set state
        return "order_service" if state.context_data.get("service_question_shown") and state.last_user_message else \
               ("order_service" if not state.context_data.get("service_question_shown") else "end")

    if not state.context_data.get("apparel_complete"):
        state.current_state = ConversationState.ORDER_APPAREL  # ✅ Set state
        return "order_apparel" if state.context_data.get("apparel_question_shown") and state.last_user_message else \
               ("order_apparel" if not state.context_data.get("apparel_question_shown") else "end")

    if not state.context_data.get("product_complete"):
        state.current_state = ConversationState.ORDER_PRODUCT  # ✅ Set state
        return "order_product" if state.context_data.get("product_question_shown") and state.last_user_message else \
               ("order_product" if not state.context_data.get("product_question_shown") else "end")

    if not state.context_data.get("logo_complete"):
        state.current_state = ConversationState.ORDER_LOGO  # ✅ Set state
        print(f"DEBUG router: logo not complete, checking state...")
        if state.context_data.get("logo_question_shown") and state.last_user_message:
            return "order_logo"
        elif not state.context_data.get("logo_question_shown"):
            return "order_logo"
        else:
            return "end"
    
    print(f"DEBUG router: logo complete, moving to decoration_location")

    if not state.context_data.get("decoration_location_complete"):
        state.current_state = ConversationState.ORDER_DECORATION_LOCATION  # ✅ Set state
        return "order_decoration_location" if state.context_data.get("decoration_location_shown") and state.last_user_message else \
            ("order_decoration_location" if not state.context_data.get("decoration_location_shown") else "end")

    if not state.context_data.get("decoration_colors_complete"):
        state.current_state = ConversationState.ORDER_DECORATION_COLORS  # ✅ Set state
        return "order_decoration_colors" if state.context_data.get("decoration_colors_shown") and state.last_user_message else \
            ("order_decoration_colors" if not state.context_data.get("decoration_colors_shown") else "end")

    if not state.context_data.get("qty_complete"):
        state.current_state = ConversationState.ORDER_QUANTITY  # ✅ Set state
        return "order_quantity" if state.context_data.get("qty_question_shown") and state.last_user_message else \
               ("order_quantity" if not state.context_data.get("qty_question_shown") else "end")

    if not state.context_data.get("sizes_complete"):
        state.current_state = ConversationState.ORDER_SIZES  # ✅ Set state
        if state.context_data.get("sizes_pending") and state.last_user_message:
            txt = state.last_user_message.strip().lower()
            if "use sizes total" in txt:
                pending = state.context_data.pop("sizes_pending")
                state.order.sizes = [SizeQuantity(size=k.upper(), quantity=v) for k, v in pending.items()]
                state.context_data["sizes_complete"] = True
            else:
                return "order_sizes"
        return "order_sizes" if (state.context_data.get("sizes_question_shown") and state.last_user_message) else \
               ("order_sizes" if not state.context_data.get("sizes_question_shown") else "end")

    if not state.context_data.get("delivery_complete"):
        state.current_state = ConversationState.ORDER_DELIVERY  # ✅ Set state
        if state.context_data.get("awaiting_address"):
            state.current_state = ConversationState.ORDER_DELIVERY_ADDRESS  # ✅ Override if address needed
            return "order_delivery_address" if (state.context_data.get("address_question_shown") and state.last_user_message) else \
                   ("order_delivery_address" if not state.context_data.get("address_question_shown") else "end")
        return "order_delivery" if (state.context_data.get("delivery_question_shown") and state.last_user_message) else \
               ("order_delivery" if not state.context_data.get("delivery_question_shown") else "end")
  
    if not state.context_data.get("order_complete"):  # Only go to summary if not complete
            state.current_state = ConversationState.ORDER_SUMMARY
            return "order_summary"

    # ✅ CRITICAL: Set state to ORDER_SUMMARY when everything is complete
    state.current_state = ConversationState.ORDER_SUMMARY
    return "order_summary"
