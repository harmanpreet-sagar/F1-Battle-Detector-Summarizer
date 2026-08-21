"""
Unit tests for battle detection and scoring.
"""
import pytest
from datetime import datetime, timedelta

from app.battle import (
    calculate_battle_score,
    calculate_closing_rate,
    calculate_pace_delta,
    detect_battles,
    detect_pit_window,
    reset_battle_tracker,
)
from app.config import config
from app.models import BattleFlags, DriverState


BASE_TIME = datetime(2026, 3, 5, 14, 30, 0)


def make_state(
    driver_number=1,
    position=1,
    gap_to_ahead_s=None,
    last_lap_time_s=None,
    offset_s=0.0,
    gap_offset_s=None,
    data_confidence="high",
):
    """Build a DriverState at BASE_TIME + offset_s."""
    return DriverState(
        driver_number=driver_number,
        full_name=f"Driver {driver_number}",
        team_name="Test Team",
        position=position,
        last_lap_time_s=last_lap_time_s,
        gap_to_leader_s=None,
        gap_to_ahead_s=gap_to_ahead_s,
        updated_at=BASE_TIME + timedelta(seconds=offset_s),
        gap_updated_at=(
            BASE_TIME + timedelta(seconds=gap_offset_s)
            if gap_offset_s is not None
            else None
        ),
        data_confidence=data_confidence,
    )


@pytest.fixture(autouse=True)
def clean_tracker():
    """The stability filter is module-global; isolate each test from the last."""
    reset_battle_tracker()
    yield
    reset_battle_tracker()


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------

# gap, closing_rate, pace_delta, expected score, reaches WATCH threshold?
SCORE_CASES = [
    # Nose to tail, no trend data at all - gap alone is half the score
    (0.0, None, None, 0.500, False),
    # Nose to tail and clearly quicker - this must register
    (0.0, None, -0.5, 0.700, True),
    # The canonical battle: 0.3s back, closing, half a second a lap quicker
    (0.3, -0.004, -0.5, 0.662, True),
    # Same gap and pace, no measurable closing rate yet
    (0.3, None, -0.5, 0.650, True),
    # Closing hard with no lap data available (early in a session)
    (0.0, -0.02, None, 0.560, True),
    # Following at a distance, no pace advantage - not a battle
    (2.5, None, 0.4, 0.083, False),
    # Losing ground while slower - not a battle
    (1.5, 0.01, 0.3, 0.250, False),
]


@pytest.mark.parametrize("gap,closing_rate,pace_delta,expected,is_watch", SCORE_CASES)
def test_battle_score_table(gap, closing_rate, pace_delta, expected, is_watch):
    """Score is stable and the WATCH threshold is actually reachable."""
    score = calculate_battle_score(gap, closing_rate, pace_delta, BattleFlags())

    assert score == pytest.approx(expected, abs=0.001)
    assert (score >= config.BATTLE_WATCH_SCORE) is is_watch


def test_battle_score_bounds():
    """Score stays within [0, 1] at both extremes."""
    assert calculate_battle_score(0.0, -10.0, -10.0, BattleFlags()) <= 1.0
    assert calculate_battle_score(99.0, 5.0, 5.0, BattleFlags()) >= 0.0


def test_safety_car_penalty():
    """Ensure battles are downweighted under yellow flag."""
    flags_green = BattleFlags(under_yellow=False)
    flags_yellow = BattleFlags(under_yellow=True)

    score_green = calculate_battle_score(1.0, 0.1, -0.2, flags_green)
    score_yellow = calculate_battle_score(1.0, 0.1, -0.2, flags_yellow)

    assert score_yellow < score_green
    assert score_yellow == score_green * 0.5


# --------------------------------------------------------------------------
# Closing rate
# --------------------------------------------------------------------------

def test_closing_rate_uses_real_timestamps():
    """Rate comes from the stored timestamps, not the configured poll interval."""
    # 0.4s closed over 8 real seconds, while the poll interval claims 1.5s/sample
    history = [
        make_state(gap_to_ahead_s=1.0, offset_s=0, gap_offset_s=0),
        make_state(gap_to_ahead_s=0.6, offset_s=8, gap_offset_s=8),
    ]

    assert calculate_closing_rate(history) == pytest.approx(-0.05)


