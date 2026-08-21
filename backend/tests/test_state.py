"""
Tests for the state manager, in particular the /position + /intervals join.
"""
import pytest
from datetime import datetime, timedelta, timezone

from app.state import StateManager, parse_gap, parse_openf1_timestamp


def iso(offset_s: float = 0.0) -> str:
    """An OpenF1-style timestamp `offset_s` seconds in the past."""
    return (datetime.now(timezone.utc) - timedelta(seconds=offset_s)).isoformat().replace(
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

    manager.update_from_openf1_positions(positions, DRIVERS_INFO, intervals=intervals)

    chaser = manager.get_current_state(44)
    assert chaser.gap_to_ahead_s == 0.42
    assert chaser.gap_to_leader_s == 0.42
    assert chaser.gap_updated_at is not None
    # The gap is stamped with the interval's own time, not the position poll's
    assert chaser.gap_updated_at < chaser.updated_at


def test_positions_without_intervals_leave_gaps_none(manager):
    positions = [{"driver_number": 44, "position": 2, "date": iso()}]

    manager.update_from_openf1_positions(positions, DRIVERS_INFO)

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
        [{"driver_number": 44, "position": 2, "date": iso()}], DRIVERS_INFO, intervals=intervals
    )
    first = manager.get_current_state(44)

    # Next poll: fresh position, no new interval row
    manager.update_from_openf1_positions(
        [{"driver_number": 44, "position": 2, "date": iso()}], DRIVERS_INFO, intervals=[]
    )
    second = manager.get_current_state(44)

    assert second.gap_to_ahead_s == 0.5
    assert second.gap_updated_at == first.gap_updated_at
    assert second.updated_at >= first.updated_at


def test_long_stale_gap_downgrades_confidence(manager):
    """A gap that has missed several interval refreshes is no longer trusted."""
    positions = [{"driver_number": 44, "position": 2, "date": iso()}]
    intervals = [{"driver_number": 44, "interval": 0.5, "date": iso(60.0)}]

    manager.update_from_openf1_positions(positions, DRIVERS_INFO, intervals=intervals)

    assert manager.get_current_state(44).data_confidence == "low"


def test_lapped_car_interval_string_does_not_crash_the_join(manager):
    positions = [{"driver_number": 44, "position": 20, "date": iso()}]
    intervals = [{"driver_number": 44, "interval": "+1 LAP", "gap_to_leader": "+1 LAP", "date": iso()}]

    manager.update_from_openf1_positions(positions, DRIVERS_INFO, intervals=intervals)

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

    manager.update_from_openf1_positions(positions, DRIVERS_INFO, laps=laps)

    # Most recent completed lap
    assert manager.get_current_state(44).last_lap_time_s == 89.9


def test_missing_lap_data_leaves_lap_time_none(manager):
    positions = [{"driver_number": 44, "position": 2, "date": iso()}]

    manager.update_from_openf1_positions(positions, DRIVERS_INFO, laps={})

    assert manager.get_current_state(44).last_lap_time_s is None
