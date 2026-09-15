"""
Unit tests for battle detection and scoring.
"""
import pytest
from datetime import datetime, timedelta, timezone

from app.models import BattleFlags
from app.battle import (
    BattleDetector,
    calculate_battle_score,
    calculate_closing_rate,
    calculate_pace_delta,
    detect_pit_window,
)
from app.config import config
from app.models import BattleFlags, DriverState


BASE_TIME = datetime(2026, 3, 5, 14, 30, 0, tzinfo=timezone.utc)

# Gap readings are 4s apart (see poll()), so this is the instant at which
# reading `sample` arrives. Detection is run at that instant rather than at wall
# time, which is what makes the eviction and stability assertions below mean
# anything under replay.
def at(sample: int) -> datetime:
    return BASE_TIME + timedelta(seconds=4.0 * sample)


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


@pytest.fixture
def detector():
    """A detector per test - no shared state to reset between them."""
    return BattleDetector()


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


def poll(sample: int):
    """
    States as of gap reading number `sample` (0-based).

    Each reading is 4s apart - the real /intervals refresh rate - with the gap
    closing 0.01s per reading.
    """
    leader_history, chaser_history = [], []
    for i in range(sample + 1):
        at = 4.0 * i
        leader_history.append(
            make_state(driver_number=1, position=1, offset_s=at,
                       gap_offset_s=at, last_lap_time_s=90.5)
        )
        chaser_history.append(
            make_state(driver_number=44, position=2, gap_to_ahead_s=0.35 - 0.01 * i,
                       offset_s=at, gap_offset_s=at, last_lap_time_s=90.0)
        )

    states = [leader_history[-1], chaser_history[-1]]
    histories = {1: leader_history, 44: chaser_history}
    return states, histories


def mature(detector, track_status=None):
    """Feed enough distinct gap readings for a battle to clear the filter."""
    battles = []
    for sample in range(config.BATTLE_MIN_DURATION_UPDATES):
        battles = detector.detect(
            *poll(sample), track_status=track_status, now=at(sample)
        )
    return battles


def test_close_gap_with_closing_rate_is_detected_as_watch(detector):
    """
    The regression this suite exists for: a 0.3s gap with a closing rate and a
    pace advantage must classify as WATCH, not NONE.
    """
    battles = mature(detector)

    assert len(battles) == 1
    battle = battles[0]
    assert battle.battle_id == "44_1"
    assert battle.intensity in ("WATCH", "HOT")
    assert battle.battle_score >= config.BATTLE_WATCH_SCORE
    assert battle.closing_rate_s_per_s == pytest.approx(-0.0025)
    assert battle.pace_delta_s_per_lap == pytest.approx(-0.5)


def test_no_battle_without_gap_data(detector):
    """
    Guards the /position-vs-/intervals bug: if gaps never arrive, detection is
    silently dead. Any state carrying gap_to_ahead_s of None yields no battles.
    """
    for sample in range(config.BATTLE_MIN_DURATION_UPDATES + 1):
        states, histories = poll(sample)
        for history in histories.values():
            for state in history:
                state.gap_to_ahead_s = None
        for state in states:
            state.gap_to_ahead_s = None

        assert detector.detect(states, histories, now=at(sample)) == []


def test_yellow_flag_suppresses_battle(detector):
    """The same scenario under a safety car is halved out of contention."""
    assert mature(detector, track_status="sc") == []


# --------------------------------------------------------------------------
# Stability filter
# --------------------------------------------------------------------------

def test_battle_stability_filter(detector):
    """Test that new battles don't appear immediately."""
    seen = [
        len(detector.detect(*poll(sample), now=at(sample)))
        for sample in range(config.BATTLE_MIN_DURATION_UPDATES)
    ]

    assert seen[:-1] == [0] * (config.BATTLE_MIN_DURATION_UPDATES - 1)
    assert seen[-1] == 1


def test_stability_filter_counts_gap_readings_not_polls(detector):
    """
    Gaps come from /intervals, which refreshes slower than the poll loop. Polling
    the same reading over and over must not mature a battle - otherwise the
    filter certifies a battle it has only ever seen once.
    """
    states, histories = poll(0)

    for repeat in range(config.BATTLE_MIN_DURATION_UPDATES * 5):
        assert detector.detect(states, histories, now=at(repeat)) == []

    assert detector.tracked_count == 1
    assert detector._tracked["44_1"].distinct_samples == 1


