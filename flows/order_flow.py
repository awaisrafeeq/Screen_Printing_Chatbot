from __future__ import annotations
from typing import Dict, List, Tuple, Optional
import re

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
    return any(w in t for w in ["end", "cancel", "stop", "goodbye", "bye", "finish chat"])

async def _check_interrupt(state: SessionState) -> Optional[ConversationState]:
    if not state.last_user_message:
        return None

    text = state.last_user_message.strip().lower()

    if any(word in text for word in ["product", "question", "price", "pricing", "cost", 
                                      "shirt", "hoodie", "embroidery", "screen print"]):
        state.interrupted_from = state.current_state
        state.context_data["order_interrupted"] = True
        state.context_data["interrupt_reason"] = "product_questions"
        
        state.context_data["interrupt_snapshot"] = {
            "contact_question_shown": state.context_data.get("contact_question_shown"),
            "org_question_shown": state.context_data.get("org_question_shown"),
            "type_question_shown": state.context_data.get("type_question_shown"),
        }
        
        state.classified_intent = Intent.HAS_QUESTIONS_ABOUT_PRODUCT
        state.current_state = ConversationState.HAS_QUESTIONS_ABOUT_PRODUCT
        state.context_data["intent_confidence"] = 0.95
        state.context_data["intent_reasoning"] = "keyword: product question during order"
        
        
        state.add_message(
            role="assistant",
            content="Sure! I'll help answer your product questions. What would you like to know?"
        )
        state.context_data["product_question_prompted"] = True  
        
        return ConversationState.HAS_QUESTIONS_ABOUT_PRODUCT

    if _wants_human(text):
        state.interrupted_from = state.current_state
        state.context_data["order_interrupted"] = True
        state.context_data["interrupt_reason"] = "wants_human"
        
        state.classified_intent = Intent.WANTS_HUMAN
        state.current_state = ConversationState.WANTS_HUMAN
        state.context_data["intent_confidence"] = 1.0
        state.context_data["intent_reasoning"] = "keyword: wants human during order"
        
        return ConversationState.WANTS_HUMAN

    if _wants_end(text):
        state.interrupted_from = state.current_state
        state.context_data["order_interrupted"] = True
        state.context_data["interrupt_reason"] = "end_conversation"
        
        state.classified_intent = Intent.END_CONVERSATION
        state.current_state = ConversationState.END
        state.context_data["intent_confidence"] = 1.0
        state.context_data["intent_reasoning"] = "keyword: end conversation during order"
        
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
    if o.contact.email:
        contact_line = contact_line.lstrip(', ')  
    
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

def _send_summary_to_customer(state: SessionState) -> bool:
    to_addr = (state.order.contact.email or "").strip()
    if not to_addr:
        return False

    subject = "Your Screen Printing NW Quote Request Summary"
    md = _render_summary_text(state)

    body = md.replace("**", "")
    link = state.context_data.get("logo_view_link", "").strip()
    if link:
        body = body.replace(f"[View]({link})", f"View: {link}")

    return send_email(to_addr, subject, body)

EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
PHONE_RE = re.compile(r"(?:\+?\d{1,3}[-.\s()]*)?(?:\d[-.\s()]*){7,}", re.I)

def parse_contact_info(text: str) -> Dict[str, Optional[str]]:
    out = {"first_name": None, "last_name": None, "email": None, "phone": None}
    if not text:
        return out

    m = EMAIL_RE.search(text)
    if m:
        out["email"] = m.group(0).strip()

    m = PHONE_RE.search(text)
    if m:
        phone = re.sub(r"[^0-9+]", "", m.group(0))
        if len(re.sub(r"\D", "", phone)) >= 8:
            out["phone"] = phone

    lowered = text.lower()
    name_match = re.search(
        r"(?:my\s+name\s+is|name\s+is|i\s+am|i'm)\s+([A-Za-z][A-Za-z\-' ]{1,60})",
        lowered, re.I
    )
    candidate = name_match.group(1) if name_match else None

    if not candidate and out["email"]:
        before_email = text.split(out["email"])[0]
        parts = [p.strip() for p in re.split(r"[,\-]\s*", before_email) if p.strip()]
        if parts:
            candidate = parts[-1]

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

