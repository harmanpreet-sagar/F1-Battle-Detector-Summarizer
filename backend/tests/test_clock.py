"""
Time injection: the clock itself, and the two bugs it exists to fix.

B1 (confidence) and B2 (eviction) both came from reading wall time inside the
pipeline. Each has a paired test below: one showing the correct behaviour at
race time, one showing the old behaviour is now only reachable by explicitly
handing the pipeline a wall clock.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.battle import BattleDetector
from app.clock import Clock, ReplayClock, WallClock, ensure_utc, utcnow
from app.config import config
from app.models import DriverState
from app.state import StateManager

# A real instant from the 2025 season. The point of everything below is that
# nothing cares how far in the past it is.
RACE_TIME = datetime(2025, 9, 7, 13, 5, 0, tzinfo=timezone.utc)

DRIVERS_INFO = {44: {"full_name": "L. Hamilton", "team_name": "Ferrari"}}


def pair(at: datetime, gap):
    """A leader and a chaser `gap` seconds behind, both sampled at `at`."""
    leader = DriverState(
        driver_number=1, full_name="Driver 1", team_name="T", position=1,
        gap_to_ahead_s=None, last_lap_time_s=90.5,
        updated_at=at, gap_updated_at=at,
    )
    chaser = DriverState(
        driver_number=44, full_name="Driver 44", team_name="T", position=2,
        gap_to_ahead_s=gap, last_lap_time_s=90.0,
        updated_at=at, gap_updated_at=at,
    )
    return leader, chaser


def mature_battle(detector: BattleDetector, clock: ReplayClock):
    """Feed enough distinct gap readings, at race time, to clear the filter."""
    leader_history, chaser_history = [], []
    battles = []
    for i in range(config.BATTLE_MIN_DURATION_UPDATES):
        clock.set(RACE_TIME + timedelta(seconds=4.0 * i))
        leader, chaser = pair(clock.now(), 0.35 - 0.01 * i)
        leader_history.append(leader)
        chaser_history.append(chaser)
        battles = detector.detect(
            [leader, chaser],
            {1: list(leader_history), 44: list(chaser_history)},
            now=clock.now(),
        )
    return battles


# --------------------------------------------------------------------------
# The clock
# --------------------------------------------------------------------------

def test_utcnow_is_timezone_aware():
    assert utcnow().tzinfo is not None


def test_ensure_utc_treats_naive_as_utc_not_local():
    """
    A naive OpenF1 timestamp is UTC. Reading it as local would shift race time
    by the host's offset - a bug that would only show up on machines outside UTC.
    """
    assert ensure_utc(datetime(2025, 9, 7, 13, 5, 0)) == RACE_TIME


def test_ensure_utc_converts_other_offsets():
    trackside = datetime(2025, 9, 7, 15, 5, 0, tzinfo=timezone(timedelta(hours=2)))
    assert ensure_utc(trackside) == RACE_TIME


def test_wall_clock_satisfies_the_protocol_and_returns_aware_time():
    clock = WallClock()
    assert isinstance(clock, Clock)
    assert clock.now().tzinfo is not None


def test_replay_clock_does_not_advance_on_its_own():
    """Race time moves only when the replay source moves it."""
    clock = ReplayClock(RACE_TIME)
    assert clock.now() == clock.now() == RACE_TIME


def test_replay_clock_set_and_advance():
    clock = ReplayClock(RACE_TIME)
    clock.advance(timedelta(seconds=90))
    assert clock.now() == RACE_TIME + timedelta(seconds=90)
    clock.set(RACE_TIME)
    assert clock.now() == RACE_TIME


def test_replay_clock_normalises_a_naive_start():
    assert ReplayClock(datetime(2025, 9, 7, 13, 5, 0)).now() == RACE_TIME


# --------------------------------------------------------------------------
# B1: confidence was row age vs the wall
# --------------------------------------------------------------------------

def test_historical_row_is_high_confidence_at_its_own_race_time():
    """
    A 2025 row judged at the 2025 instant it came from is fresh. Before the
    clock was injected it was ~a year old, so every driver came back 'low',
    which sets data_quality_warning and multiplies every battle score by 0.7 -
    flagging the entire field of every replayed race.
    """
    manager = StateManager()
    manager.update_from_openf1_positions(
        [{"driver_number": 44, "position": 2, "date": RACE_TIME.isoformat()}],
        DRIVERS_INFO,
        now=RACE_TIME,
    )
    assert manager.get_current_state(44).data_confidence == "high"


def test_same_row_is_low_confidence_when_judged_against_wall_time():
    """The old behaviour, now reachable only by explicitly asking for it."""
    manager = StateManager()
    manager.update_from_openf1_positions(
        [{"driver_number": 44, "position": 2, "date": RACE_TIME.isoformat()}],
        DRIVERS_INFO,
        now=utcnow(),
    )
    assert manager.get_current_state(44).data_confidence == "low"


# --------------------------------------------------------------------------
# B2: eviction ran on wall seconds
# --------------------------------------------------------------------------

def test_battle_evicted_after_the_window_in_race_seconds():
    """
    A battle unseen for BATTLE_EVICTION_S + 1 *race* seconds is evicted, even
    though the wall clock has barely moved. Under the old code a fast batch run
    pushed thousands of ticks through in a few wall seconds and evicted nothing.
    """
    detector = BattleDetector()
    clock = ReplayClock(RACE_TIME)

    assert len(mature_battle(detector, clock)) == 1
    assert detector.tracked_count == 1

    clock.advance(timedelta(seconds=config.BATTLE_EVICTION_S + 1))
    leader, chaser = pair(clock.now(), None)

    assert detector.detect([leader, chaser], {1: [leader], 44: [chaser]}, now=clock.now()) == []
    assert detector.tracked_count == 0


def test_battle_survives_inside_the_window_in_race_seconds():
    """
    The mirror image: at 10x speed the window must not close early either.
    Race time short of the threshold keeps the battle tracked.
    """
    detector = BattleDetector()
    clock = ReplayClock(RACE_TIME)
    mature_battle(detector, clock)

    clock.advance(timedelta(seconds=config.BATTLE_EVICTION_S - 5))
    leader, chaser = pair(clock.now(), None)
    detector.detect([leader, chaser], {1: [leader], 44: [chaser]}, now=clock.now())

    assert detector.tracked_count == 1


def test_detected_at_is_race_time_not_wall_time():
    detector = BattleDetector()
    leader, chaser = pair(RACE_TIME, 0.4)
    detector.detect([leader, chaser], {1: [leader], 44: [chaser]}, now=RACE_TIME)

    assert detector.detected_at == RACE_TIME


# --------------------------------------------------------------------------
# The rule that keeps all of the above true
# --------------------------------------------------------------------------

def test_no_wall_clock_reads_outside_the_clock_module():
    """
    datetime.now() anywhere in the pipeline silently couples behaviour to the
    wall, which is the whole bug class B1 and B2 came from. clock.py is where
    wall time is allowed to live; health.py reports on the server rather than
    on the race, so it is exempt by design.
    """
    exempt = {"clock.py", "health.py"}
    app_dir = Path(__file__).resolve().parent.parent / "app"

    offenders = [
        f"{path.relative_to(app_dir)}:{lineno}"
        for path in sorted(app_dir.rglob("*.py"))
        if path.name not in exempt
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if "datetime.now(" in line
    ]

    assert offenders == [], f"wall-clock reads found: {offenders}"
