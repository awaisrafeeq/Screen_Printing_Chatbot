
from pydantic import BaseModel, Field
from enum import Enum
from typing import Dict, Any, Optional, List
from datetime import datetime

class ConversationState(str, Enum):
    """All possible conversation states"""
    WELCOME = "WELCOME"
    MAIN_MENU = "MAIN_MENU"
    WANTS_HUMAN = "WANTS_HUMAN"
    HAS_QUESTIONS_ABOUT_PRODUCT = "HAS_QUESTIONS_ABOUT_PRODUCT"
    ORDER_CONTACT = "ORDER_CONTACT"
    ORDER_ORGANIZATION = "ORDER_ORGANIZATION"
    ORDER_TYPE = "ORDER_TYPE"
    ORDER_BUDGET = "ORDER_BUDGET"
    ORDER_SERVICE = "ORDER_SERVICE"
    ORDER_APPAREL = "ORDER_APPAREL"
    ORDER_PRODUCT = "ORDER_PRODUCT"
    ORDER_LOGO = "ORDER_LOGO"
    ORDER_DECORATION_LOCATION = "ORDER_DECORATION_LOCATION"
    ORDER_DECORATION_COLORS = "ORDER_DECORATION_COLORS"
    ORDER_QUANTITY = "ORDER_QUANTITY"
    ORDER_SIZES = "ORDER_SIZES"
    ORDER_DELIVERY = "ORDER_DELIVERY"
    ORDER_SUMMARY = "ORDER_SUMMARY"
    END = "END"

class Intent(str, Enum):
    """All possible user intents"""
    GREETING = "Greeting"
    HAS_QUESTIONS_ABOUT_PRODUCT = "Has Questions about Product"  
    PLACE_ORDER = "Place order"
    END_CONVERSATION = "End conversation"
    WANTS_HUMAN = "Wants Human"
    NO_MATCH = "No match"
    YES = "Yes"
    NO = "No"

class Contact(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None

class Organization(BaseModel):
    is_business: Optional[bool] = None
    name: Optional[str] = None

class SizeQuantity(BaseModel):
    size: str
    quantity: int

class OrderDetails(BaseModel):
    contact: Contact = Field(default_factory=Contact)
    organization: Organization = Field(default_factory=Organization)
    order_type: Optional[str] = None
    budget_range: Optional[str] = None  # "Premium" or "Value"
    service_type: Optional[str] = None  # "Screen Printing" or "Embroidery"
    apparel_category: Optional[str] = None  # "Hats", "Hoodies", etc.
    product_name: Optional[str] = None
    color: Optional[str] = None    
    decoration_location: Optional[str] = None
    decoration_colors: Optional[int] = None
    total_quantity: Optional[str] = None  # "0-10", "11-20", etc.
    sizes: List[SizeQuantity] = Field(default_factory=list)
    delivery_option: Optional[str] = None  # "Pick Up" or "Delivery"
    delivery_address: Optional[str] = None

class SessionState(BaseModel):
    """Complete session state for the chatbot"""
    session_id: str
    current_state: ConversationState = ConversationState.WELCOME
    last_user_message: Optional[str] = None
    classified_intent: Optional[Intent] = None
    conversation_history: List[Dict[str, Any]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)    
    # Order data
    order: OrderDetails = Field(default_factory=OrderDetails)
    # Context preservation for flow interruptions
    interrupted_from: Optional[ConversationState] = None
    context_data: Dict[str, Any] = Field(default_factory=dict)
    
    def add_message(self, role: str, content: str, metadata: Optional[Dict] = None):
        """Add a message to conversation history"""
        message = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "metadata": metadata or {}
        }
        self.conversation_history.append(message)