async def order_contact_first_name_node(state: SessionState) -> SessionState:
    interrupt = await _check_interrupt(state)
    if interrupt:
        return state

    if state.last_user_message == "__RESUME__":
        state.last_user_message = ""  
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
    interrupt = await _check_interrupt(state)
    if interrupt:
        return state

    if state.last_user_message == "__RESUME__":
        state.last_user_message = ""  
        state.context_data["contact_last_name_shown"] = False  

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
    interrupt = await _check_interrupt(state)
    if interrupt:
        return state

    if state.last_user_message == "__RESUME__":
        state.last_user_message = ""  
        state.context_data["contact_email_shown"] = False  

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
            if email and "@" in email:  
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
    interrupt = await _check_interrupt(state)
    if interrupt:
        return state

    if state.last_user_message == "__RESUME__":
        state.last_user_message = ""  
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
                state.context_data["contact_complete"] = True  
                state.add_message("assistant", "Perfect, thanks for your contact details.")
            else:
                state.add_message("assistant", "Please provide your phone number.")
        except Exception as e:
            state.add_message("assistant", "Sorry, I couldn't process that. What's your phone?")
        state.last_user_message = ""
    return state

async def order_organization_node(state: SessionState) -> SessionState:
    interrupt = await _check_interrupt(state)
    if interrupt:
        return state

    if state.last_user_message == "__RESUME__":
        state.last_user_message = ""  
        state.context_data["org_type_shown"] = False

    if not state.context_data.get("org_type_shown"):
        state.add_message(
            "assistant",
            "Is this order for a business, organization, or team? Reply **yes** or **personal**."
        )
        state.context_data["org_type_shown"] = True
        state.last_user_message = ""
        return state

    if state.last_user_message and state.context_data.get("org_type_shown") and not state.context_data.get("org_name_shown"):
        text = state.last_user_message.strip().lower()
        
        if any(word in text for word in ["no", "personal"]):
            state.order.organization.is_business = False
            state.order.organization.name = None
            state.context_data["org_complete"] = True
            state.last_user_message = ""
            return state
            
        elif any(word in text for word in ["yes", "business", "organization", "team"]):
            state.add_message(
                "assistant",
                "What is the name of your business/organization/team?"
            )
            state.context_data["org_name_shown"] = True
            state.last_user_message = ""
            return state
            
        else:
            state.add_message(
                "assistant",
                "Please reply **Yes** if it's for a business/organization/team, or **No** if personal."
            )
            state.last_user_message = ""  
            return state

    if state.last_user_message and state.context_data.get("org_name_shown"):
        name = state.last_user_message.strip()
        if name:  
            state.order.organization.is_business = True
            state.order.organization.name = name
            state.context_data["org_complete"] = True
            state.last_user_message = ""  
        else:
            state.add_message(
                "assistant",
                "Please provide the name of your business/organization/team."
            )
            state.last_user_message = ""  
        return state

    state.last_user_message = ""  
    return state

async def order_type_node(state: SessionState) -> SessionState:
    
    interrupt = await _check_interrupt(state)
    if interrupt:
        state.last_user_message = ""
        return state
    if state.last_user_message == "__RESUME__":
        state.last_user_message = ""  
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
        state.last_user_message = ""  
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
        state.last_user_message = ""  
        state.context_data["service_question_shown"] = False

    if state.last_user_message == "__RESUME__":
        state.last_user_message = ""  
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
        state.last_user_message = ""  
        state.context_data["apparel_question_shown"] = False

    if state.last_user_message == "__RESUME__":
        state.last_user_message = ""  
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

        state.order.apparel_category = product  
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
    
    if state.last_user_message == "__RESUME__":
        state.last_user_message = ""  
        state.context_data["product_question_shown"] = False

    if state.context_data.get("product_question_shown") and not state.last_user_message:
        return state

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
    if state.last_user_message == "__RESUME__":
        state.last_user_message = ""  
        state.context_data["logo_complete"] = False  

    if state.context_data.get("logo_complete"):
        state.last_user_message = ""
        return state

    if state.context_data.get("logo_question_shown") and not state.last_user_message:
        return state

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

    if state.last_user_message:
        txt = state.last_user_message.strip().lower()
        print(f"DEBUG order_logo_node: user message = '{txt}'")

        if txt in ["skip", "no logo", "no", "none"]:
            state.context_data["logo_complete"] = True
            state.context_data["awaiting_upload"] = False
            state.last_user_message = ""
            return state

        if txt.startswith("upload:") or "drive_file_id" in txt or "upload_complete" in txt:
            try:
                payload = json.loads(txt)
                file_id = payload.get("file_id")
                view_link = payload.get("view_link") or payload.get("web_view_link")
                if file_id:
                    state.context_data["logo_file_id"] = file_id
                if view_link:
                    state.context_data["logo_view_link"] = view_link

                state.context_data["logo_complete"] = True
                state.context_data["awaiting_upload"] = False
                state.last_user_message = ""
                return state
            except Exception as e:
                print(f"DEBUG order_logo_node: failed to parse upload payload: {e}")
                state.add_message(
                    role="assistant",
                    content="I had trouble reading that upload. Please try again or say **Skip** to continue without a logo."
                )
                state.last_user_message = ""
                return state

        state.add_message(
            role="assistant",
            content="Please either upload a logo file or type **Skip** to continue without one."
        )
        state.last_user_message = ""
        return state

    state.last_user_message = ""
    return state

