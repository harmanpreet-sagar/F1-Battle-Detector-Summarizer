"""
Battle detection and scoring algorithm.
"""
from typing import List, Optional, Dict
from datetime import datetime
import logging

from app.models import Battle, BattleFlags, DriverState
from app.config import config

logger = logging.getLogger(__name__)


# Global battle tracker for stability filter
# Maps battle_id -> (Battle object, first_seen_timestamp)
_battle_tracker: Dict[str, tuple[Battle, datetime]] = {}


def reset_battle_tracker():
    """Drop all tracked battles (session change, or test isolation)."""
    _battle_tracker.clear()


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
    # Negative closing_rate = gap is closing (chaser catching up) = exciting!
    closing_score = 0.0
    if closing_rate is not None and closing_rate < 0:
        closing_score = min(1.0, abs(closing_rate) * 10)
    
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


def _gap_sample_time(state: DriverState) -> datetime:
    """
    When the gap on this state was actually measured.

    Gaps come from /intervals, which refreshes slower than the position poll, so
    the interval timestamp - not the poll timestamp - is the real sample time.
    """
    return state.gap_updated_at or state.updated_at


def calculate_closing_rate(history: List[DriverState]) -> Optional[float]:
    """
    Calculate closing rate from gap history.
    Returns seconds per second (negative = gap closing).
    """
    if len(history) < 2:
        return None

    # Use last N points for trend
    window_size = min(config.BATTLE_GAP_TREND_WINDOW, len(history))
    recent = history[-window_size:]

    # Keep one point per distinct gap measurement. The same /intervals row is
    # served across several position polls; counting it repeatedly would read as
    # a stalled gap and flatten the rate toward zero.
    valid_points = []
    for state in recent:
        if state.gap_to_ahead_s is None:
            continue
        sample_time = _gap_sample_time(state)
        if valid_points and valid_points[-1][0] == sample_time:
            continue
        valid_points.append((sample_time, state.gap_to_ahead_s))

    if len(valid_points) < 2:
        return None

    first_time, first_gap = valid_points[0]
    last_time, last_gap = valid_points[-1]

    # Measure the span from the timestamps we already store rather than assuming
    # the poll interval held - retries and slow responses make it drift.
    time_span = (last_time - first_time).total_seconds()

    if time_span <= 0:
        return None

    # Closing rate: change in gap over time
    # Negative = chaser is closing in
    return (last_gap - first_gap) / time_span


def calculate_pace_delta(
    chaser_history: List[DriverState],
    ahead_history: List[DriverState],
    window: Optional[int] = None
) -> Optional[float]:
    """
    Mean lap-time delta over the last N laps, in seconds per lap.

    Negative = the chaser is the faster car. A car sitting 0.3s back *and* half a
    second a lap quicker is the thing a battle actually is; without this term the
    pace weight in the score is dead.
    """
    window = window or config.BATTLE_PACE_TREND_WINDOW

    def recent_lap_times(history: List[DriverState]) -> List[float]:
        # Consecutive polls repeat the same lap time until a new lap completes.
        times = []
        for state in history:
            if state.last_lap_time_s is None:
                continue
            if times and times[-1] == state.last_lap_time_s:
                continue
            times.append(state.last_lap_time_s)
        return times[-window:]

    chaser_times = recent_lap_times(chaser_history)
    ahead_times = recent_lap_times(ahead_history)

    if not chaser_times or not ahead_times:
        return None

    return (sum(chaser_times) / len(chaser_times)) - (sum(ahead_times) / len(ahead_times))


