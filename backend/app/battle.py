"""
Battle detection and scoring algorithm.
"""
from typing import List, Optional
import logging

from app.models import Battle, BattleFlags, DriverState
from app.config import config

logger = logging.getLogger(__name__)


def calculate_battle_score(
    gap_s: float,
    closing_rate: Optional[float],
    pace_delta: Optional[float],
    flags: BattleFlags
) -> float:
    """
    Calculate battle score based on gap, closing rate, and pace.
    
    Returns a score between 0 and 1, where higher is more intense.
    """
    # Base score from gap (0-1, smaller gap = higher score)
    gap_score = max(0, 1 - (gap_s / config.BATTLE_MAX_GAP_S))
    
    # Closing rate contribution (0-1)
    closing_score = 0.0
    if closing_rate is not None:
        closing_score = min(1.0, max(0, closing_rate * 10))
    
    # Pace advantage contribution (0-1)
    pace_score = 0.0
    if pace_delta is not None and pace_delta < 0:
        # Negative = chaser is faster
        pace_score = min(1.0, abs(pace_delta) * 2)
    
    # Weighted combination
    raw_score = (gap_score * 0.5) + (closing_score * 0.3) + (pace_score * 0.2)
    
    # Apply penalties for special conditions
    if flags.under_yellow:
        raw_score *= 0.5
    if flags.pit_window_active:
        raw_score *= 0.3
    if flags.data_quality_warning:
        raw_score *= 0.7
    
    return raw_score


def detect_battles(driver_states: List[DriverState]) -> List[Battle]:
    """
    Detect battles between adjacent drivers.
    
    Returns a list of Battle objects sorted by score (highest first).
    """
    battles = []
    
    # TODO: Implement battle detection logic
    # 1. Sort drivers by position
    # 2. Check adjacent pairs (P2 vs P1, P3 vs P2, etc.)
    # 3. Calculate scores
    # 4. Filter by minimum intensity
    # 5. Apply stability filter
    
    logger.info(f"Detected {len(battles)} battles")
    return battles


def detect_pit_window(driver_history: List[DriverState]) -> bool:
    """Detect if a driver likely just pitted based on gap changes."""
    if len(driver_history) < 2:
        return False
    
    # Check for sudden gap change (> 10s difference)
    if driver_history[-1].gap_to_ahead_s and driver_history[-2].gap_to_ahead_s:
        gap_change = abs(
            driver_history[-1].gap_to_ahead_s - driver_history[-2].gap_to_ahead_s
        )
        return gap_change > 10.0
    
    return False
