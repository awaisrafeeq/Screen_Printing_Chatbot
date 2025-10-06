from __future__ import annotations
from typing import Dict, List, Tuple, Optional
import re
# add near other imports
from flows.email_sender import send_email

from models.session_state import (
    SessionState,
    ConversationState,
    Intent,
    SizeQuantity,
)
from services.intent_classifier import IntentClassifier
import os
import asyncio
from flows.oauth_uploader import upload_to_drive



# ---------- Utilities ----------

INTERRUPT_INTENTS = {Intent.WANTS_HUMAN, Intent.END_CONVERSATION}

PRODUCT_CATALOG = {
    "t-shirt": ["white", "black", "navy", "red", "gray"],
    "hoodie": ["black", "gray", "navy"],
    "cap": ["black", "white", "navy", "khaki"],
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
            # ... save all question_shown flags
        }
        
        state.classified_intent = Intent.HAS_QUESTIONS_ABOUT_PRODUCT
        state.current_state = ConversationState.HAS_QUESTIONS_ABOUT_PRODUCT
        state.context_data["intent_confidence"] = 0.95
        state.context_data["intent_reasoning"] = "keyword: product question during order"
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
        return ConversationState.END
    
    # # Otherwise, classify but don't jump unless future logic needs it
    # try:
    #     result = await _classifier.classify_intent(text, context={"current_state": state.current_state.value})
    #     # Store for debugging/telemetry only
    #     state.context_data["intent_confidence"] = float(result.get("confidence", 0.0) or 0.0)
    #     state.context_data["intent_reasoning"] = result.get("reasoning", "") or ""
    #     # (We intentionally DO NOT change current_state here unless explicit keywords matched)
    # except Exception:
    #     pass

    return None

