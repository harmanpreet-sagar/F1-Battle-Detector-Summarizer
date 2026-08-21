"""
In-memory state manager for driver states and rolling history.
"""
from typing import Dict, List, Optional, Any
from collections import deque
from datetime import datetime
import logging

from app.models import DriverState
from app.config import config

logger = logging.getLogger(__name__)


def parse_openf1_timestamp(date_str: Optional[str]) -> Optional[datetime]:
    """Parse an OpenF1 ISO timestamp. Returns None if absent or malformed."""
    if not date_str:
        return None
    try:
        return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
    except ValueError:
        logger.warning(f"Unparseable OpenF1 timestamp: {date_str!r}")
        return None


def parse_gap(value: Any) -> Optional[float]:
    """
    Coerce an OpenF1 gap field to seconds.

    /intervals reports lapped cars as strings like "+1 LAP", and the leader's
    own interval is null. Neither is a gap in seconds, so both become None.
    """
    if isinstance(value, (int, float)):
        return float(value)
    return None


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
    
    def get_driver_info(self, driver_number: int) -> Optional[Dict]:
        """Get basic driver info (name, team)."""
        state = self.get_current_state(driver_number)
        if state:
            return {
                "driver_number": state.driver_number,
                "full_name": state.full_name,
                "team_name": state.team_name
            }
        return None
    
    def update_from_openf1_positions(
        self,
        positions: List[Dict],
        drivers_info: Dict[int, Dict],
        intervals: Optional[List[Dict]] = None,
        laps: Optional[Dict[int, List[Dict]]] = None,
    ):
        """
        Update driver states from OpenF1 position data.

        Args:
            positions: List of position data from OpenF1 (/position)
            drivers_info: Dict mapping driver_number to driver info (name, team, etc.)
            intervals: Latest /intervals row per driver. /position carries no gap
                fields, so without this every gap is None and no battle is detectable.
            laps: Recent completed laps per driver, oldest first (/laps).
        """
        intervals_by_driver = {
            row["driver_number"]: row
            for row in (intervals or [])
            if row.get("driver_number") is not None
        }
        laps = laps or {}

        for pos_data in positions:
            driver_num = pos_data.get("driver_number")
            if not driver_num:
                continue

            driver_info = drivers_info.get(driver_num, {})

            # Calculate data confidence based on recency
            updated_at = parse_openf1_timestamp(pos_data.get("date")) or datetime.now()
            age_seconds = (datetime.now(updated_at.tzinfo) - updated_at).total_seconds()

            if age_seconds < config.DATA_CONFIDENCE_MEDIUM_S:
                confidence = "high"
            elif age_seconds < config.DATA_STALE_THRESHOLD_S:
                confidence = "medium"
            else:
                confidence = "low"

            gap_to_ahead, gap_to_leader, gap_updated_at = self._resolve_gaps(
                driver_num, intervals_by_driver.get(driver_num)
            )

            # A gap that has not refreshed in several interval cycles is no longer
            # trustworthy for closing-rate work, even if the position row is fresh.
            if gap_updated_at is not None:
                gap_age = (datetime.now(gap_updated_at.tzinfo) - gap_updated_at).total_seconds()
                if gap_age > config.INTERVAL_STALE_THRESHOLD_S:
                    confidence = "low"

            driver_laps = laps.get(driver_num) or []
            last_lap_time_s = driver_laps[-1].get("lap_duration") if driver_laps else None

            driver_state = DriverState(
                driver_number=driver_num,
                full_name=driver_info.get("full_name") or driver_info.get("name_acronym", f"Driver {driver_num}"),
                team_name=driver_info.get("team_name", "Unknown"),
                position=pos_data.get("position", 20),
                last_lap_time_s=last_lap_time_s,
                gap_to_leader_s=gap_to_leader,
                gap_to_ahead_s=gap_to_ahead,
                tire_compound=None,  # Not in position data
                tire_age_laps=None,
                pit_stops_count=0,  # Will be calculated
                updated_at=updated_at,
                gap_updated_at=gap_updated_at,
                data_confidence=confidence
            )

            self.update_driver_state(driver_state)

    def _resolve_gaps(self, driver_num: int, interval_row: Optional[Dict]):
        """
        Resolve (gap_to_ahead, gap_to_leader, gap_updated_at) for one driver.

        /intervals lags /position, so a poll may bring no row for a driver at all.
        Carrying the previous gap forward keeps the history continuous; carrying
        its original timestamp forward is what stops a repeated sample from being
        mistaken for a fresh one by the closing-rate calculation.
        """
        if interval_row is not None:
            return (
                parse_gap(interval_row.get("interval")),
                parse_gap(interval_row.get("gap_to_leader")),
                parse_openf1_timestamp(interval_row.get("date")),
            )

        previous = self.current_state.get(driver_num)
        if previous is None:
            return None, None, None

        return previous.gap_to_ahead_s, previous.gap_to_leader_s, previous.gap_updated_at
    
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
