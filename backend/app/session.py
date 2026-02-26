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
                # Parse OpenF1 session data into our SessionStatus model
                self.current_session = SessionStatus(
                    session_key=session_data.get("session_key"),
                    session_name=session_data.get("session_name", "Unknown"),
                    session_type=session_data.get("session_type", "Unknown"),
                    session_status="started",  # OpenF1 doesn't always provide this
                    circuit_short_name=session_data.get("circuit_short_name", "Unknown"),
                    meeting_name=session_data.get("meeting_official_name") or session_data.get("meeting_name", "Unknown"),
                    current_lap=None,  # Will be updated from position data
                    total_laps=None,  # Not always available in session endpoint
                    track_status=None,  # Will be inferred from live data or session status
                    gmt_offset=session_data.get("gmt_offset", "+00:00"),
                    updated_at=datetime.now()
                )
                logger.info(
                    f"Session updated: {self.current_session.meeting_name} - "
                    f"{self.current_session.session_name} (key: {self.current_session.session_key})"
                )
            else:
                logger.warning("No active session found")
                self.current_session = None
        
        except Exception as e:
            logger.error(f"Failed to update session: {e}", exc_info=True)
    
    def get_current_session(self) -> Optional[SessionStatus]:
        """Get the current active session."""
        return self.current_session
    
    def is_session_active(self) -> bool:
        """Check if there is an active session."""
        return self.current_session is not None


# Global session manager instance
session_manager = SessionManager()
