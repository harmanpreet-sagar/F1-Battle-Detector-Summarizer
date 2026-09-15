"""
Mock data generator for testing battle detection without live F1 sessions.
"""
from datetime import datetime, timedelta
from typing import List, Optional
import math
import random

from app.clock import ensure_utc, utcnow
from app.models import DriverState, SessionStatus
from app.config import config


# Real gaps come from /intervals, which refreshes slower than the position poll.
# The mock reproduces that so TEST_MODE exercises the same repeated-sample path
# as live data instead of handing the detector a fresh gap every tick.
TICKS_PER_INTERVAL_REFRESH = 3

# The scripted race runs for a fixed number of ticks and then starts over, so a
# demo left open indefinitely keeps showing a race rather than a frozen grid.
# The gaps below used to drift linearly, which walked each of them into its
# clamp within a few minutes: gaps stopped moving, closing rate went to zero and
# nothing could reach HOT again. They are periodic now, and this is the period.
#
# Divisible by TICKS_PER_INTERVAL_REFRESH so the wrap lands on an interval
# boundary rather than mid-refresh.
TICKS_PER_LAP = 10
TOTAL_LAPS = 24
LOOP_TICKS = TICKS_PER_LAP * TOTAL_LAPS

assert LOOP_TICKS % TICKS_PER_INTERVAL_REFRESH == 0

# Gaps are written as functions of this, the fraction of the loop elapsed, so
# that the value at the end of the loop equals the value at the start and the
# restart is not a visible jump. Counted in interval refreshes rather than ticks
# because that is the cadence gaps actually move at.
INTERVAL_TICKS_PER_LOOP = LOOP_TICKS // TICKS_PER_INTERVAL_REFRESH

# Pace advantage (s/lap, negative = chaser quicker) for the scripted battles.
# P2 is the marquee one and is the only car quick enough to reach HOT: the
# scorer caps the pace term at |delta| >= 0.5s/lap, and with gap and closing
# contributing at most 0.5 and ~0.03 at realistic rates, nothing under that cap
# clears the 0.70 HOT threshold.
MOCK_PACE_ADVANTAGE = {2: -0.60, 4: -0.35, 7: 0.10, 10: -0.40}


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
    
    def generate_driver_states(self, *, now: Optional[datetime] = None) -> List[DriverState]:
        """
        Generate mock driver states with evolving battles.

        `now` is the instant this poll is claimed to have arrived. It defaults to
        the wall clock, which is what the live poll loop wants. The serverless
        path in app/demo.py passes the tick's own time instead, because it
        replays a window of past ticks in one go and each needs its real
        timestamp for closing rate and freshness to come out right.
        """
        self.tick += 1
        states = []

        now = ensure_utc(now) if now is not None else utcnow()

        # Values come from the position within the loop; timestamps come from
        # the raw tick, which never goes backwards. Splitting the two is what
        # lets the race restart without time appearing to restart with it.
        loop_tick = (self.tick - 1) % LOOP_TICKS + 1

        # Gaps advance only when /intervals would have refreshed, and carry the
        # timestamp of that refresh rather than of this poll. Derived from a
        # fixed anchor, not from `now`: a real interval row repeats byte for byte
        # across polls, so drift in the poll loop must not make it look fresh.
        # tick is 1-based, so shift before grouping to get whole groups of three
        interval_tick = (loop_tick - 1) // TICKS_PER_INTERVAL_REFRESH
        sample_index = (self.tick - 1) // TICKS_PER_INTERVAL_REFRESH
        gap_updated_at = self.started_at + timedelta(
            seconds=sample_index * TICKS_PER_INTERVAL_REFRESH * config.POLL_POSITIONS_INTERVAL_S
        )

        # Seeded per tick rather than drawn from the module RNG: the serverless
        # path regenerates the same tick on every request, and unseeded jitter
        # would make each driver's gap history a different shape every two
        # seconds, which reads as noise in the sparklines.
        rng = random.Random(loop_tick)

        # Fraction of the loop elapsed, in [0, 1).
        phase = interval_tick / INTERVAL_TICKS_PER_LOOP

        lap_number = self.current_lap()
        cumulative_gap_to_leader = 0.0

        for position in range(1, 21):
            idx = position - 1
            driver = MOCK_DRIVERS[idx]

            # Get base gap to car ahead
            gap_to_ahead = self.base_gaps[idx]

            # The scripted battles. Each is a periodic function of `phase`, so
            # the loop closes without a visible jump, and their widest points are
            # deliberately spread: P2 is widest at phase 0 and 0.5, so P4 is put
            # at its closest at 0.5 and P10 at 0. Let them coincide and the board
            # sits empty for a minute at a time, which reads as broken.
            if position == 2:
                # The marquee battle: two attacking runs per loop, each holding
                # inside a tenth of a second long enough to register as HOT.
                # The floor is a clamp rather than a curve on purpose - a pure
                # cosine is flattest exactly where its closing rate is zero, and
                # HOT needs the gap small *and* still shrinking.
                gap_to_ahead = max(0.10, 0.62 + 0.55 * math.cos(4 * math.pi * phase))
            elif position == 4:
                # A long single-run battle, closing through the middle of the
                # loop and giving the gap back over the second half.
                gap_to_ahead = 0.95 + 0.62 * math.cos(2 * math.pi * phase)
            elif position == 7:
                # Close but going nowhere, and no pace advantage to back it up.
                # Deliberately the case the detector should decline to call a
                # battle - a demo where everything is a battle shows nothing.
                gap_to_ahead = 1.70 + 0.18 * math.sin(2 * math.pi * phase)
            elif position == 10:
                # Three short skirmishes per loop, offset by half a cycle so one
                # of them lands on the seam where P2 and P4 are both cold.
                gap_to_ahead = 0.85 + 0.42 * math.cos(6 * math.pi * phase + math.pi)
            else:
                # Others have stable gaps with small variations
                gap_to_ahead += rng.uniform(-0.05, 0.05)

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
                # Derived from the lap rather than drawn fresh each tick: a tire
                # age that jumps between 5 and 25 laps every 1.5 seconds is the
                # first thing anyone notices is fake.
                tire_age_laps=(lap_number + idx) % 20 + 5,
                pit_stops_count=lap_number // 15,
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
        """
        Lap number implied by the current tick, wrapped to the scripted race.

        Wrapping matters for a demo left running: without it the card reads
        "Lap 4113 / 24" by the end of an afternoon.
        """
        loop_tick = (self.tick - 1) % LOOP_TICKS if self.tick else 0
        return loop_tick // TICKS_PER_LAP + 1
    
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
            total_laps=TOTAL_LAPS,
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