def test_duration_updates_reports_distinct_readings(detector):
    """duration_updates is the count of genuine readings the battle survived."""
    battles = mature(detector)

    assert battles[0].duration_updates == config.BATTLE_MIN_DURATION_UPDATES


# --------------------------------------------------------------------------
# Eviction
# --------------------------------------------------------------------------

def test_long_running_battle_is_not_evicted(detector):
    """
    Eviction keys off last_seen, not first_seen. A battle that has been running
    for an hour is the most interesting thing on track; dropping it resets its
    duration and flickers it out of the UI for several polls.
    """
    mature(detector)

    # Pretend this battle started long before the eviction window
    tracked = detector._tracked["44_1"]
    tracked.first_seen -= timedelta(seconds=config.BATTLE_EVICTION_S * 10)

    battles = detector.detect(
        *poll(config.BATTLE_MIN_DURATION_UPDATES),
        now=at(config.BATTLE_MIN_DURATION_UPDATES),
    )

    assert len(battles) == 1
    assert battles[0].duration_updates == config.BATTLE_MIN_DURATION_UPDATES + 1
    assert detector.tracked_count == 1


def test_battle_evicted_after_absence(detector):
    """A battle that stops being detected is dropped once it goes stale."""
    mature(detector)
    detector._tracked["44_1"].last_seen -= timedelta(
        seconds=config.BATTLE_EVICTION_S + 1
    )

    # A poll where the pair is no longer close enough to register
    states, histories = poll(config.BATTLE_MIN_DURATION_UPDATES)
    for state in states:
        state.gap_to_ahead_s = None

    assert detector.detect(
        states, histories, now=at(config.BATTLE_MIN_DURATION_UPDATES)
    ) == []
    assert detector.tracked_count == 0


# --------------------------------------------------------------------------
# Result cache
# --------------------------------------------------------------------------

def test_cache_holds_last_result_without_rerunning_detection(detector):
    """
    /battles/top reads this cache. Reading it must not advance the stability
    filter - that was the bug where two open browser tabs matured battles twice
    as fast and zero clients matured them never.
    """
    mature(detector)
    tracked_before = detector._tracked["44_1"].distinct_samples

    for _ in range(10):
        assert len(detector.latest_battles) == 1

    assert detector._tracked["44_1"].distinct_samples == tracked_before


def test_cache_is_empty_before_first_detection(detector):
    assert detector.latest_battles == []
    assert detector.detected_at is None


def test_detected_at_advances_with_each_run(detector):
    detector.detect(*poll(0), now=at(0))
    first = detector.detected_at
    detector.detect(*poll(1), now=at(1))

    assert first is not None
    assert detector.detected_at >= first


def test_latest_battles_is_a_copy(detector):
    """Callers mutating the returned list must not corrupt the cache."""
    mature(detector)

    detector.latest_battles.clear()

    assert len(detector.latest_battles) == 1


def test_reset_clears_tracker_and_cache(detector):
    mature(detector)

    detector.reset()

    assert detector.latest_battles == []
    assert detector.detected_at is None
    assert detector.tracked_count == 0


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


# --------------------------------------------------------------------------
# Flags
# --------------------------------------------------------------------------

def test_battle_flags_carry_no_unreachable_fields():
    """
    blue_flag_situation was computed as abs(ahead.position - chaser.position) > 5
    over *adjacent* positions, so it was always False. Detection pairs adjacent
    classified positions, and F1 classification already orders by lap count, so a
    lapped car is never adjacent to the car lapping it - the flag could not be
    implemented under this pairing model, and a permanently-false field in the
    API implies a capability that is not there.
    """
    assert "blue_flag_situation" not in BattleFlags.model_fields


def test_pit_window_flag_is_still_populated(detector):
    """Deleting the dead pit-window no-op must not disturb the working one."""
    states, histories = poll(1)
    # A 15s gap jump is the pit-stop signature detect_pit_window looks for
    histories[44][-1].gap_to_ahead_s = 20.0
    histories[44][-2].gap_to_ahead_s = 0.35

    assert detect_pit_window(histories[44]) is True
