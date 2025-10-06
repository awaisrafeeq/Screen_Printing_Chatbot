
from typing import Dict
from models.session_state import SessionState

class SessionManager:
    """Simple in-memory session storage"""
    
    def __init__(self):
        self.sessions: Dict[str, SessionState] = {}
    
    def get_session(self, session_id: str) -> SessionState:
        """Get or create a session"""
        if session_id not in self.sessions:
            self.sessions[session_id] = SessionState(session_id=session_id)
        return self.sessions[session_id]
    
    def update_session(self, session_state: SessionState):
        """Update session in storage"""
        self.sessions[session_state.session_id] = session_state