def test_closing_rate_ignores_repeated_interval_samples():
    """
    /intervals refreshes slower than /position, so the same gap is served across
    several polls. Those repeats must not stretch the measured time span.
    """
    # Two distinct interval samples 4s apart, each served across three polls
    history = [
        make_state(gap_to_ahead_s=1.0, offset_s=0.0, gap_offset_s=0.0),
        make_state(gap_to_ahead_s=1.0, offset_s=1.5, gap_offset_s=0.0),
        make_state(gap_to_ahead_s=1.0, offset_s=3.0, gap_offset_s=0.0),
        make_state(gap_to_ahead_s=0.8, offset_s=4.5, gap_offset_s=4.0),
        make_state(gap_to_ahead_s=0.8, offset_s=6.0, gap_offset_s=4.0),
    ]

    # -0.2s over the 4s between interval refreshes, not over the 6s of polling
    assert calculate_closing_rate(history) == pytest.approx(-0.05)


def test_closing_rate_falls_back_to_poll_timestamp():
    """States without gap_updated_at (mock or legacy) still produce a rate."""
    history = [
        make_state(gap_to_ahead_s=1.0, offset_s=0),
        make_state(gap_to_ahead_s=0.7, offset_s=3),
    ]

    assert calculate_closing_rate(history) == pytest.approx(-0.1)


def test_closing_rate_none_without_two_distinct_samples():
    """A single repeated gap sample is not a trend."""
    history = [
        make_state(gap_to_ahead_s=1.0, offset_s=0.0, gap_offset_s=0.0),
        make_state(gap_to_ahead_s=1.0, offset_s=1.5, gap_offset_s=0.0),
    ]

    assert calculate_closing_rate(history) is None
    assert calculate_closing_rate([make_state(gap_to_ahead_s=1.0)]) is None
    assert calculate_closing_rate([]) is None


def test_closing_rate_skips_missing_gaps():
    """None gaps are dropped rather than treated as zero."""
    history = [
        make_state(gap_to_ahead_s=None, offset_s=0),
        make_state(gap_to_ahead_s=1.2, offset_s=2),
        make_state(gap_to_ahead_s=None, offset_s=4),
        make_state(gap_to_ahead_s=1.0, offset_s=6),
    ]

    assert calculate_closing_rate(history) == pytest.approx(-0.05)


# --------------------------------------------------------------------------
# Pace delta
# --------------------------------------------------------------------------

def test_pace_delta_negative_when_chaser_is_faster():
    chaser = [make_state(last_lap_time_s=t) for t in (90.0, 89.8, 89.6)]
    ahead = [make_state(last_lap_time_s=t) for t in (90.4, 90.2, 90.4)]

    # chaser mean 89.8 vs ahead mean 90.333
    assert calculate_pace_delta(chaser, ahead) == pytest.approx(-0.533, abs=0.001)


def test_pace_delta_positive_when_chaser_is_slower():
    chaser = [make_state(last_lap_time_s=91.0)]
    ahead = [make_state(last_lap_time_s=90.0)]

    assert calculate_pace_delta(chaser, ahead) == pytest.approx(1.0)


def test_pace_delta_deduplicates_repeated_lap_times():
    """A lap time repeats across polls until the next lap completes."""
    chaser = [make_state(last_lap_time_s=t) for t in (90.0, 90.0, 90.0, 89.0, 89.0)]
    ahead = [make_state(last_lap_time_s=90.0)]

    # Means the two distinct laps (90.0, 89.0), not the five samples
    assert calculate_pace_delta(chaser, ahead) == pytest.approx(-0.5)


def test_pace_delta_respects_window():
    chaser = [make_state(last_lap_time_s=t) for t in (95.0, 90.0, 90.0)]
    ahead = [make_state(last_lap_time_s=90.0)]

    # window=2 drops the outlier first lap; note the repeated 90.0 collapses to one
    assert calculate_pace_delta(chaser, ahead, window=1) == pytest.approx(0.0)


