from typing import Dict
from models.session_state import SessionState

class SessionManager:
    
    def __init__(self):
        self.sessions: Dict[str, SessionState] = {}
    
    def get_session(self, session_id: str) -> SessionState:
        if session_id not in self.sessions:
            self.sessions[session_id] = SessionState(session_id=session_id)
        return self.sessions[session_id]
    
    def update_session(self, session_state: SessionState):
        print(f"Updating session {session_state.session_id} with state {session_state.current_state}")
        self.sessions[session_state.session_id] = session_state
