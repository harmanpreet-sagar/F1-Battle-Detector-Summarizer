"""
Tests for stateless mock mode - the path that makes serverless hosting work.

The thing under test is an equivalence claim: a pipeline rebuilt from scratch on
every request shows the same race a long-running poll loop would have. These
pin the parts of that claim which would break silently in production, where the
only symptom is a demo that looks dead.
"""
from datetime import timedelta
import time

import pytest

from app import demo
from app.config import config
from app.mock_data import LOOP_TICKS, MockDataGenerator, TICKS_PER_INTERVAL_REFRESH

# A tick well away from RACE_EPOCH, so the replay window is never truncated by
# the start of the race the way it is at tick 0.
MID_RACE_TICK = 10 * LOOP_TICKS + 60


def at(tick: int):
    return demo.tick_time(tick)


# --------------------------------------------------------------------------
# Tick arithmetic
# --------------------------------------------------------------------------

def test_tick_and_time_are_inverses():
    for tick in (1, 47, LOOP_TICKS, MID_RACE_TICK):
        assert demo.tick_at(demo.tick_time(tick)) == tick


def test_the_tick_advances_with_the_wall_clock():
    start = at(MID_RACE_TICK)
    later = start + timedelta(seconds=config.POLL_POSITIONS_INTERVAL_S * 10)

    assert demo.tick_at(later) - demo.tick_at(start) == 10


def test_everyone_watching_sees_the_same_race():
    """
    Two viewers hitting two instances must agree. The epoch is fixed rather than
    per-process for exactly this reason - a process-start anchor would put every
    cold start at lap 1 of its own private race.
    """
    moment = at(MID_RACE_TICK) + timedelta(milliseconds=250)

    one = demo.build_pipeline(moment).latest_battles()
    two = demo.build_pipeline(moment).latest_battles()

    assert [b.model_dump() for b in one] == [b.model_dump() for b in two]


# --------------------------------------------------------------------------
# The rebuilt pipeline
# --------------------------------------------------------------------------

def test_a_rebuilt_pipeline_has_history_not_just_a_snapshot():
    """
    Without history there is no closing rate and no stability filter, so every
    battle would be brand new on every request and none would ever be shown.
    """
    pipeline = demo.build_pipeline(at(MID_RACE_TICK))

    assert len(pipeline.current_states()) == 20
    assert len(pipeline.history(1)) == config.DEMO_WINDOW_TICKS


def test_battles_survive_the_stability_filter():
    """
    The regression that would ship silently: a window too short for
    BATTLE_MIN_DURATION_UPDATES distinct gap samples returns an empty board on
    every request, and looks exactly like "no session".
    """
    pipeline = demo.build_pipeline(at(MID_RACE_TICK))
    battles = pipeline.latest_battles()

    assert battles, "stateless mode detected no battles at all"
    assert max(b.duration_updates for b in battles) >= config.BATTLE_MIN_DURATION_UPDATES


def test_the_window_holds_enough_distinct_gap_samples():
    """The trend window is counted in /intervals refreshes, not in polls."""
    needed = config.BATTLE_GAP_TREND_WINDOW * TICKS_PER_INTERVAL_REFRESH

    assert config.DEMO_WINDOW_TICKS >= needed

    history = demo.build_pipeline(at(MID_RACE_TICK)).history(11)
    distinct = {state.gap_updated_at for state in history}

    assert len(distinct) >= config.BATTLE_GAP_TREND_WINDOW


def test_data_looks_fresh_to_the_client():
    """
    The frontend calls the feed stale above three seconds. The newest state must
    therefore be stamped with its own tick time, not with the start of the
    replayed window a minute earlier.
    """
    now = at(MID_RACE_TICK) + timedelta(seconds=1.0)
    newest = max(s.updated_at for s in demo.build_pipeline(now).current_states())

    assert 0 <= (now - newest).total_seconds() <= config.POLL_POSITIONS_INTERVAL_S


def test_detection_runs_on_race_time_not_the_wall():
    pipeline = demo.build_pipeline(at(MID_RACE_TICK))

    assert pipeline.detected_at == at(MID_RACE_TICK)


def test_rebuilding_is_cheap_enough_to_do_per_request():
    """
    This runs on every request, so its cost is the hosting bill. Generous enough
    not to flake on a loaded CI box, tight enough to catch the window or the
    loop being widened by an order of magnitude.
    """
    start = time.perf_counter()
    for i in range(5):
        demo.build_pipeline(at(MID_RACE_TICK + i))
    per_call = (time.perf_counter() - start) / 5

    assert per_call < 0.25, f"{per_call * 1000:.0f}ms per request"