async def order_decoration_location_node(state: SessionState) -> SessionState:

    interrupt = await _check_interrupt(state)
    if interrupt:
        state.last_user_message = ""
        return state

    if state.last_user_message == "__RESUME__":
        state.last_user_message = ""  
        state.context_data["decoration_location_shown"] = False

    if state.context_data.get("decoration_location_shown") and not state.last_user_message:
        return state

    if not state.context_data.get("decoration_location_shown"):
        state.add_message(
            role="assistant",
            content=(
                "Where on the apparel would you like your design?\n"
                "For example: `Left chest`, `Full front`, `Full back`, `Sleeve`, etc."
            ),
        )
        state.context_data["decoration_location_shown"] = True
        state.last_user_message = ""
        return state

    if state.last_user_message:
        location = state.last_user_message.strip()
        if location:
            state.order.decoration_location = location
            state.context_data["decoration_location_complete"] = True
            state.last_user_message = ""
            return state
        else:
            state.add_message(
                role="assistant",
                content="Please describe where on the apparel you want your design (e.g., `Left chest`, `Full front`)."
            )
            state.last_user_message = ""
            return state

    state.last_user_message = ""
    return state

async def order_decoration_colors_node(state: SessionState) -> SessionState:

    interrupt = await _check_interrupt(state)
    if interrupt:
        state.last_user_message = ""
        return state

    if state.last_user_message == "__RESUME__":
        state.last_user_message = ""  
        state.context_data["decoration_colors_shown"] = False

    if state.context_data.get("decoration_colors_shown") and not state.last_user_message:
        return state

    if not state.context_data.get("decoration_colors_shown"):
        state.add_message(
            role="assistant",
            content=(
                "How many colors are in your design? (For example: `1`, `2–3`, or `Full color`)."
            ),
        )
        state.context_data["decoration_colors_shown"] = True
        state.last_user_message = ""
        return state

    if state.last_user_message:
        colors = state.last_user_message.strip()
        if colors:
            state.order.decoration_colors = colors
            state.context_data["decoration_colors_complete"] = True
            state.last_user_message = ""
            return state
        else:
            state.add_message(
                role="assistant",
                content="Please tell me how many colors are in your design."
            )
            state.last_user_message = ""
            return state

    state.last_user_message = ""
    return state

async def order_quantity_node(state: SessionState) -> SessionState:

    interrupt = await _check_interrupt(state)
    if interrupt:
        state.last_user_message = ""
        return state

    if state.last_user_message == "__RESUME__":
        state.last_user_message = ""  
        state.context_data["quantity_question_shown"] = False
        state.context_data["quantity_confirm_shown"] = False

    if state.context_data.get("quantity_confirm_shown") and not state.last_user_message:
        return state

    if state.context_data.get("quantity_question_shown") and not state.context_data.get("quantity_confirm_shown") and not state.last_user_message:
        return state

    if not state.context_data.get("quantity_question_shown"):
        state.add_message(
            role="assistant",
            content=(
                "Approximately how many pieces do you need?\n"
                "You can answer with a single number (e.g., `25`) or a range (e.g., `20–30`)."
            ),
        )
        state.context_data["quantity_question_shown"] = True
        state.context_data["quantity_confirm_shown"] = False
        state.last_user_message = ""
        return state

    if not state.context_data.get("quantity_confirm_shown") and state.last_user_message:
        raw = state.last_user_message.strip()
        nums = re.findall(r"\d{1,4}", raw)
        if nums:
            if len(nums) == 1:
                total_qty = int(nums[0])
                state.order.total_quantity = total_qty
            else:
                total_qty = int(round(sum(map(int, nums)) / len(nums)))
                state.order.total_quantity = total_qty

            state.context_data["parsed_quantity"] = total_qty
            state.context_data["quantity_confirm_shown"] = True
            state.last_user_message = ""

            state.add_message(
                role="assistant",
                content=(
                    f"Got it. I'll use **{total_qty}** as your approximate quantity.\n"
                    "If this looks correct, reply **Yes**. If not, type the quantity again."
                ),
            )
            return state
        else:
            state.add_message(
                role="assistant",
                content="I couldn't find a number there. Please tell me approximately how many pieces you need."
            )
            state.last_user_message = ""
            return state

    if state.context_data.get("quantity_confirm_shown") and state.last_user_message:
        txt = state.last_user_message.strip().lower()
        if txt in ["yes", "y", "correct", "ok", "okay", "looks good"]:
            state.context_data["quantity_complete"] = True
            state.last_user_message = ""
            return state
        else:
            state.context_data["quantity_question_shown"] = False
            state.context_data["quantity_confirm_shown"] = False
            state.order.total_quantity = None
            state.last_user_message = ""
            return state

    state.last_user_message = ""
    return state