def test_pace_delta_none_without_lap_data():
    chaser = [make_state(last_lap_time_s=90.0)]
    no_laps = [make_state(last_lap_time_s=None)]

    assert calculate_pace_delta(chaser, no_laps) is None
    assert calculate_pace_delta(no_laps, chaser) is None
    assert calculate_pace_delta([], []) is None


# --------------------------------------------------------------------------
# Detection end to end
# --------------------------------------------------------------------------

def build_battle_scenario():
    """P2 sits 0.3s behind P1, closing, and half a second a lap quicker."""
    leader_history = [
        make_state(driver_number=1, position=1, offset_s=0, last_lap_time_s=90.5),
        make_state(driver_number=1, position=1, offset_s=4, last_lap_time_s=90.5),
    ]
    chaser_history = [
        make_state(
            driver_number=44, position=2, gap_to_ahead_s=0.35,
            offset_s=0, gap_offset_s=0, last_lap_time_s=90.0,
        ),
        make_state(
            driver_number=44, position=2, gap_to_ahead_s=0.30,
            offset_s=4, gap_offset_s=4, last_lap_time_s=90.0,
        ),
    ]

    states = [leader_history[-1], chaser_history[-1]]
    histories = {1: leader_history, 44: chaser_history}
    return states, histories


def test_close_gap_with_closing_rate_is_detected_as_watch():
    """
    The regression this suite exists for: a 0.3s gap with a closing rate and a
    pace advantage must classify as WATCH, not NONE.
    """
    states, histories = build_battle_scenario()

    # The stability filter withholds a battle until it has persisted
    for _ in range(config.BATTLE_MIN_DURATION_UPDATES - 1):
        assert detect_battles(states, histories) == []

    battles = detect_battles(states, histories)

    assert len(battles) == 1
    battle = battles[0]
    assert battle.battle_id == "44_1"
    assert battle.intensity in ("WATCH", "HOT")
    assert battle.battle_score >= config.BATTLE_WATCH_SCORE
    assert battle.closing_rate_s_per_s == pytest.approx(-0.0125)
    assert battle.pace_delta_s_per_lap == pytest.approx(-0.5)


def test_no_battle_without_gap_data():
    """
    Guards the /position-vs-/intervals bug: if gaps never arrive, detection is
    silently dead. Any state carrying gap_to_ahead_s of None yields no battles.
    """
    states, histories = build_battle_scenario()
    for history in histories.values():
        for state in history:
            state.gap_to_ahead_s = None
    for state in states:
        state.gap_to_ahead_s = None

    for _ in range(config.BATTLE_MIN_DURATION_UPDATES + 1):
        assert detect_battles(states, histories) == []


def test_yellow_flag_suppresses_battle():
    """The same scenario under a safety car is halved out of contention."""
    states, histories = build_battle_scenario()

    for _ in range(config.BATTLE_MIN_DURATION_UPDATES + 1):
        battles = detect_battles(states, histories, track_status="sc")

    assert battles == []


def test_battle_stability_filter():
    """Test that new battles don't appear immediately."""
    states, histories = build_battle_scenario()

    seen_counts = [
        len(detect_battles(states, histories))
        for _ in range(config.BATTLE_MIN_DURATION_UPDATES)
    ]

    assert seen_counts[:-1] == [0] * (config.BATTLE_MIN_DURATION_UPDATES - 1)
    assert seen_counts[-1] == 1


# --------------------------------------------------------------------------
# Pit window
# --------------------------------------------------------------------------

def test_pit_stop_detection():
    """Test sudden gap changes indicating pit stops."""
    pitted = [
        make_state(gap_to_ahead_s=1.2, offset_s=0),
        make_state(gap_to_ahead_s=25.0, offset_s=2),
    ]
    steady = [
        make_state(gap_to_ahead_s=1.2, offset_s=0),
        make_state(gap_to_ahead_s=1.4, offset_s=2),
    ]

    assert detect_pit_window(pitted) is True
    assert detect_pit_window(steady) is False
    assert detect_pit_window(steady[:1]) is False
