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
        # Last session actually seen. Survives current_session going None so a
        # dropped poll does not read as a new session on the way back.
        self._last_session_key: Optional[int] = None
    
    def set_session(self, session: Optional[SessionStatus]) -> bool:
        """
        Replace the current session, reporting whether this is a *new* session.

        True only on a transition to a different, non-None session_key. Losing
        the session - a blip, or the race ending - deliberately does not count:
        callers reset accumulated state on a True, and a one-poll gap in the
        OpenF1 response must not wipe a live session's history.
        """
        previous_key = self._last_session_key
        new_key = session.session_key if session else None

        self.current_session = session
        if new_key is not None:
            self._last_session_key = new_key

        return new_key is not None and new_key != previous_key

    async def update_current_session(self) -> bool:
        """Poll OpenF1 for the latest session. True if the session changed."""
        try:
            session_data = await openf1_client.get_latest_session()
            
            if session_data:
                # Parse OpenF1 session data into our SessionStatus model
                session = SessionStatus(
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
                changed = self.set_session(session)
                logger.info(
                    f"Session updated: {self.current_session.meeting_name} - "
                    f"{self.current_session.session_name} (key: {self.current_session.session_key})"
                )
                return changed
            else:
                logger.warning("No active session found")
                self.set_session(None)
        
        except Exception as e:
            logger.error(f"Failed to update session: {e}", exc_info=True)

        return False
    
    def get_current_session(self) -> Optional[SessionStatus]:
        """Get the current active session."""
        return self.current_session
    
    def is_session_active(self) -> bool:
        """Check if there is an active session."""
        return self.current_session is not None


# Global session manager instance
session_manager = SessionManager()