async def order_sizes_node(state: SessionState) -> SessionState:

    interrupt = await _check_interrupt(state)
    if interrupt:
        state.last_user_message = ""
        return state

    if state.last_user_message == "__RESUME__":
        state.last_user_message = ""  
        state.context_data["sizes_question_shown"] = False
        state.context_data["sizes_parsed"] = False

    if state.context_data.get("sizes_question_shown") and not state.last_user_message:
        return state

    if not state.context_data.get("sizes_question_shown"):
        state.add_message(
            role="assistant",
            content=(
                "Please provide sizes and quantities, for example:\n"
                "`S:10, M:15, L:5, XL:2` or `10 small, 15 medium, 5 large, 2 XL`."
            ),
        )
        state.context_data["sizes_question_shown"] = True
        state.context_data["sizes_parsed"] = False
        state.last_user_message = ""
        return state

    if state.last_user_message and not state.context_data.get("sizes_parsed"):
        parsed = parse_sizes(state.last_user_message)
        if parsed:
            total = sum(parsed.values())
            state.order.sizes = [SizeQuantity(size=k, quantity=v) for k, v in parsed.items()]
            state.order.total_quantity = total
            state.context_data["sizes_parsed"] = True
            state.last_user_message = ""

            preview = ", ".join(f"{k}:{v}" for k, v in parsed.items())
            state.add_message(
                role="assistant",
                content=(
                    f"I've set your sizes as: {preview} (total {total}).\n"
                    "If this looks correct, reply **Yes**. If not, type your sizes again."
                ),
            )
            return state
        else:
            state.add_message(
                role="assistant",
                content="I couldn't read the sizes from that. Please try again, like `S:10, M:15, L:5, XL:2`."
            )
            state.last_user_message = ""
            return state

    if state.context_data.get("sizes_parsed") and state.last_user_message:
        txt = state.last_user_message.strip().lower()
        if txt in ["yes", "y", "correct", "ok", "okay", "looks good"]:
            state.context_data["sizes_complete"] = True
            state.last_user_message = ""
            return state
        else:
            state.context_data["sizes_question_shown"] = False
            state.context_data["sizes_parsed"] = False
            state.order.sizes = None
            state.last_user_message = ""
            return state

    state.last_user_message = ""
    return state