# --------------------------------------------------------------------------
# Running on repeat
# --------------------------------------------------------------------------

def test_the_race_repeats():
    """A demo left open must not run out of race."""
    once = demo.build_pipeline(at(MID_RACE_TICK)).current_states()
    again = demo.build_pipeline(at(MID_RACE_TICK + LOOP_TICKS)).current_states()

    gaps = {s.driver_number: s.gap_to_ahead_s for s in once}
    later = {s.driver_number: s.gap_to_ahead_s for s in again}

    assert gaps == later


def test_the_loop_seam_is_not_a_jump():
    """
    Gaps are written as periodic functions so the restart is invisible. A
    discontinuity here reads downstream as every car pitting at once.
    """
    generator = MockDataGenerator()

    def gaps_at(tick):
        generator.tick = tick - 1
        return [s.gap_to_ahead_s for s in generator.generate_driver_states(now=at(tick))[1:]]

    before = gaps_at(LOOP_TICKS)
    after = gaps_at(LOOP_TICKS + 1)

    # One tick's worth of movement, not a reset to the grid.
    assert max(abs(a - b) for a, b in zip(before, after)) < 0.15


def test_the_lap_counter_wraps_with_the_race():
    session = demo.build_session(at(MID_RACE_TICK))

    assert 1 <= session.current_lap <= session.total_laps


def test_the_board_is_never_empty_for_long():
    """
    The scripted battles are spread so their quiet phases do not coincide. When
    they do, the demo shows an empty board for a minute at a time and reads as
    broken.
    """
    empty_run = worst = 0
    for tick in range(LOOP_TICKS, 2 * LOOP_TICKS, 3):
        if demo.build_pipeline(at(tick)).latest_battles():
            empty_run = 0
        else:
            empty_run += 1
            worst = max(worst, empty_run)

    assert worst * 3 * config.POLL_POSITIONS_INTERVAL_S < 20


def test_the_demo_reaches_its_top_intensity():
    """
    HOT needs a sub-1.2s gap that is still closing and a chaser half a second a
    lap quicker. If the scripted race never gets there, the demo silently only
    ever shows half of what the product does.
    """
    intensities = set()
    for tick in range(LOOP_TICKS, 2 * LOOP_TICKS, 3):
        intensities.update(b.intensity for b in demo.build_pipeline(at(tick)).latest_battles())

    assert intensities == {"HOT", "WATCH"}


# --------------------------------------------------------------------------
# Wiring
# --------------------------------------------------------------------------

def test_stateless_mode_is_off_by_default(monkeypatch):
    """Docker and local runs keep the long-running poll loop."""
    from app.config import resolve_stateless

    monkeypatch.delenv("DEMO_STATELESS", raising=False)
    monkeypatch.delenv("VERCEL", raising=False)

    assert resolve_stateless() is False


def test_vercel_turns_stateless_mode_on_by_itself(monkeypatch):
    from app.config import resolve_stateless

    monkeypatch.delenv("DEMO_STATELESS", raising=False)
    monkeypatch.setenv("VERCEL", "1")

    assert resolve_stateless() is True


@pytest.mark.parametrize("value,expected", [("true", True), ("1", True), ("false", False)])
def test_stateless_mode_can_be_forced_either_way(monkeypatch, value, expected):
    from app.config import resolve_stateless

    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("DEMO_STATELESS", value)

    assert resolve_stateless() is expected


def test_endpoints_serve_the_demo_without_a_poll_loop(monkeypatch):
    """
    The whole point, end to end: no lifespan, no background task, and the API
    still answers with a live-looking race.
    """
    from fastapi.testclient import TestClient
    from app.main import app

    monkeypatch.setattr(config, "DEMO_STATELESS", True)
    client = TestClient(app)

    battles = client.get("/battles/top?k=5&min_intensity=WATCH").json()
    assert battles["detected_at"] is not None
    assert battles["battles"], "no battles served in stateless mode"
    assert battles["battles"][0]["chaser_name"]

    session = client.get("/session/current").json()
    assert session["session_name"] == "Race"
    assert 1 <= session["current_lap"] <= session["total_laps"]

    assert client.get("/health").json()["status"] == "healthy"
    assert client.get("/state/latest").json()["count"] == 20
