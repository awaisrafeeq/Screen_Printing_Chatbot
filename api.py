# api.py - FastAPI endpoints for Screen Printing NW Chatbot
import os
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
import uvicorn
import uuid
# Import your existing chatbot
from main import ScreenPrintingChatbot
from models.session_state import ConversationState 

# Initialize FastAPI app
app = FastAPI(
    title="Screen Printing NW Chatbot API",
    description="Conversational AI for quote requests and product questions",
    version="1.0.0"
)

# Add CORS middleware to allow frontend requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace with specific domains
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize chatbot instance
chatbot = ScreenPrintingChatbot()

# ============================================
# REQUEST/RESPONSE MODELS
# ============================================

class ChatRequest(BaseModel):
    session_id: str = Field(..., description="Unique session identifier for the user")
    message: str = Field(..., description="User's message text")
    
    class Config:
        json_schema_extra = {
            "example": {
                "session_id": "user_12345",
                "message": "I want to order 50 t-shirts"
            }
        }

class ChatResponse(BaseModel):
    success: bool = Field(..., description="Whether the request was successful")
    response: str = Field(..., description="Bot's response message")
    session_id: str = Field(..., description="Session identifier")
    current_state: str = Field(..., description="Current conversation state")
    classified_intent: Optional[str] = Field(None, description="Classified user intent")
    conversation_ended: bool = Field(..., description="Whether conversation has ended")
    error: Optional[str] = Field(None, description="Error message if success=false")
    context_data: Optional[Dict[str, Any]] = Field(None, description="Additional context data, e.g., upload flags")  # ADDED
    
    class Config:
        json_schema_extra = {
            "example": {
                "success": True,
                "response": "Great! Let's start with your contact details...",
                "session_id": "user_12345",
                "current_state": "ORDER_CONTACT",
                "classified_intent": "Place order",
                "conversation_ended": False,
                "error": None,
                "context_data": {  # ADDED
                    "awaiting_upload": False,
                    "upload_key": None
                }
            }
        }

class NewSessionResponse(BaseModel):
    success: bool
    session_id: str
    message: str

class SessionStateResponse(BaseModel):
    success: bool
    session_id: str
    current_state: str
    order_data: Dict[str, Any]
    conversation_history: list

class UploadResponse(BaseModel):
    success: bool
    select_message: str = Field(..., description="Message prompting file selection")
    upload_key: str = Field(..., description="Unique key for the upload request")
    file_details: Dict[str, Optional[str]] = Field(..., description="Details of the uploaded file")
    confirmation_message: str = Field(..., description="Confirmation message after upload")
    error: Optional[str] = Field(None, description="Error message if success=false")

    class Config:
        json_schema_extra = {
            "example": {
                "success": True,
                "select_message": "Select logo/artwork file",
                "upload_key": "4c53a306b5764d60b8f018d4a25e4c3e",
                "file_details": {
                    "path": "/tmp/tmpfile123.jpeg",
                    "file_id": "1zE2t-SBw0YxH8jmIXOuBMXc_Lr-58LHZ",
                    "view_link": "https://drive.google.com/file/d/1zE2t-SBw0YxH8jmIXOuBMXc_Lr-58LHZ/view?usp=drivesdk",
                    "filename": "logo.jpeg"
                },
                "confirmation_message": "logo uploaded",
                "error": ""
            }
        }

# ============================================
# ENDPOINTS
# ============================================

@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "status": "online",
        "service": "Screen Printing NW Chatbot API",
        "version": "1.0.0"
    }

@app.get("/health")
async def health_check():
    """Detailed health check"""
    return {
        "status": "healthy",
        "chatbot_initialized": chatbot is not None,
        "openai_key_configured": bool(os.getenv("OPENAI_API_KEY"))
    }