async def order_delivery_node(state: SessionState) -> SessionState:

    interrupt = await _check_interrupt(state)
    if interrupt:
        state.last_user_message = ""
        return state

    if state.last_user_message == "__RESUME__":
        state.last_user_message = ""  
        state.context_data["delivery_question_shown"] = False

    if state.context_data.get("delivery_question_shown") and not state.last_user_message:
        return state

    if not state.context_data.get("delivery_question_shown"):
        state.add_message(
            role="assistant",
            content=(
                "Do you prefer **Delivery** or **Pickup**?\n"
                "Reply with `Delivery` (and later we'll ask for your address) or `Pickup`."
            ),
        )
        state.context_data["delivery_question_shown"] = True
        state.last_user_message = ""
        return state

    if state.last_user_message:
        txt = state.last_user_message.strip().lower()
        if "deliver" in txt:
            state.order.delivery_option = "Delivery"
            state.context_data["delivery_complete"] = True
            state.last_user_message = ""
            return state
        elif "pickup" in txt or "pick up" in txt:
            state.order.delivery_option = "Pickup"
            state.context_data["delivery_complete"] = True
            state.last_user_message = ""
            return state
        else:
            state.add_message(
                role="assistant",
                content="Please reply with either **Delivery** or **Pickup**."
            )
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
        state.last_user_message = ""  
        state.context_data["delivery_address_question_shown"] = False

    if state.order.delivery_option != "Delivery":
        state.context_data["delivery_address_complete"] = True
        state.last_user_message = ""
        return state

    if state.context_data.get("delivery_address_question_shown") and not state.last_user_message:
        return state

    if not state.context_data.get("delivery_address_question_shown"):
        state.add_message(
            role="assistant",
            content="Please provide your full delivery address (including city and postal code)."
        )
        state.context_data["delivery_address_question_shown"] = True
        state.last_user_message = ""
        return state

    if state.last_user_message:
        address = state.last_user_message.strip()
        if address:
            state.order.delivery_address = address
            state.context_data["delivery_address_complete"] = True
            state.last_user_message = ""
            return state
        else:
            state.add_message(
                role="assistant",
                content="Please provide a valid delivery address."
            )
            state.last_user_message = ""
            return state

    state.last_user_message = ""
    return state

async def order_notes_node(state: SessionState) -> SessionState:

    interrupt = await _check_interrupt(state)
    if interrupt:
        state.last_user_message = ""
        return state

    if state.last_user_message == "__RESUME__":
        state.last_user_message = ""  
        state.context_data["notes_question_shown"] = False

    if state.context_data.get("notes_question_shown") and not state.last_user_message:
        return state

    if not state.context_data.get("notes_question_shown"):
        state.add_message(
            role="assistant",
            content=(
                "Any additional notes, deadlines, or details you'd like us to know?"
                "You can also say **No** if there's nothing else."
            ),
        )
        state.context_data["notes_question_shown"] = True
        state.last_user_message = ""
        return state

    if state.last_user_message:
        txt = state.last_user_message.strip()
        if txt.lower() in ["no", "none", "nothing"]:
            state.order.notes = None
        else:
            state.order.notes = txt
        state.context_data["notes_complete"] = True
        state.last_user_message = ""
        return state

    state.last_user_message = ""
    return state

async def order_summary_node(state: SessionState) -> SessionState:

    interrupt = await _check_interrupt(state)
    if interrupt:
        state.last_user_message = ""
        return state

    if state.last_user_message == "__RESUME__":
        state.last_user_message = ""  
        state.context_data["summary_shown"] = False
        state.context_data["summary_confirmation_shown"] = False

    if state.context_data.get("summary_confirmation_shown") and not state.last_user_message:
        return state

    if not state.context_data.get("summary_shown"):
        summary_text = _render_summary_text(state)
        state.add_message(
            role="assistant",
            content=(
                summary_text + "\n\n"
                "Please review your details above.\n"
                "If everything looks correct, reply **Confirm**.\n"
                "If you want to change something, tell me what to update."
            ),
        )
        state.context_data["summary_shown"] = True
        state.context_data["summary_confirmation_shown"] = False
        state.last_user_message = ""
        return state

    if state.last_user_message and not state.context_data.get("summary_confirmation_shown"):
        txt = state.last_user_message.strip().lower()
        if txt in ["confirm", "yes", "looks good", "all good", "ok", "okay"]:
            state.context_data["summary_confirmation_shown"] = True
            state.last_user_message = ""
            return state
        else:
            change_prompt = f"""
You are a change parser for an order summary. Given the user message: '{text}'

Extract any requested changes as JSON:
- If it's a change request, output: {{"changes": [{{"field": "field_name", "new_value": "value"}}]}} (array for multiple)
- Valid fields: first_name, last_name, email, phone, organization, order_type, budget_range, service_type,
  product_name, color, decoration_location, decoration_colors, total_quantity, sizes, delivery_option, delivery_address, notes
- If no changes are requested, output: {{"changes": []}}.
"""
            text = state.last_user_message.strip()
            try:
                classification = _classifier.classify(text, system_prompt=change_prompt)
                changes = classification.get("changes", [])
                for c in changes:
                    field = c.get("field")
                    val = c.get("new_value")
                    if field == "first_name":
                        state.order.contact.first_name = val
                    elif field == "last_name":
                        state.order.contact.last_name = val
                    elif field == "email":
                        state.order.contact.email = val
                    elif field == "phone":
                        state.order.contact.phone = val
                    elif field == "organization":
                        state.order.organization.name = val
                    elif field == "order_type":
                        state.order.order_type = val
                    elif field == "budget_range":
                        state.order.budget_range = val
                    elif field == "service_type":
                        state.order.service_type = val
                    elif field == "product_name":
                        state.order.product_name = val
                    elif field == "color":
                        state.order.color = val
                    elif field == "decoration_location":
                        state.order.decoration_location = val
                    elif field == "decoration_colors":
                        state.order.decoration_colors = val
                    elif field == "total_quantity":
                        try:
                            state.order.total_quantity = int(val)
                        except:
                            pass
                    elif field == "delivery_option":
                        state.order.delivery_option = val
                    elif field == "delivery_address":
                        state.order.delivery_address = val
                    elif field == "notes":
                        state.order.notes = val

                new_summary = _render_summary_text(state)
                state.add_message(
                    role="assistant",
                    content=(
                        "I've updated your details. Here's the new summary:\n\n"
                        f"{new_summary}\n\n"
                        "If this looks correct, reply **Confirm**. If you want more changes, let me know."
                    ),
                )
            except Exception as e:
                print(f"DEBUG summary change parsing error: {e}")
                state.add_message(
                    role="assistant",
                    content=(
                        "I couldn't automatically interpret those changes.\n"
                        "Please clearly specify what to change, for example:\n"
                        "`Change quantity to 40` or `Make it hoodies instead of t-shirts`."
                    ),
                )
            state.last_user_message = ""
            return state

    if state.context_data.get("summary_confirmation_shown") and state.last_user_message:
        txt = state.last_user_message.strip().lower()
        if txt in ["confirm", "yes", "looks good", "all good", "ok", "okay"]:
            state.context_data["summary_complete"] = True
            state.last_user_message = ""
            return state
        else:
            state.context_data["summary_shown"] = False
            state.context_data["summary_confirmation_shown"] = False
            state.last_user_message = ""
            return state

    state.last_user_message = ""
    return state

async def order_final_confirmation_node(state: SessionState) -> SessionState:
    interrupt = await _check_interrupt(state)
    if interrupt:
        state.last_user_message = ""
        return state

    state.context_data.setdefault("quote_email_sent", False)
    state.context_data.setdefault("quote_notification_sent", False)

    if not state.context_data["quote_email_sent"]:
        try:
            sent_customer = _send_summary_to_customer(state)
            state.context_data["quote_email_sent"] = bool(sent_customer)
        except Exception as e:
            print(f"DEBUG email_to_customer_error: {e}")
            state.context_data["quote_email_sent"] = False

    if not state.context_data["quote_notification_sent"]:
        try:
            admin_email = os.getenv("ADMIN_QUOTE_EMAIL", "").strip()
            if admin_email:
                subject = "New Quote Request from Screen Printing Chatbot"
                body = _render_summary_text(state)
                ok = send_email(admin_email, subject, body)
                state.context_data["quote_notification_sent"] = bool(ok)
        except Exception as e:
            print(f"DEBUG email_to_admin_error: {e}")
            state.context_data["quote_notification_sent"] = False

    if not state.context_data.get("final_message_shown"):
        if state.context_data["quote_email_sent"]:
            msg = (
                "You're all set. We've sent a copy of your quote request summary to your email.\n"
                "Our team will review your details and follow up with pricing and next steps."
            )
        else:
            msg = (
                "You're all set. Our team will review your quote request and follow up with pricing and next steps.\n"
                "If you don't hear from us, please double-check that your email address was entered correctly."
            )
        state.add_message("assistant", msg)
        state.context_data["final_message_shown"] = True
        state.last_user_message = ""
        return state

    state.last_user_message = ""
    return state

async def handle_file_upload(state: SessionState, file_path: str, mime_type: str, original_name: str) -> SessionState:
    try:
        result = await upload_to_drive(file_path, mime_type, original_name)
        file_id = result.get("file_id")
        view_link = result.get("view_link")
        if file_id:
            state.context_data["logo_file_id"] = file_id
        if view_link:
            state.context_data["logo_view_link"] = view_link

        state.context_data["logo_complete"] = True
        state.context_data["awaiting_upload"] = False

        state.add_message(
            role="assistant",
            content="Thanks. I've received your logo file."
        )
    except Exception as e:
        print(f"DEBUG handle_file_upload error: {e}")
        state.add_message(
            role="assistant",
            content="I had trouble saving that file. Please try again or type **Skip** to continue without a logo."
        )
    finally:
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception as e:
            print(f"DEBUG handle_file_upload cleanup error: {e}")

    return state

