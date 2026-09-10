"""
Tests for RacePipeline.

The claim being tested is B3: two pipelines can run in the same process without
touching each other. Before the extraction, detection read module-level
`state_manager` and `battle_detector` singletons, so a replay and the live feed
would have shared one set of driver states and one battle tracker.
"""
from datetime import datetime, timedelta, timezone

from app.clock import ReplayClock
from app.config import config
from app.models import DriverState
from app.pipeline import RacePipeline, TickData

RACE_TIME = datetime(2025, 9, 7, 13, 5, 0, tzinfo=timezone.utc)

DRIVERS_INFO = {
    1: {"full_name": "M. Verstappen", "team_name": "Red Bull Racing"},
    44: {"full_name": "L. Hamilton", "team_name": "Ferrari"},
}


def states_tick(at: datetime, gap, track_status=None) -> TickData:
    """A leader and a chaser `gap` behind, as finished DriverState objects."""
    states = [
        DriverState(
            driver_number=1, full_name="M. Verstappen", team_name="Red Bull Racing",
            position=1, gap_to_ahead_s=None, last_lap_time_s=90.5,
            updated_at=at, gap_updated_at=at,
        ),
        DriverState(
            driver_number=44, full_name="L. Hamilton", team_name="Ferrari",
            position=2, gap_to_ahead_s=gap, last_lap_time_s=90.0,
            updated_at=at, gap_updated_at=at,
        ),
    ]
    return TickData(states=states, track_status=track_status)


def rows_tick(at: datetime, gap, track_status=None) -> TickData:
    """
    The same tick as raw OpenF1 rows, the way a live poll delivers it.

    Lap times are included because they are what /laps supplies live; without
    them the pace term is dead and the two paths would not be comparable.
    """
    stamp = at.isoformat()
    return TickData(
        positions=[
            {"driver_number": 1, "position": 1, "date": stamp},
            {"driver_number": 44, "position": 2, "date": stamp},
        ],
        intervals=[
            {"driver_number": 1, "interval": None, "gap_to_leader": 0, "date": stamp},
            {"driver_number": 44, "interval": gap, "gap_to_leader": gap, "date": stamp},
        ],
        drivers_info=DRIVERS_INFO,
        laps={1: [{"lap_duration": 90.5}], 44: [{"lap_duration": 90.0}]},
        track_status=track_status,
    )


def mature(pipeline, clock, track_status=None, source=states_tick):
    """Feed enough distinct gap readings for a battle to clear the filter."""
    battles = []
    for i in range(config.BATTLE_MIN_DURATION_UPDATES):
        at = RACE_TIME + timedelta(seconds=4.0 * i)
        clock.set(at)
        pipeline.ingest(source(at, 0.35 - 0.01 * i, track_status))
        battles = pipeline.detect()
    return battles


def new_pipeline(name="test"):
    clock = ReplayClock(RACE_TIME)
    return RacePipeline(clock=clock, name=name), clock


# --------------------------------------------------------------------------
# Ingest
# --------------------------------------------------------------------------

def test_ingest_accepts_finished_states():
    """The mock generator path: DriverState objects rather than OpenF1 rows."""
    pipeline, clock = new_pipeline()
    pipeline.ingest(states_tick(RACE_TIME, 0.4))

    assert len(pipeline.current_states()) == 2
    assert pipeline.state.get_current_state(44).gap_to_ahead_s == 0.4


def test_ingest_accepts_raw_openf1_rows():
    """The live path: positions joined to intervals, scored at the tick's time."""
    pipeline, clock = new_pipeline()
    pipeline.ingest(rows_tick(RACE_TIME, 0.4))

    chaser = pipeline.state.get_current_state(44)
    assert chaser.gap_to_ahead_s == 0.4
    assert chaser.full_name == "L. Hamilton"
    # Judged against race time, so a 2025 row is fresh rather than a year stale
    assert chaser.data_confidence == "high"


def test_ingest_of_an_empty_tick_leaves_state_alone():
    pipeline, clock = new_pipeline()
    pipeline.ingest(states_tick(RACE_TIME, 0.4))
    pipeline.ingest(TickData())

    assert len(pipeline.current_states()) == 2


def test_detect_with_no_state_returns_empty():
    pipeline, clock = new_pipeline()

    assert pipeline.detect() == []
    assert pipeline.detected_at is None


# --------------------------------------------------------------------------
# The same tick through either door
# --------------------------------------------------------------------------

def test_raw_rows_and_finished_states_detect_the_same_battle():
    """
    The pipeline must not care which door data came in through. This is the
    small-scale rehearsal of Phase 1's live-equivalence test.
    """
    from_states, states_clock = new_pipeline("states")
    from_rows, rows_clock = new_pipeline("rows")

    a = mature(from_states, states_clock, source=states_tick)
    b = mature(from_rows, rows_clock, source=rows_tick)

    assert [x.battle_id for x in a] == [x.battle_id for x in b] == ["44_1"]
    assert a[0].gap_now_s == b[0].gap_now_s
    assert a[0].intensity == b[0].intensity


# --------------------------------------------------------------------------
# Isolation - the reason the pipeline exists (B3)
# --------------------------------------------------------------------------

def test_two_pipelines_do_not_share_state():
    """
    A replay and the live feed in one process. Before the extraction these
    shared one StateManager and one BattleDetector, and the last writer won.
    """
    live, live_clock = new_pipeline("live")
    replay, replay_clock = new_pipeline("replay")

    mature(live, live_clock)

    assert len(live.latest_battles()) == 1
    assert replay.latest_battles() == []
    assert replay.current_states() == []
    assert replay.detected_at is None


def test_two_pipelines_keep_independent_clocks():
    live, live_clock = new_pipeline("live")
    replay, replay_clock = new_pipeline("replay")

    replay_clock.advance(timedelta(hours=3))

    live.ingest(states_tick(RACE_TIME, 0.4))
    live.detect()
    replay.ingest(states_tick(RACE_TIME, 0.4))
    replay.detect()

    assert live.detected_at == RACE_TIME
    assert replay.detected_at == RACE_TIME + timedelta(hours=3)


def test_resetting_one_pipeline_leaves_the_other_alone():
    live, live_clock = new_pipeline("live")
    replay, replay_clock = new_pipeline("replay")

    mature(live, live_clock)
    mature(replay, replay_clock)
    replay.reset()

    assert len(live.latest_battles()) == 1
    assert replay.latest_battles() == []
    assert replay.current_states() == []
    assert replay.track_status is None


# --------------------------------------------------------------------------
# Track status
# --------------------------------------------------------------------------

def test_track_status_from_the_tick_reaches_detection():
    """A safety car halves the score, which drops this battle out of contention."""
    pipeline, clock = new_pipeline()

    assert mature(pipeline, clock, track_status="sc") == []
    assert pipeline.track_status == "sc"