def _render_summary_text(state: SessionState) -> str:
    o = state.order
    sizes_line = ", ".join(f"{s.size}:{s.quantity}" for s in (o.sizes or [])) if o.sizes else "—"
    color_line = o.color.title() if o.color else "No preference"
    logo_line = "Uploaded" if state.context_data.get("logo_file_id") else "—"
    if state.context_data.get("logo_view_link"):
        logo_line = f"[View]({state.context_data['logo_view_link']})"
    return (
        "**Quote Request Summary**\n"
        f"- Name: {(o.contact.first_name or '')} {(o.contact.last_name or '')}\n"
        f"- Email / Phone: {o.contact.email or '—'} / {o.contact.phone or '—'}\n"
        f"- Organization: {o.organization.name or ('Personal' if o.organization.is_business is False else '—')}\n"
        f"- Order Type: {o.order_type or '—'}\n"
        f"- Budget: {o.budget_range or '—'}\n"
        f"- Service: {o.service_type or '—'}\n"
        f"- Product: {o.product_name.title() if o.product_name else '—'}\n"
        f"- Color: {color_line}\n"
        f"- Decoration Location: {o.decoration_location or '—'}\n"  # ADD
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

    # Render markdown summary
    md = _render_summary_text(state)

    # Plain-text body: strip bold and convert any [View](URL) to "View: URL"
    body = md.replace("**", "")
    link = (state.context_data.get("logo_view_link") or "").strip()
    if link:
        body = body.replace(f"[View]({link})", f"View: {link}")

    return send_email(to_addr, subject, body)

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

# ---------- Order nodes ----------

async def order_contact_node(state: SessionState) -> SessionState:

    if state.context_data.get("contact_question_shown") and not state.last_user_message:
        return state  # Question asked, waiting for response

    interrupt = await _check_interrupt(state)
    if interrupt:
        state.last_user_message = ""
        return state

    if not state.context_data.get("contact_question_shown"):
        state.add_message(
            role="assistant",
            content=(
                "Great — let's start with your contact details.\n"
                "Please provide **Full Name**, **Email**, and **Phone** (with country code).\n"
                "You can paste them like: `John Doe, john@example.com, +92-3xx-xxxxxxx`"
            ),
        )
        state.context_data["contact_question_shown"] = True
        state.last_user_message = ""
        return state

    if state.last_user_message:
        parsed = parse_contact_info(state.last_user_message)
        # Write into your actual model: state.order.contact
        if parsed["first_name"] and not state.order.contact.first_name:
            state.order.contact.first_name = parsed["first_name"]
        if parsed["last_name"] and not state.order.contact.last_name:
            state.order.contact.last_name = parsed["last_name"]
        if parsed["email"] and not state.order.contact.email:
            state.order.contact.email = parsed["email"]
        if parsed["phone"] and not state.order.contact.phone:
            state.order.contact.phone = parsed["phone"]

        missing = []
        if not state.order.contact.first_name: missing.append("**First Name**")
        if not state.order.contact.last_name:  missing.append("**Last Name**")
        if not state.order.contact.email:      missing.append("**Email**")
        if not state.order.contact.phone:      missing.append("**Phone**")

        if missing:
            state.add_message(
                role="assistant",
                content=(
                    "Thanks! I still need: " + ", ".join(missing) + ".\n"
                    "Tip: you can send just your name like `John Doe`."
                ),
            )
            state.last_user_message = ""
            return state

        state.context_data["contact_complete"] = True
        state.add_message( 
            role="assistant", 
            content=( 
                f"Got it — **{state.order.contact.first_name} {state.order.contact.last_name}**, "
                f"**{state.order.contact.email}**, **{state.order.contact.phone}**.\n"
                "Next up: your organization details."
            ),
        )
        state.last_user_message = ""
        return state

    state.last_user_message = ""
    return state

async def order_organization_node(state: SessionState) -> SessionState:

    if state.context_data.get("org_question_shown") and not state.last_user_message:
        return state

    interrupt = await _check_interrupt(state)
    if interrupt:
        state.last_user_message = ""
        return state
    
    if not state.context_data.get("org_question_shown"):
        state.add_message(
            role="assistant",
            content=(
                "Is this for a **Business/Organization/Team**? If yes, please share the organization name; "
                "otherwise say **Personal**."
            ),
        )
        state.context_data["org_question_shown"] = True
        state.last_user_message = ""
        return state

    if state.last_user_message:
        txt = state.last_user_message.strip()
        if re.search(r"\b(personal|none|no)\b", txt, re.I):
            state.order.organization.is_business = False
            state.order.organization.name = None
        else:
            state.order.organization.is_business = True
            state.order.organization.name = txt[:120]
        state.context_data["org_complete"] = True
        state.add_message(role="assistant", content="Thanks. Next, what type of order is this?")
        state.last_user_message = ""
        return state

    state.last_user_message = ""
    return state

async def order_type_node(state: SessionState) -> SessionState:
    
    interrupt = await _check_interrupt(state)
    if interrupt:
        state.last_user_message = ""
        return state
    
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

    if state.context_data.get("service_question_shown") and not state.last_user_message:
        return state

    if not state.context_data.get("service_question_shown"):
        state.add_message(
            role="assistant",
            content=(
                "Choose **Service Type**:\n"
                "1) Screen Printing — best for larger, colorful prints\n"
                "2) Embroidery — premium stitched finish for logos\n"
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
                "1) T-Shirt\n2) Hoodie\n3) Cap\n4) Polo\n\nReply with a number."
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

    if state.context_data.get("apparel_question_shown") and not state.last_user_message:
        return state


    if not state.context_data.get("apparel_question_shown"):
        state.add_message(
            role="assistant",
            content=(
                "Which **product** would you like?\n"
                "1) T-Shirt\n2) Hoodie\n3) Cap\n4) Polo\n\n"
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

    if state.context_data.get("product_question_shown") and not state.last_user_message:
        return state


    if not state.context_data.get("product_question_shown"):
        if state.order.product_name and not state.order.color:
            colors = ", ".join(c.title() for c in PRODUCT_CATALOG.get(state.order.product_name, []))
            state.add_message(
                role="assistant",
                content=f"Any preferred **color** for {state.order.product_name.title()}? Options: {colors}\nOr say **No preference**."
            )
            state.context_data["product_question_shown"] = True
            state.last_user_message = ""
            return state
        else:
            state.context_data["product_complete"] = True
            state.last_user_message = ""
            return state

    if state.last_user_message:
        txt = state.last_user_message.strip().lower()
        if state.order.product_name:
            if "no preference" in txt or "any" in txt:
                state.order.color = None
            else:
                for c in PRODUCT_CATALOG.get(state.order.product_name, []):
                    if c.lower() in txt:
                        state.order.color = c.lower()
                        break
        state.context_data["product_complete"] = True
        state.add_message(role="assistant", content="Great. How many units do you need (approx)?")
        state.last_user_message = ""
        return state

    state.last_user_message = ""
    return state


async def order_logo_node(state: SessionState) -> SessionState:


    # Allow mid-flow wants human / end
    interrupt = await _check_interrupt(state)
    if interrupt:
        state.last_user_message = ""
        return state

    if state.context_data.get("logo_attempted") and not state.last_user_message:
        return state


    # --- local helper: open native file dialog (force foreground) ---
    def _pick_file_via_dialog() -> str | None:
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            # Make a tiny invisible window that we can focus/raise
            root.withdraw()
            # Force to foreground / focus
            try:
                root.update_idletasks()
                root.lift()
                root.attributes("-topmost", True)
                root.after(200, lambda: root.attributes("-topmost", False))  # keep topmost briefly
                root.focus_force()
            except Exception:
                pass

            # Important: pass parent=root so dialog is attached to our focused window
            path = filedialog.askopenfilename(
                parent=root,
                title="Select logo/artwork file",
                filetypes=[
                    ("Image / Vector / PDF", "*.png;*.jpg;*.jpeg;*.svg;*.pdf;*.ai;*.eps;*.psd"),
                    ("All files", "*.*"),
                ],
            )
            try:
                root.destroy()
            except Exception:
                pass
            return path if path else None

        except Exception:
            # No Tk in this environment or some GUI error
            return None
    # ----------------------------------------------------------------

    # Auto-open the dialog immediately on first entry
    if not state.context_data.get("logo_attempted"):
        state.context_data["logo_attempted"] = True

        # Small UX ping so the user knows what's happening
        state.add_message(role="assistant", content="📁 Opening file picker…")
        # We don't pause; we continue and try the dialog right away.

        # chosen = _pick_file_via_dialog()
        chosen = await asyncio.to_thread(_pick_file_via_dialog)

        if not chosen:
            # User canceled, dialog behind (OS blocked), or headless → continue
            state.context_data["logo_complete"] = True
            state.add_message(
                role="assistant",
                content="(No logo selected) Continuing without a logo."
            )
            state.last_user_message = ""
            return state

        # Try to upload the selected file
        try:
            parent = os.getenv("GDRIVE_PARENT_FOLDER_ID", "").strip() or None
            make_public = (os.getenv("GDRIVE_MAKE_PUBLIC", "false").lower() in {"1", "true", "yes"})
            file_id, view_link = upload_to_drive(
                chosen,
                filename=None,
                parent_folder_id=parent,
                make_public=make_public,
            )
            state.context_data["logo_file_id"] = file_id
            state.context_data["logo_view_link"] = view_link
            state.context_data["logo_filename"] = os.path.basename(chosen)
            state.context_data["logo_complete"] = True

            link_text = f"\nLink: {view_link}" if view_link else ""
            state.add_message(
                role="assistant",
                content=f"✅ Logo uploaded to Google Drive as **{state.context_data['logo_filename']}**.{link_text}"
            )
        except Exception:
            state.context_data["logo_complete"] = True
            state.add_message(
                role="assistant",
                content=(
                    "⚠️ I couldn't upload the file. Continuing without a logo. "
                    "Please verify Drive credentials (GDRIVE_SERVICE_ACCOUNT_JSON) and folder access."
                ),
            )

        state.last_user_message = ""
        return state

    # Re-entry safety: don't reopen the dialog; just advance
    state.context_data["logo_complete"] = True
    state.last_user_message = ""
    return state

async def order_decoration_location_node(state: SessionState) -> SessionState:
    """Ask where the decoration/print will be placed"""
    
    if state.context_data.get("decoration_location_shown") and not state.last_user_message:
        return state
        # Define decoration locations
    decoration_locations = [
        "Full Back",
        "Full Front", 
        "Left Chest",
        "Right Chest",
        "Left Sleeve",
        "Right Sleeve",
    ]

    if not state.context_data.get("decoration_location_shown"):
        state.add_message(
            role="assistant",
            content=(
                "Where would you like the decoration/print to be placed?\n"
                "Common locations:\n"
                "• Full Back\n"
                "• Full Front\n"
                "• Left Chest\n"
                "• Right Chest\n"
                "• Left Sleeve\n"
                "• Right Sleeve\n\n"
                "You can type the location or say **Skip** if you're not sure yet."
            ),
        )
        state.context_data["decoration_location_shown"] = True
        state.last_user_message = ""
        return state
    
    if state.last_user_message:
        txt = state.last_user_message.strip().lower()
        if txt in {"skip", "not sure", "don't know"}:
            state.order.decoration_location = None
        else:
            # Check if user entered a number (1-6)
            num_match = re.match(r"^\s*([1-6])\s*$", txt)
            if num_match:
                idx = int(num_match.group(1)) - 1
                state.order.decoration_location = decoration_locations[idx]
            else:
                # User typed the location name - try to match it
                matched = False
                for loc in decoration_locations:
                    if loc.lower() in txt or txt in loc.lower():
                        state.order.decoration_location = loc
                        matched = True
                        break
                
                if not matched:
                    state.add_message(
                        role="assistant",
                        content="Please reply with a number 1-6 or type a location name from the list."
                    )
                    state.last_user_message = ""
                    return state
                        
        state.context_data["decoration_location_complete"] = True
        state.add_message(
            role="assistant",
            content="Got it. Next, how many colors will be in your design?"
        )
        state.last_user_message = ""
        return state
    
    state.last_user_message = ""
    return state


async def order_decoration_colors_node(state: SessionState) -> SessionState:
    """Ask how many colors in the decoration/print"""
    
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
        state.add_message(
            role="assistant",
            content="Thanks! Now, how many units do you need?"
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

    if state.context_data.get("qty_question_shown") and not state.last_user_message:
        return state

    if not state.context_data.get("qty_question_shown"):
        state.add_message(role="assistant", content="Please share the **quantity** (e.g., 25).")
        state.context_data["qty_question_shown"] = True
        state.last_user_message = ""
        return state

    if state.last_user_message:
        nums = re.findall(r"\d{1,5}", state.last_user_message)
        if not nums:
            state.add_message(role="assistant", content="Please provide a numeric quantity (e.g., 25).")
            state.last_user_message = ""
            return state
        qty = int(nums[0])
        # Your model stores total_quantity as str — keep a numeric copy in context too
        state.order.total_quantity = str(qty)
        state.context_data["qty_numeric"] = qty
        state.context_data["qty_complete"] = True
        state.add_message(
            role="assistant",
            content=(
                "If you need **size breakdowns**, send like `S:3, M:10, L:12` (formats like `3S 10M 12L` also work).\n"
                "Otherwise say **Skip**."
            ),
        )
        state.last_user_message = ""
        return state

    state.last_user_message = ""
    return state

async def order_sizes_node(state: SessionState) -> SessionState:


    interrupt = await _check_interrupt(state)
    if interrupt:
        state.last_user_message = ""
        return state

    if state.context_data.get("sizes_question_shown") and not state.last_user_message:
        return state


    if not state.context_data.get("sizes_question_shown"):
        state.add_message(
            role="assistant",
            content=(
                "Please provide sizes (optional). Example: `S:3, M:10, L:12` or `3S 10M 12L`.\n"
                "Or say **Skip**."
            ),
        )
        state.context_data["sizes_question_shown"] = True
        state.last_user_message = ""
        return state

    if state.last_user_message:
        txt = state.last_user_message.strip().lower()
        if txt in {"skip", "no", "none"}:
            state.order.sizes = []
            state.context_data["sizes_complete"] = True
            state.add_message(role="assistant", content="Got it. Delivery or Pickup?")
            state.last_user_message = ""
            return state

        parsed = parse_sizes(state.last_user_message)
        if not parsed:
            state.add_message(role="assistant", content="I couldn't parse sizes. Try like `S:3, M:5, L:2` or say **Skip**.")
            state.last_user_message = ""
            return state

        qty_target = int(state.context_data.get("qty_numeric") or 0)
        total_parsed = sum(parsed.values())
        if qty_target and total_parsed != qty_target:
            state.add_message(
                role="assistant",
                content=(
                    f"I read sizes totaling **{total_parsed}**, but your quantity is **{qty_target}**.\n"
                    "Reply **Use sizes total** to adopt this total, or resend sizes matching the quantity."
                ),
            )
            state.context_data["sizes_pending"] = parsed
            state.last_user_message = ""
            return state

        # Convert dict → List[SizeQuantity]
        state.order.sizes = [SizeQuantity(size=k.upper(), quantity=v) for k, v in parsed.items()]
        state.context_data["sizes_complete"] = True
        state.add_message(role="assistant", content="Thanks! Delivery or Pickup?")
        state.last_user_message = ""
        return state

    state.last_user_message = ""
    return state

async def order_delivery_node(state: SessionState) -> SessionState:


    interrupt = await _check_interrupt(state)
    if interrupt:
        state.last_user_message = ""
        return state

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

    interrupt = await _check_interrupt(state)
    if interrupt:
        state.last_user_message = ""
        return state

    if state.context_data.get("summary_shown") and not state.last_user_message:
        return state



    summary_md = _render_summary_text(state)
    state.context_data["summary_text"] = summary_md  # store for email on confirm

    state.add_message(
        role="assistant",
        content=summary_md + (
            "\n\nIf this looks good, reply **Confirm**. To change anything, say for example "
            "`Change color to Black` or `Update quantity to 50`."
        )
    )
    state.context_data["summary_shown"] = True
    state.last_user_message = ""
    return state

# ---------- Router ----------
def route_order_flow(state: SessionState) -> str:
    """
    Router hub that determines next order step.
    Handles resumption from interrupts.
    """
    # CRITICAL: Check if interrupt just happened
    if state.context_data.get("order_interrupted"):
        # Don't route to order steps - let the graph handle the interrupt
        current = state.current_state.value
        
        if current == "HAS_QUESTIONS_ABOUT_PRODUCT":
            return "end"  # Pause, product_questions will be entered on next invocation
        elif current == "WANTS_HUMAN":
            return "wants_human"
        elif current == "END":
            return "end_conversation"
        
    # Special handling: If just resumed from interrupt, re-enter current step
    if state.context_data.get("just_resumed_from_interrupt"):
        state.context_data["just_resumed_from_interrupt"] = False
        # Return to the step that was interrupted
        current = state.current_state.value
        if current == "ORDER_CONTACT":
            # Check which sub-step within contact flow
            if not state.context_data.get("contact_complete"):
                if state.context_data.get("contact_question_shown"):
                    return "end"  # Wait for contact info
                return "order_contact"
        # Map other states similarly
        step_map = {
            "ORDER_ORGANIZATION": "order_organization",
            "ORDER_TYPE": "order_type",
            "ORDER_BUDGET": "order_budget",
            "ORDER_SERVICE": "order_service",
            "ORDER_APPAREL": "order_apparel",
            "ORDER_PRODUCT": "order_product",
            "ORDER_LOGO": "order_logo",
            "ORDER_QUANTITY": "order_quantity",
            "ORDER_SIZES": "order_sizes",
            "ORDER_DELIVERY": "order_delivery",
        }
        if current in step_map:
            return step_map[current]
    
    # If we've shown the summary, only handle "confirm"
    if state.context_data.get("summary_shown"):
        if state.last_user_message:
            txt = state.last_user_message.strip().lower()
            if txt in {"confirm", "confirmed", "yes", "y"} or "confirm" in txt:
                ok = _send_summary_to_customer(state)
                if ok:
                    state.add_message(
                        role="assistant",
                        content="✅ Confirmed! I've emailed your summary to you and will share it with our team."
                    )
                else:
                    state.add_message(
                        role="assistant",
                        content="✅ Confirmed! I couldn't send the email automatically, but your summary is above."
                    )
                state.current_state = ConversationState.END
                return "end_conversation"
        return "end"


    if not state.context_data.get("contact_complete"):
        return "order_contact" if state.context_data.get("contact_question_shown") and state.last_user_message else \
               ("order_contact" if not state.context_data.get("contact_question_shown") else "end")

    if not state.context_data.get("org_complete"):
        return "order_organization" if state.context_data.get("org_question_shown") and state.last_user_message else \
               ("order_organization" if not state.context_data.get("org_question_shown") else "end")

    if not state.context_data.get("type_complete"):
        return "order_type" if state.context_data.get("type_question_shown") and state.last_user_message else \
               ("order_type" if not state.context_data.get("type_question_shown") else "end")

    if not state.context_data.get("budget_complete"):
        return "order_budget" if state.context_data.get("budget_question_shown") and state.last_user_message else \
               ("order_budget" if not state.context_data.get("budget_question_shown") else "end")

    if not state.context_data.get("service_complete"):
        return "order_service" if state.context_data.get("service_question_shown") and state.last_user_message else \
               ("order_service" if not state.context_data.get("service_question_shown") else "end")

    if not state.context_data.get("apparel_complete"):
        return "order_apparel" if state.context_data.get("apparel_question_shown") and state.last_user_message else \
               ("order_apparel" if not state.context_data.get("apparel_question_shown") else "end")

    if not state.context_data.get("product_complete"):
        return "order_product" if state.context_data.get("product_question_shown") and state.last_user_message else \
               ("order_product" if not state.context_data.get("product_question_shown") else "end")

    # LOGO step (optional)
    if not state.context_data.get("logo_complete"):
        return "order_logo" if state.context_data.get("logo_question_shown") and state.last_user_message else \
               ("order_logo" if not state.context_data.get("logo_question_shown") else "end")
# ADD THESE SECTIONS:
    if not state.context_data.get("decoration_location_complete"):
        return "order_decoration_location" if state.context_data.get("decoration_location_shown") and state.last_user_message else \
            ("order_decoration_location" if not state.context_data.get("decoration_location_shown") else "end")

    if not state.context_data.get("decoration_colors_complete"):
        return "order_decoration_colors" if state.context_data.get("decoration_colors_shown") and state.last_user_message else \
            ("order_decoration_colors" if not state.context_data.get("decoration_colors_shown") else "end")

    if not state.context_data.get("qty_complete"):
        return "order_quantity" if state.context_data.get("qty_question_shown") and state.last_user_message else \
               ("order_quantity" if not state.context_data.get("qty_question_shown") else "end")

    if not state.context_data.get("sizes_complete"):
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
        if state.context_data.get("awaiting_address"):
            return "order_delivery_address" if (state.context_data.get("address_question_shown") and state.last_user_message) else \
                   ("order_delivery_address" if not state.context_data.get("address_question_shown") else "end")
        return "order_delivery" if (state.context_data.get("delivery_question_shown") and state.last_user_message) else \
               ("order_delivery" if not state.context_data.get("delivery_question_shown") else "end")

    return "order_summary"