@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    Main chat endpoint - processes user messages and returns bot responses
    """
    try:
        # Validate session_id
        if not request.session_id or len(request.session_id) < 3:
            raise HTTPException(
                status_code=400,
                detail="session_id must be at least 3 characters"
            )

        # Process message with chatbot (uses main.py logic)
        result = await chatbot.chat(
            session_id=request.session_id,
            user_message=request.message
        )

        # Get session state to include context_data
        from services.session_manager import SessionManager
        session_manager = SessionManager()
        state = session_manager.get_session(request.session_id)

        # Ensure response matches main.py output format
        return ChatResponse(
            success=result["success"],
            response=result["response"],
            session_id=result["session_id"],
            current_state=result["current_state"],
            classified_intent=result.get("classified_intent"),
            conversation_ended=result.get("conversation_ended", False),
            error=result.get("error"),
            context_data=state.context_data if state else {}  # ADDED
        )

    except Exception as e:
        return ChatResponse(
            success=False,
            response="I'm experiencing technical difficulties. Please try again.",
            session_id=request.session_id,
            current_state="ERROR",
            classified_intent=None,
            conversation_ended=False,
            error=str(e),
            context_data={}  # ADDED
        )

@app.post("/api/session/new", response_model=NewSessionResponse)
async def create_new_session():
    """
    Create a new chat session with a welcome message
    """
    try:
        import uuid
        session_id = f"session_{uuid.uuid4().hex[:12]}"

        # Initialize session with welcome message
        result = await chatbot.chat(session_id=session_id, user_message="")

        return NewSessionResponse(
            success=True,
            session_id=session_id,
            message=result["response"]
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/session/{session_id}", response_model=SessionStateResponse)
async def get_session_state(session_id: str):
    """
    Get current state of a session
    """
    try:
        from services.session_manager import SessionManager
        session_manager = SessionManager()
        state = session_manager.get_session(session_id)

        return SessionStateResponse(
            success=True,
            session_id=session_id,
            current_state=state.current_state.value,
            order_data={
                "contact": {
                    "first_name": state.order.contact.first_name,
                    "last_name": state.order.contact.last_name,
                    "email": state.order.contact.email,
                    "phone": state.order.contact.phone,
                },
                "organization": {
                    "is_business": state.order.organization.is_business,
                    "name": state.order.organization.name,
                },
                "order_type": state.order.order_type,
                "budget_range": state.order.budget_range,
                "service_type": state.order.service_type,
                "product_name": state.order.product_name,
                "color": state.order.color,
                "decoration_location": state.order.decoration_location,
                "decoration_colors": state.order.decoration_colors,
                "total_quantity": state.order.total_quantity,
                "sizes": [{"size": s.size, "quantity": s.quantity} for s in state.order.sizes],
                "delivery_option": state.order.delivery_option,
                "delivery_address": state.order.delivery_address,
                "context_data": state.context_data  # ADDED for consistency, though not strictly needed
            },
            conversation_history=state.conversation_history[-10:]
        )
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Session not found: {str(e)}")

@app.delete("/api/session/{session_id}")
async def delete_session(session_id: str):
    """
    Delete/end a session
    """
    try:
        from services.session_manager import SessionManager
        session_manager = SessionManager()
        if session_id in session_manager.sessions:
            del session_manager.sessions[session_id]
            return {"success": True, "message": f"Session {session_id} deleted"}
        else:
            raise HTTPException(status_code=404, detail="Session not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/upload", response_model=UploadResponse)
async def upload_file(session_id: str, file: UploadFile = File(...)):
    """
    Upload logo/artwork file for a session
    """
    try:
        from flows.oauth_uploader import upload_to_drive
        from services.session_manager import SessionManager
        import tempfile

        allowed_extensions = {'.png', '.jpg', '.jpeg', '.svg', '.pdf', '.ai', '.eps', '.psd'}
        file_ext = os.path.splitext(file.filename)[1].lower()

        # Initialize upload_key and select_message
        upload_key = uuid.uuid4().hex
        select_message = "Select logo/artwork file"

        if file_ext not in allowed_extensions:
            return UploadResponse(
                success=False,
                select_message=select_message,
                upload_key=upload_key,
                file_details={"path": "", "file_id": "", "view_link": "", "filename": file.filename},
                confirmation_message="",
                error=f"File type {file_ext} not allowed. Allowed: {', '.join(allowed_extensions)}"
            )

        # Initialize session manager and get state
        session_manager = SessionManager()
        state = session_manager.get_session(session_id)

        # Update session with upload_key
        state.context_data["upload_key"] = upload_key
        state.add_message(role="assistant", content=f"{select_message} (key: {upload_key})")
        session_manager.update_session(state)

        # Save file temporarily
        with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext) as tmp_file:
            content = await file.read()
            tmp_file.write(content)
            tmp_path = tmp_file.name

        try:
            parent = os.getenv("GDRIVE_PARENT_FOLDER_ID", "").strip() or None
            make_public = (os.getenv("GDRIVE_MAKE_PUBLIC", "false").lower() in {"1", "true", "yes"})

            file_id, view_link = upload_to_drive(
                tmp_path,
                filename=file.filename,
                parent_folder_id=parent,
                make_public=make_public,
            )

            # Update session with upload details and confirmation message
            state.context_data["logo_file_id"] = file_id
            state.context_data["logo_view_link"] = view_link
            state.context_data["logo_filename"] = file.filename
            state.context_data["logo_complete"] = True
            state.current_state = ConversationState.ORDER_LOGO
            state.add_message(role="assistant", content="logo uploaded")
            session_manager.update_session(state)

            return UploadResponse(
                success=True,
                select_message=select_message,
                upload_key=upload_key,
                file_details={
                    "path": tmp_path,
                    "file_id": file_id,
                    "view_link": view_link,
                    "filename": file.filename
                },
                confirmation_message="logo uploaded",
                error=None
            )
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
    except Exception as e:
        # Update session with error message
        state.add_message(
            role="assistant",
            content=f"⚠️ I couldn't upload the file. Continuing without a logo. Error: {str(e)}"
        )
        session_manager.update_session(state)
        return UploadResponse(
            success=False,
            select_message=select_message,
            upload_key=upload_key,
            file_details={"path": "", "file_id": "", "view_link": "", "filename": file.filename},
            confirmation_message="",
            error=f"Upload failed: {str(e)}"
        )

# ============================================
# RUN SERVER
# ============================================

if __name__ == "__main__":
    if not os.getenv("OPENAI_API_KEY"):
        print("⚠️  Warning: OPENAI_API_KEY not set")

    uvicorn.run(
        "api:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )
