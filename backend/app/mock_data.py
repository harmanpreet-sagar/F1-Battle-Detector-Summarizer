"""
Mock data generator for testing battle detection without live F1 sessions.
"""
from datetime import datetime, timedelta
from typing import List
import math
import random

from app.clock import utcnow
from app.models import DriverState, SessionStatus
from app.config import config


# Real gaps come from /intervals, which refreshes slower than the position poll.
# The mock reproduces that so TEST_MODE exercises the same repeated-sample path
# as live data instead of handing the detector a fresh gap every tick.
TICKS_PER_INTERVAL_REFRESH = 3

# Pace advantage (s/lap, negative = chaser quicker) for the scripted battles.
MOCK_PACE_ADVANTAGE = {2: -0.45, 4: -0.35, 7: 0.10, 10: -0.30}


# Mock driver data
MOCK_DRIVERS = [
    {"number": 1, "name": "Max Verstappen", "team": "Red Bull Racing"},
    {"number": 11, "name": "Sergio Perez", "team": "Red Bull Racing"},
    {"number": 16, "name": "Charles Leclerc", "team": "Ferrari"},
    {"number": 55, "name": "Carlos Sainz", "team": "Ferrari"},
    {"number": 44, "name": "Lewis Hamilton", "team": "Mercedes"},
    {"number": 63, "name": "George Russell", "team": "Mercedes"},
    {"number": 4, "name": "Lando Norris", "team": "McLaren"},
    {"number": 81, "name": "Oscar Piastri", "team": "McLaren"},
    {"number": 14, "name": "Fernando Alonso", "team": "Aston Martin"},
    {"number": 18, "name": "Lance Stroll", "team": "Aston Martin"},
    {"number": 10, "name": "Pierre Gasly", "team": "Alpine"},
    {"number": 31, "name": "Esteban Ocon", "team": "Alpine"},
    {"number": 23, "name": "Alex Albon", "team": "Williams"},
    {"number": 2, "name": "Logan Sargeant", "team": "Williams"},
    {"number": 22, "name": "Yuki Tsunoda", "team": "AlphaTauri"},
    {"number": 3, "name": "Daniel Ricciardo", "team": "AlphaTauri"},
    {"number": 77, "name": "Valtteri Bottas", "team": "Alfa Romeo"},
    {"number": 24, "name": "Zhou Guanyu", "team": "Alfa Romeo"},
    {"number": 20, "name": "Kevin Magnussen", "team": "Haas"},
    {"number": 27, "name": "Nico Hulkenberg", "team": "Haas"},
]