def detect_battles(
    driver_states: List[DriverState],
    driver_histories: Dict[int, List[DriverState]],
    track_status: Optional[str] = None
) -> List[Battle]:
    """
    Detect battles between adjacent drivers.
    
    Args:
        driver_states: Current state of all drivers
        driver_histories: Historical states for calculating trends
        track_status: Current track status (green, yellow, sc, vsc, etc.)
    
    Returns:
        List of Battle objects sorted by score (highest first).
    """
    global _battle_tracker
    
    if not driver_states:
        logger.warning("detect_battles: No driver states provided")
        return []
    
    logger.debug(f"detect_battles: Processing {len(driver_states)} drivers")
    
    # 1. Sort drivers by position
    sorted_drivers = sorted(driver_states, key=lambda d: d.position)
    
    battles = []
    current_time = datetime.now()
    
    # 2. Check adjacent pairs (P2 chasing P1, P3 chasing P2, etc.)
    for i in range(len(sorted_drivers) - 1):
        chaser = sorted_drivers[i + 1]
        ahead = sorted_drivers[i]
        
        # Skip if positions don't match expected pattern
        if ahead.position >= chaser.position:
            logger.debug(f"Skipping pair: positions don't match (ahead P{ahead.position} >= chaser P{chaser.position})")
            continue
        
        # Get gap
        gap = chaser.gap_to_ahead_s
        if gap is None:
            logger.debug(f"Skipping P{chaser.position} vs P{ahead.position}: gap is None")
            continue
        if gap <= 0:
            logger.debug(f"Skipping P{chaser.position} vs P{ahead.position}: gap <= 0 ({gap})")
            continue
        if gap > config.BATTLE_MAX_GAP_S:
            logger.debug(f"Skipping P{chaser.position} vs P{ahead.position}: gap too large ({gap:.2f}s > {config.BATTLE_MAX_GAP_S}s)")
            continue
        
        logger.debug(f"Checking battle: P{chaser.position} ({chaser.full_name}) vs P{ahead.position} ({ahead.full_name}), gap={gap:.2f}s")
        
        # Calculate closing rate from history
        chaser_history = driver_histories.get(chaser.driver_number, [])
        ahead_history = driver_histories.get(ahead.driver_number, [])
        closing_rate = calculate_closing_rate(chaser_history)

        # Pace delta from lap times (negative = chaser is the faster car)
        pace_delta = calculate_pace_delta(chaser_history, ahead_history)
        
        # 3. Determine battle flags
        flags = BattleFlags(
            pit_window_active=detect_pit_window(chaser_history),
            under_yellow=(track_status in ["yellow", "sc", "vsc"]) if track_status else False,
            blue_flag_situation=abs(ahead.position - chaser.position) > 5,  # Lapping situation
            data_quality_warning=(
                chaser.data_confidence != "high" or 
                ahead.data_confidence != "high"
            )
        )
        
        # 4. Calculate battle score
        score = calculate_battle_score(gap, closing_rate, pace_delta, flags)
        
        # Determine intensity
        if score > config.BATTLE_HOT_SCORE and gap < config.BATTLE_HOT_GAP_S and closing_rate and closing_rate < 0:
            intensity = "HOT"
        elif score > config.BATTLE_WATCH_SCORE and gap < config.BATTLE_WATCH_GAP_S:
            intensity = "WATCH"
        else:
            intensity = "NONE"
        
        logger.debug(
            f"  Battle P{chaser.position} vs P{ahead.position}: "
            f"gap={gap:.2f}s, closing_rate={closing_rate}, score={score:.3f}, intensity={intensity}"
        )
        
        # Skip if not interesting
        if intensity == "NONE":
            continue
        
        # Create battle ID
        battle_id = f"{chaser.driver_number}_{ahead.driver_number}"
        
        # Get gap trend from history
        trend_gaps = [
            s.gap_to_ahead_s for s in chaser_history[-config.BATTLE_GAP_TREND_WINDOW:]
            if s.gap_to_ahead_s is not None
        ]
        trend_timestamps = [
            s.updated_at for s in chaser_history[-config.BATTLE_GAP_TREND_WINDOW:]
            if s.gap_to_ahead_s is not None
        ]
        
        # Generate explanation
        explanation_parts = []
        if closing_rate and closing_rate < 0:
            # Realistic rates are hundredths of a second per second; report the
            # gap eaten per minute, which is legible at that scale.
            explanation_parts.append(f"Closing {abs(closing_rate) * 60:.2f}s/min")
        if pace_delta is not None and pace_delta < 0:
            explanation_parts.append(f"{abs(pace_delta):.2f}s/lap quicker")
        if gap < 1.0:
            explanation_parts.append("Within DRS range")
        if flags.pit_window_active:
            explanation_parts.append("Pit window active")
        if not explanation_parts:
            explanation_parts.append(f"Gap: {gap:.2f}s")
        
        explanation = " • ".join(explanation_parts)
        
        # Create battle object
        battle = Battle(
            battle_id=battle_id,
            chaser_driver_number=chaser.driver_number,
            ahead_driver_number=ahead.driver_number,
            chaser_position=chaser.position,
            ahead_position=ahead.position,
            gap_now_s=gap,
            closing_rate_s_per_s=closing_rate,
            pace_delta_s_per_lap=pace_delta,
            battle_score=score,
            intensity=intensity,
            explanation=explanation,
            trend_gap_s=trend_gaps,
            trend_timestamps=trend_timestamps,
            duration_updates=1,  # Will be updated below
            flags=flags
        )
        
        # 5. Apply stability filter
        if battle_id in _battle_tracker:
            # Existing battle - increment duration
            prev_battle, first_seen = _battle_tracker[battle_id]
            battle.duration_updates = prev_battle.duration_updates + 1
            _battle_tracker[battle_id] = (battle, first_seen)
            
            # Only show if it's been around for minimum duration
            if battle.duration_updates >= config.BATTLE_MIN_DURATION_UPDATES:
                battles.append(battle)
        else:
            # New battle - track it but don't show yet
            _battle_tracker[battle_id] = (battle, current_time)
            # Skip adding to battles list until it persists
    
    # Clean up old battles (not seen in last 30 seconds)
    cutoff_time = current_time.timestamp() - 30
    _battle_tracker = {
        bid: (b, t) for bid, (b, t) in _battle_tracker.items()
        if t.timestamp() > cutoff_time
    }
    
    # Sort by score
    battles.sort(key=lambda b: b.battle_score, reverse=True)
    
    logger.debug(f"Detected {len(battles)} battles (tracked {len(_battle_tracker)} total)")
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