def _is_contact_complete(state: SessionState) -> bool:
    o = state.order
    return bool(
        (o.contact.first_name or "").strip() and
        (o.contact.last_name or "").strip() and
        (o.contact.email or "").strip() and
        (o.contact.phone or "").strip()
    )

def _is_organization_complete(state: SessionState) -> bool:
    return state.context_data.get("org_complete", False)

def _is_type_complete(state: SessionState) -> bool:
    return bool((state.order.order_type or "").strip())

def _is_budget_complete(state: SessionState) -> bool:
    return bool((state.order.budget_range or "").strip())

def _is_service_complete(state: SessionState) -> bool:
    return bool((state.order.service_type or "").strip())

def _is_apparel_complete(state: SessionState) -> bool:
    return bool((state.order.product_name or "").strip())

def _is_product_complete(state: SessionState) -> bool:
    return state.context_data.get("product_complete", False)

def _is_logo_complete(state: SessionState) -> bool:
    return state.context_data.get("logo_complete", False)

def _is_decoration_info_complete(state: SessionState) -> bool:
    return bool((state.order.decoration_location or "").strip()) and bool((state.order.decoration_colors or "").strip())

def _is_quantity_complete(state: SessionState) -> bool:
    return state.context_data.get("quantity_complete", False)

def _is_sizes_complete(state: SessionState) -> bool:
    return state.context_data.get("sizes_complete", False)

def _is_delivery_option_complete(state: SessionState) -> bool:
    return state.context_data.get("delivery_complete", False)

def _is_delivery_address_complete(state: SessionState) -> bool:
    if state.order.delivery_option != "Delivery":
        return True
    return state.context_data.get("delivery_address_complete", False)

def _is_notes_complete(state: SessionState) -> bool:
    return state.context_data.get("notes_complete", False)

def _is_summary_complete(state: SessionState) -> bool:
    return state.context_data.get("summary_complete", False)

def is_order_flow_complete(state: SessionState) -> bool:
    return all([
        _is_contact_complete(state),
        _is_organization_complete(state),
        _is_type_complete(state),
        _is_budget_complete(state),
        _is_service_complete(state),
        _is_apparel_complete(state),
        _is_product_complete(state),
        _is_logo_complete(state),
        _is_decoration_info_complete(state),
        _is_quantity_complete(state),
        _is_sizes_complete(state),
        _is_delivery_option_complete(state),
        _is_delivery_address_complete(state),
        _is_notes_complete(state),
        _is_summary_complete(state),
    ])

def get_next_order_state(state: SessionState) -> str:
    if not _is_contact_complete(state):
        if not state.context_data.get("contact_first_name_complete"):
            return "order_contact_first_name"
        if not state.context_data.get("contact_last_name_complete"):
            return "order_contact_last_name"
        if not state.context_data.get("contact_email_complete"):
            return "order_contact_email"
        if not state.context_data.get("contact_phone_complete"):
            return "order_contact_phone"

    if not _is_organization_complete(state):
        return "order_organization"

    if not _is_type_complete(state):
        return "order_type"

    if not _is_budget_complete(state):
        return "order_budget"

    if not _is_service_complete(state):
        return "order_service"

    if not _is_apparel_complete(state):
        return "order_apparel"

    if not _is_product_complete(state):
        return "order_product"

    if not _is_logo_complete(state):
        return "order_logo"

    if not _is_decoration_info_complete(state):
        if not (state.order.decoration_location or "").strip():
            return "order_decoration_location"
        if not (state.order.decoration_colors or "").strip():
            return "order_decoration_colors"

    if not _is_quantity_complete(state):
        return "order_quantity"

    if not _is_sizes_complete(state):
        return "order_sizes"

    if not _is_delivery_option_complete(state):
        return "order_delivery"

    if not _is_delivery_address_complete(state):
        return "order_delivery_address"

    if not _is_notes_complete(state):
        return "order_notes"

    if not _is_summary_complete(state):
        return "order_summary"

    if not state.context_data.get("final_message_shown"):
        return "order_final_confirmation"

    state.current_state = ConversationState.ORDER_SUMMARY
    return "order_summary"