class MockDataGenerator:
    """Generates realistic mock race data."""
    
    def __init__(self):
        self.tick = 0
        self.started_at = utcnow()
        self.base_gaps = self._initialize_gaps()
    
    def _initialize_gaps(self):
        """Create initial gap structure with some close battles."""
        return [
            0.0,      # P1 - Leader
            0.8,      # P2 - HOT battle with P1!
            2.5,      # P3
            0.6,      # P4 - WATCH battle with P3!
            1.2,      # P5 - Potential battle with P4
            3.5,      # P6
            1.5,      # P7 - WATCH battle
            4.2,      # P8
            2.1,      # P9
            1.8,      # P10 - Close battle
            5.5,      # P11
            3.2,      # P12
            2.8,      # P13
            6.1,      # P14
            4.5,      # P15
            3.9,      # P16
            7.2,      # P17
            5.8,      # P18
            4.3,      # P19
            8.1,      # P20
        ]
    
    def generate_driver_states(self) -> List[DriverState]:
        """Generate mock driver states with evolving battles."""
        self.tick += 1
        states = []

        now = utcnow()

        # Gaps advance only when /intervals would have refreshed, and carry the
        # timestamp of that refresh rather than of this poll. Derived from a
        # fixed anchor, not from `now`: a real interval row repeats byte for byte
        # across polls, so drift in the poll loop must not make it look fresh.
        # tick is 1-based, so shift before grouping to get whole groups of three
        interval_tick = (self.tick - 1) // TICKS_PER_INTERVAL_REFRESH
        gap_updated_at = self.started_at + timedelta(
            seconds=interval_tick * TICKS_PER_INTERVAL_REFRESH * config.POLL_POSITIONS_INTERVAL_S
        )

        lap_number = self.current_lap()
        cumulative_gap_to_leader = 0.0

        for position in range(1, 21):
            idx = position - 1
            driver = MOCK_DRIVERS[idx]

            # Get base gap to car ahead
            gap_to_ahead = self.base_gaps[idx]

            # Add some dynamic behavior - use oscillating patterns for realistic battles
            if position == 2:
                # P2 is catching P1 - slow, consistent closing
                gap_to_ahead = 0.35 + 0.08 * math.sin(interval_tick * 0.2) - (interval_tick * 0.005)
                gap_to_ahead = max(0.25, min(0.5, gap_to_ahead))
            elif position == 4:
                # P4 is slowly catching P3 - gentle oscillation
                gap_to_ahead = 0.45 + 0.08 * math.sin(interval_tick * 0.15) - (interval_tick * 0.003)
                gap_to_ahead = max(0.35, min(0.6, gap_to_ahead))
            elif position == 7:
                # P7 maintaining gap to P6 - consistent pressure, no pace advantage
                gap_to_ahead = 1.5 + 0.15 * math.sin(interval_tick * 0.3)
            elif position == 10:
                # P10 and P9 having a close battle - gentle oscillation staying close
                gap_to_ahead = 0.95 + 0.15 * math.sin(interval_tick * 0.3) - (interval_tick * 0.004)
                gap_to_ahead = max(0.75, min(1.15, gap_to_ahead))
            else:
                # Others have stable gaps with small variations
                gap_to_ahead += random.uniform(-0.05, 0.05)

            # Update cumulative gap to leader
            cumulative_gap_to_leader += gap_to_ahead

            state = DriverState(
                driver_number=driver["number"],
                full_name=driver["name"],
                team_name=driver["team"],
                position=position,
                last_lap_time_s=self._lap_time(position, lap_number),
                gap_to_leader_s=cumulative_gap_to_leader if position > 1 else 0.0,
                gap_to_ahead_s=gap_to_ahead if position > 1 else None,
                tire_compound="SOFT" if position <= 10 else "MEDIUM",
                tire_age_laps=random.randint(5, 25),
                pit_stops_count=random.randint(0, 2),
                updated_at=now,
                gap_updated_at=gap_updated_at if position > 1 else None,
                data_confidence="high"
            )

            states.append(state)

        return states

    def _lap_time(self, position: int, lap_number: int) -> float:
        """
        Lap time for a driver, held constant for the duration of a lap.

        Cars further back are nominally slower, except where MOCK_PACE_ADVANTAGE
        scripts a chaser to be quicker than the car it is following.
        """
        base = 90.0 + (position - 1) * 0.15
        advantage = MOCK_PACE_ADVANTAGE.get(position)
        if advantage is not None:
            # Relative to the car ahead, which is one grid slot quicker nominally
            base = 90.0 + (position - 2) * 0.15 + advantage

        # Deterministic per-lap variation so pace deltas stay stable within a lap
        return round(base + 0.05 * math.sin(lap_number * 0.7), 3)

    def current_lap(self) -> int:
        """Lap number implied by the current tick."""
        return self.tick // 10 + 1
    
    def generate_mock_session(self) -> SessionStatus:
        """Generate a mock session."""
        return SessionStatus(
            session_key=99999,
            session_name="Race",
            session_type="Race",
            session_status="started",
            circuit_short_name="Mock Circuit",
            meeting_name="Mock Grand Prix 2026",
            current_lap=self.current_lap(),
            total_laps=50,
            track_status="green",
            gmt_offset="+00:00",
            updated_at=utcnow()
        )
    
    def reset(self):
        """Reset the mock data generator."""
        self.tick = 0
        self.started_at = utcnow()
        self.base_gaps = self._initialize_gaps()


# Global instance
mock_data_generator = MockDataGenerator()
