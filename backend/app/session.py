"""
Session lifecycle management - tracks current session and status.
"""
from typing import Optional
from datetime import datetime
import logging

from app.models import SessionStatus
from app.openf1_client import openf1_client

logger = logging.getLogger(__name__)


class SessionManager:
    """Manages F1 session lifecycle."""
    
    def __init__(self):
        self.current_session: Optional[SessionStatus] = None
    
    async def update_current_session(self):
        """Poll OpenF1 for the latest session."""
        try:
            session_data = await openf1_client.get_latest_session()
            
            if session_data:
                # TODO: Parse session data into SessionStatus model
                logger.info(f"Found session: {session_data.get('session_name')}")
                self.current_session = None  # Placeholder
            else:
                logger.warning("No active session found")
                self.current_session = None
        
        except Exception as e:
            logger.error(f"Failed to update session: {e}")
    
    def get_current_session(self) -> Optional[SessionStatus]:
        """Get the current active session."""
        return self.current_session
    
    def is_session_active(self) -> bool:
        """Check if there is an active session."""
        return self.current_session is not None


# Global session manager instance
session_manager = SessionManager()
