"""
In-memory state manager for driver states and rolling history.
"""
from typing import Dict, List, Optional
from collections import deque
from datetime import datetime
import logging

from app.models import DriverState
from app.config import config

logger = logging.getLogger(__name__)


class StateManager:
    """Manages in-memory state for all drivers."""
    
    def __init__(self):
        self.current_state: Dict[int, DriverState] = {}
        self.history: Dict[int, deque] = {}
        self.max_history_length = config.BATTLE_GAP_TREND_WINDOW
    
    def update_driver_state(self, driver_state: DriverState):
        """Update state for a single driver."""
        driver_number = driver_state.driver_number
        
        # Update current state
        self.current_state[driver_number] = driver_state
        
        # Add to history
        if driver_number not in self.history:
            self.history[driver_number] = deque(maxlen=self.max_history_length)
        self.history[driver_number].append(driver_state)
        
        logger.debug(f"Updated state for driver {driver_number}")
    
    def get_current_state(self, driver_number: int) -> Optional[DriverState]:
        """Get current state for a driver."""
        return self.current_state.get(driver_number)
    
    def get_all_current_states(self) -> List[DriverState]:
        """Get current states for all drivers."""
        return list(self.current_state.values())
    
    def get_history(self, driver_number: int) -> List[DriverState]:
        """Get history for a driver."""
        return list(self.history.get(driver_number, []))
    
    def detect_pit_windows(self):
        """Detect sudden gap changes indicating pit stops."""
        # TODO: Implement pit stop detection logic
        pass
    
    def clear(self):
        """Clear all state (e.g., between sessions)."""
        self.current_state.clear()
        self.history.clear()
        logger.info("State cleared")


# Global state manager instance
state_manager = StateManager()
