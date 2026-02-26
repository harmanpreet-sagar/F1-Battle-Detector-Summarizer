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
    
    def update_from_openf1_positions(self, positions: List[Dict], drivers_info: Dict[int, Dict]):
        """
        Update driver states from OpenF1 position data.
        
        Args:
            positions: List of position data from OpenF1
            drivers_info: Dict mapping driver_number to driver info (name, team, etc.)
        """
        for pos_data in positions:
            driver_num = pos_data.get("driver_number")
            if not driver_num:
                continue
            
            driver_info = drivers_info.get(driver_num, {})
            
            # Calculate data confidence based on recency
            date_str = pos_data.get("date")
            updated_at = datetime.fromisoformat(date_str.replace('Z', '+00:00')) if date_str else datetime.now()
            age_seconds = (datetime.now(updated_at.tzinfo) - updated_at).total_seconds()
            
            if age_seconds < config.DATA_CONFIDENCE_MEDIUM_S:
                confidence = "high"
            elif age_seconds < config.DATA_STALE_THRESHOLD_S:
                confidence = "medium"
            else:
                confidence = "low"
            
            driver_state = DriverState(
                driver_number=driver_num,
                full_name=driver_info.get("full_name") or driver_info.get("name_acronym", f"Driver {driver_num}"),
                team_name=driver_info.get("team_name", "Unknown"),
                position=pos_data.get("position", 20),
                last_lap_time_s=None,  # Will be filled from lap data
                gap_to_leader_s=pos_data.get("gap_to_leader"),
                gap_to_ahead_s=pos_data.get("interval"),
                tire_compound=None,  # Not in position data
                tire_age_laps=None,
                pit_stops_count=0,  # Will be calculated
                updated_at=updated_at,
                data_confidence=confidence
            )
            
            self.update_driver_state(driver_state)
    
    def detect_pit_windows(self):
        """
        Detect sudden gap changes indicating pit stops.
        Sets a flag on driver states that might be in pit window.
        """
        for driver_num, history in self.history.items():
            if len(history) < 2:
                continue
            
            # Check last two states for sudden gap change
            prev_state = history[-2]
            curr_state = history[-1]
            
            # Skip if either gap is None
            if prev_state.gap_to_ahead_s is None or curr_state.gap_to_ahead_s is None:
                continue
            
            gap_change = abs(curr_state.gap_to_ahead_s - prev_state.gap_to_ahead_s)
            
            # If gap changed by > 10s, likely a pit stop
            if gap_change > 10.0:
                logger.info(f"Pit window detected for driver {driver_num}: gap changed by {gap_change:.1f}s")
                # Note: We'll use this information in battle detection
    
    def clear(self):
        """Clear all state (e.g., between sessions)."""
        self.current_state.clear()
        self.history.clear()
        logger.info("State cleared")


# Global state manager instance
state_manager = StateManager()
