"""
Tests for the state manager, in particular the /position + /intervals join.
"""
import pytest
from datetime import datetime, timedelta, timezone

from app.config import config
from app.models import DriverState, SessionStatus
from app.session import SessionManager
from app.state import StateManager, parse_gap, parse_openf1_timestamp

BASE_TIME = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def iso(offset_s: float = 0.0) -> str:
    """
    An OpenF1-style timestamp `offset_s` seconds before BASE_TIME.

    Anchored to BASE_TIME rather than to the wall so the confidence assertions
    below test the injected clock instead of how long the test took to run.
    """
    return (BASE_TIME - timedelta(seconds=offset_s)).isoformat().replace(
        "+00:00", "Z"
    )


DRIVERS_INFO = {
    1: {"full_name": "Max Verstappen", "team_name": "Red Bull Racing"},
    44: {"full_name": "Lewis Hamilton", "team_name": "Mercedes"},
}


@pytest.fixture
def manager():
    return StateManager()


# --------------------------------------------------------------------------
# Field parsing
# --------------------------------------------------------------------------

def test_parse_gap_accepts_numbers_and_rejects_lap_strings():
    assert parse_gap(1.234) == 1.234
    assert parse_gap(0) == 0.0
    # /intervals reports lapped cars as strings, and the leader's interval as null
    assert parse_gap("+1 LAP") is None
    assert parse_gap(None) is None


def test_parse_timestamp_handles_z_suffix_and_garbage():
    assert parse_openf1_timestamp("2026-03-05T14:30:00Z").year == 2026
    assert parse_openf1_timestamp(None) is None
    assert parse_openf1_timestamp("not a date") is None


# --------------------------------------------------------------------------
# The join
# --------------------------------------------------------------------------

def test_gaps_come_from_intervals_not_positions(manager):
    """
    /position carries no gap fields. Without the intervals join every gap is
    None and nothing is detectable - the bug this join exists to fix.
    """
    positions = [
        {"driver_number": 1, "position": 1, "date": iso()},
        {"driver_number": 44, "position": 2, "date": iso()},
    ]
    intervals = [
        {"driver_number": 1, "interval": None, "gap_to_leader": 0, "date": iso(1.0)},
        {"driver_number": 44, "interval": 0.42, "gap_to_leader": 0.42, "date": iso(1.0)},
    ]

    manager.update_from_openf1_positions(
        positions, DRIVERS_INFO, intervals=intervals, now=BASE_TIME
    )

    chaser = manager.get_current_state(44)
    assert chaser.gap_to_ahead_s == 0.42
    assert chaser.gap_to_leader_s == 0.42
    assert chaser.gap_updated_at is not None
    # The gap is stamped with the interval's own time, not the position poll's
    assert chaser.gap_updated_at < chaser.updated_at


def test_positions_without_intervals_leave_gaps_none(manager):
    positions = [{"driver_number": 44, "position": 2, "date": iso()}]

    manager.update_from_openf1_positions(positions, DRIVERS_INFO, now=BASE_TIME)

    state = manager.get_current_state(44)
    assert state.gap_to_ahead_s is None
    assert state.gap_updated_at is None


def test_stale_interval_is_carried_forward_with_its_original_timestamp(manager):
    """
    /intervals refreshes ~every 4s against a 1.5s position poll, so polls arrive
    with no fresh row. The gap carries forward; its timestamp does not advance,
    which is what stops the closing rate from reading it as a new sample.
    """
    intervals = [{"driver_number": 44, "interval": 0.5, "gap_to_leader": 0.5, "date": iso(1.0)}]

    manager.update_from_openf1_positions(
        [{"driver_number": 44, "position": 2, "date": iso()}], DRIVERS_INFO,
        intervals=intervals, now=BASE_TIME,
    )
    first = manager.get_current_state(44)

    # Next poll: fresh position, no new interval row
    manager.update_from_openf1_positions(
        [{"driver_number": 44, "position": 2, "date": iso()}], DRIVERS_INFO,
        intervals=[], now=BASE_TIME,
    )
    second = manager.get_current_state(44)

    assert second.gap_to_ahead_s == 0.5
    assert second.gap_updated_at == first.gap_updated_at
    assert second.updated_at >= first.updated_at


def test_long_stale_gap_downgrades_confidence(manager):
    """A gap that has missed several interval refreshes is no longer trusted."""
    positions = [{"driver_number": 44, "position": 2, "date": iso()}]
    intervals = [{"driver_number": 44, "interval": 0.5, "date": iso(60.0)}]

    manager.update_from_openf1_positions(
        positions, DRIVERS_INFO, intervals=intervals, now=BASE_TIME
    )

    assert manager.get_current_state(44).data_confidence == "low"


def test_lapped_car_interval_string_does_not_crash_the_join(manager):
    positions = [{"driver_number": 44, "position": 20, "date": iso()}]
    intervals = [{"driver_number": 44, "interval": "+1 LAP", "gap_to_leader": "+1 LAP", "date": iso()}]

    manager.update_from_openf1_positions(
        positions, DRIVERS_INFO, intervals=intervals, now=BASE_TIME
    )

    state = manager.get_current_state(44)
    assert state.gap_to_ahead_s is None
    assert state.gap_to_leader_s is None


# --------------------------------------------------------------------------
# Lap times
# --------------------------------------------------------------------------

def test_last_lap_time_populated_from_lap_data(manager):
    """Without this, pace_delta is always None and 20% of the score is dead."""
    positions = [{"driver_number": 44, "position": 2, "date": iso()}]
    laps = {
        44: [
            {"lap_number": 10, "lap_duration": 90.5},
            {"lap_number": 11, "lap_duration": 89.9},
        ]
    }

    manager.update_from_openf1_positions(
        positions, DRIVERS_INFO, laps=laps, now=BASE_TIME
    )

    # Most recent completed lap
    assert manager.get_current_state(44).last_lap_time_s == 89.9


def test_missing_lap_data_leaves_lap_time_none(manager):
    positions = [{"driver_number": 44, "position": 2, "date": iso()}]

    manager.update_from_openf1_positions(
        positions, DRIVERS_INFO, laps={}, now=BASE_TIME
    )

    assert manager.get_current_state(44).last_lap_time_s is None


# --------------------------------------------------------------------------
# Mock generator fidelity
# --------------------------------------------------------------------------

def test_mock_gaps_repeat_across_polls_like_real_intervals():
    """
    A real /intervals row repeats byte for byte until the endpoint refreshes.
    The mock must do the same, or TEST_MODE never exercises the repeated-sample
    path and the stability filter looks like it advances once per poll.
    """
    from app.mock_data import MockDataGenerator, TICKS_PER_INTERVAL_REFRESH

    generator = MockDataGenerator()
    samples = []
    for _ in range(TICKS_PER_INTERVAL_REFRESH * 2):
        chaser = next(s for s in generator.generate_driver_states() if s.position == 2)
        samples.append((chaser.gap_updated_at, chaser.gap_to_ahead_s))

    distinct = {stamp for stamp, _ in samples}
    assert len(distinct) == 2, "expected exactly two interval refreshes over six polls"

    # Within one refresh the gap itself must not move either
    for stamp in distinct:
        gaps = {gap for s, gap in samples if s == stamp}
        assert len(gaps) == 1


# --------------------------------------------------------------------------
# History depth (P2-3)
# --------------------------------------------------------------------------

def test_history_is_deeper_than_the_detection_trend_window():
    """
    History length used to be pinned to BATTLE_GAP_TREND_WINDOW (6), so
    GET /drivers/{n}/trend could never return its default 10 points - about 9
    seconds of history at a 1.5s poll.
    """
    assert config.HISTORY_MAX_LEN > config.BATTLE_GAP_TREND_WINDOW
    assert config.HISTORY_MAX_LEN >= 10, "trend endpoint defaults to points=10"

    manager = StateManager()
    for i in range(config.HISTORY_MAX_LEN + 20):
        manager.update_driver_state(
            DriverState(
                driver_number=44, full_name="L. Hamilton", team_name="Mercedes",
                position=2, gap_to_ahead_s=0.5, updated_at=BASE_TIME,
            )
        )

    history = manager.get_history(44)
    assert len(history) == config.HISTORY_MAX_LEN


def test_history_retains_enough_for_the_trend_endpoint_default():
    """A driver polled for 15s can serve the endpoint's default 10 points."""
    manager = StateManager()
    for i in range(10):
        manager.update_driver_state(
            DriverState(
                driver_number=1, full_name="M. Verstappen", team_name="Red Bull",
                position=1, updated_at=BASE_TIME + timedelta(seconds=1.5 * i),
            )
        )

    assert len(manager.get_history(1)[-10:]) == 10


# --------------------------------------------------------------------------
# Session lifecycle (P2-4)
# --------------------------------------------------------------------------

def _session(session_key: int) -> SessionStatus:
    return SessionStatus(
        session_key=session_key, session_name="Race", session_type="Race",
        session_status="started", circuit_short_name="Test", meeting_name="Test GP",
        gmt_offset="+00:00", updated_at=BASE_TIME,
    )


def test_new_session_key_is_reported_as_a_change():
    manager = SessionManager()

    assert manager.set_session(_session(1)) is True
    assert manager.set_session(_session(2)) is True


def test_same_session_key_is_not_a_change():
    """The session endpoint is polled every 30s; re-reading it must not reset."""
    manager = SessionManager()
    manager.set_session(_session(1))

    for _ in range(5):
        assert manager.set_session(_session(1)) is False


def test_losing_the_session_does_not_count_as_a_change():
    """
    A blip in the OpenF1 response, or the race ending, must not wipe a live
    session's accumulated history - only arriving at a genuinely new session does.
    """
    manager = SessionManager()
    manager.set_session(_session(1))

    assert manager.set_session(None) is False
    # ...and coming back to the same session is still not a change
    assert manager.set_session(_session(1)) is False


def test_returning_to_a_different_session_after_a_gap_is_a_change():
    manager = SessionManager()
    manager.set_session(_session(1))
    manager.set_session(None)

    assert manager.set_session(_session(2)) is True


def test_clear_drops_all_driver_state():
    manager = StateManager()
    manager.update_driver_state(
        DriverState(
            driver_number=44, full_name="L. Hamilton", team_name="Mercedes",
            position=2, updated_at=BASE_TIME,
        )
    )

    manager.clear()

    assert manager.get_all_current_states() == []
    assert manager.get_history(44) == []
