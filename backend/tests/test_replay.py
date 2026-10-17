"""
Tests for the replay engine and ReplaySource (1.3).

The engine's one job is to hand the pipeline, at tick t, exactly what a live
poll at t would have returned. Most of these pin a way that could quietly stop
being true: a grid row lost at the start, a lap time known before live could
have known it, a retired car left in the running order.
"""
from datetime import timedelta
import asyncio

import pytest

from app.clock import ReplayClock
from app.config import config
from app.openf1_client import _latest_by_driver
from app.pipeline import RacePipeline, TickData
from app.replay.broadcast import Broadcast
from app.replay.engine import RaceReplay, laps_refresh_ticks, play
from app.replay.store import SessionStore
from app.sources import ReplaySource, build_source
from app.sources.base import DataSource
from app.state import parse_openf1_timestamp
from tests import race_rows
from tests.race_rows import LAP_S, at
from tests.replay_fixtures import excerpt_replay, excerpt_store


def by_driver(rows):
    return {row["driver_number"]: row for row in rows}


def visible(rows, moment, field="date"):
    return [r for r in rows if parse_openf1_timestamp(r.get(field)) <= moment]


# --------------------------------------------------------------------------
# The tick grid
# --------------------------------------------------------------------------

def test_ticks_sit_on_the_live_poll_grid():
    replay = excerpt_replay()

    assert replay.tick_s == config.POLL_POSITIONS_INTERVAL_S
    assert replay.tick_time(0) == replay.start
    assert replay.tick_time(10) - replay.tick_time(9) == timedelta(seconds=replay.tick_s)
    assert replay.tick_at(replay.tick_time(25) + timedelta(seconds=0.4)) == 25


def test_tick_at_is_clamped_to_the_race():
    replay = excerpt_replay()

    assert replay.tick_at(replay.start - timedelta(hours=1)) == 0
    assert replay.tick_at(replay.timeline.end + timedelta(hours=1)) == replay.tick_count - 1


def test_lap_refreshes_follow_the_live_cadence():
    """LiveSource refetches /laps on the first poll at least 10s after the last."""
    assert laps_refresh_ticks(1.5) == 7    # 10.5s, the first poll past 10s
    assert laps_refresh_ticks(2.0) == 5    # exactly 10s is due, not one late


# --------------------------------------------------------------------------
# What a tick contains
# --------------------------------------------------------------------------

@pytest.mark.parametrize("tick", [0, 1, 40, 150, 333])
def test_a_tick_is_the_latest_row_per_driver_as_of_its_time(tick):
    """
    The claim the whole replay rests on, checked against the raw archive with
    the same helper live polling uses.
    """
    replay = excerpt_replay()
    archive = excerpt_store().load(replay.session_key)
    moment = replay.tick_time(tick)

    data = list(replay.ticks(tick, tick))[0][1]

    expected_positions = _latest_by_driver(visible(archive.rows("position"), moment), key=lambda r: r["date"])
    expected_intervals = _latest_by_driver(visible(archive.rows("intervals"), moment), key=lambda r: r["date"])
    assert by_driver(data.positions) == by_driver(expected_positions)
    assert by_driver(data.intervals) == by_driver(expected_intervals)


def test_every_driver_is_on_track_from_the_first_tick():
    """
    /position emits on change. Without the carried grid rows, the last of the
    20 cars got its first position 26 minutes into the Hungarian GP.
    """
    _, first = next(excerpt_replay().ticks())

    assert len(first.positions) == 20


def test_lap_times_arrive_on_the_refresh_not_before():
    """
    A lap completed between two refreshes is invisible until the next one,
    exactly as it is to LiveSource reusing its cached /laps.
    """
    replay = excerpt_replay()
    every = replay.laps_every
    cursor = replay.cursor()

    seen = 0
    for tick in range(0, 12 * every):
        data = cursor.tick(tick)
        refreshed_at = replay.tick_time(tick - tick % every)
        for laps in data.laps.values():
            for lap in laps:
                end = parse_openf1_timestamp(lap["date_start"]) + timedelta(seconds=lap["lap_duration"])
                assert end <= refreshed_at
                seen += 1

    assert seen, "the window should include completed laps"


def test_a_cursor_started_mid_race_matches_one_that_walked_there():
    """Seeking by fast-forward must land on the same state as playing through."""
    replay = excerpt_replay()
    walked = replay.cursor()
    for tick in range(0, 200):
        last = walked.tick(tick)

    jumped = replay.cursor().tick(199)

    assert by_driver(jumped.positions) == by_driver(last.positions)
    assert by_driver(jumped.intervals) == by_driver(last.intervals)
    assert jumped.laps == last.laps
    assert jumped.track_status == last.track_status


def test_a_cursor_only_goes_forward():
    cursor = excerpt_replay().cursor()
    cursor.tick(5)

    with pytest.raises(ValueError, match="only go forward"):
        cursor.tick(5)


def test_the_first_tick_asks_for_a_reset():
    ticks = [data for _, data in excerpt_replay().ticks(10, 12)]

    assert [t.reset for t in ticks] == [True, False, False]


def test_drivers_info_comes_from_the_archive():
    _, first = next(excerpt_replay().ticks())

    assert first.drivers_info[1]["name_acronym"] == "VER"


# --------------------------------------------------------------------------
# Retirements and resets reach the pipeline
# --------------------------------------------------------------------------

def test_a_retired_car_leaves_the_running_order(tmp_path):
    stops_at = LAP_S
    rows = race_rows.endpoints(
        position=race_rows.position_rows(until_s={16: stops_at}),
        intervals=race_rows.interval_rows(until_s={16: stops_at}),
        session_result=[],
    )
    replay = RaceReplay(race_rows.make_archive(tmp_path, rows))
    pipeline = play(RacePipeline(ReplayClock(replay.start)), replay)

    assert 16 not in {s.driver_number for s in pipeline.current_states()}
    assert pipeline.history(16) == []
    assert {s.driver_number for s in pipeline.current_states()} == {1, 44}


def test_a_reset_tick_clears_what_came_before():
    pipeline = play(RacePipeline(ReplayClock(excerpt_replay().start)), excerpt_replay(), 0, 100)
    assert pipeline.current_states()

    pipeline.ingest(TickData(reset=True))

    assert pipeline.current_states() == []
    assert pipeline.latest_battles() == []


# --------------------------------------------------------------------------
# Track status (B4)
# --------------------------------------------------------------------------

def _excerpt_with_safety_car(tmp_path, deployed, ended):
    """The real excerpt, with a safety car period written into race control."""
    archive = excerpt_store().load(9928)
    rows = {endpoint: list(archive.rows(endpoint)) for endpoint in archive.manifest.endpoints}
    rows["race_control"] += [
        {"date": deployed.isoformat(), "category": "SafetyCar", "flag": None,
         "scope": "Track", "message": "SAFETY CAR DEPLOYED"},
        {"date": ended.isoformat(), "category": "Flag", "flag": "GREEN",
         "scope": "Track", "message": "TRACK CLEAR"},
    ]
    archive.manifest.endpoints = {}
    store = SessionStore(tmp_path)
    store.save(archive.manifest, rows)
    return RaceReplay(store.load(9928))


def test_no_battle_is_shown_under_a_safety_car_and_they_come_back_after(tmp_path):
    base = excerpt_replay()
    deployed, ended = base.tick_time(150), base.tick_time(250)
    replay = _excerpt_with_safety_car(tmp_path, deployed, ended)

    pipeline = RacePipeline(ReplayClock(replay.start))
    before = during = after = 0
    for moment, tick in replay.ticks():
        pipeline.clock.set(moment)
        pipeline.ingest(tick)
        shown = [b for b in pipeline.detect() if b.intensity in ("HOT", "WATCH")]
        if moment < deployed:
            before += len(shown)
        elif moment < ended:
            assert tick.track_status == "sc"
            during += len(shown)
        else:
            after += len(shown)

    assert before > 0, "the excerpt should have battles before the safety car"
    assert during == 0
    assert after > 0, "battles must recover after the restart"


# --------------------------------------------------------------------------
# Lap counter and race length (fixes B7)
# --------------------------------------------------------------------------

def test_the_lap_counter_is_the_leaders_lap():
    replay = excerpt_replay()
    first_lap_done = min(
        e.at for e in replay.timeline.events if e.kind == "lap" and e.lap_number == 1
    )

    assert replay.lap_at(replay.start) == 1
    assert replay.lap_at(first_lap_done) == 2
    assert replay.lap_at(replay.timeline.end) == replay.total_laps


def test_total_laps_comes_from_the_classification():
    """session_result's winner, not however far the cached rows happen to go."""
    replay = RaceReplay(SessionStore().load(9928))

    assert replay.total_laps == 70


# --------------------------------------------------------------------------
# ReplaySource
# --------------------------------------------------------------------------

class FakeWall:
    """A wall clock the test moves, with a sleep that moves it."""

    def __init__(self, now):
        self.now = now
        self.slept = []

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += timedelta(seconds=seconds)


async def take(source, count):
    out, ticks = [], source.ticks()
    try:
        for _ in range(count):
            out.append(await ticks.__anext__())
    finally:
        await ticks.aclose()
    return out


def broadcast_of_excerpt(speed=1.0):
    replay = excerpt_replay()
    return Broadcast([replay], speed=speed, epoch=replay.start)


def test_replay_source_satisfies_the_protocol():
    source = ReplaySource(ReplayClock(excerpt_replay().start), broadcast=broadcast_of_excerpt())

    assert isinstance(source, DataSource)


def test_build_source_knows_replay():
    assert isinstance(build_source("replay", ReplayClock(excerpt_replay().start), None), ReplaySource)


def test_joining_mid_race_seeks_from_lights_out():
    """
    The container starts wherever the broadcast is, so its first pass is a
    seek: every tick from 0, fast, the first one a reset.
    """
    broadcast = broadcast_of_excerpt()
    wall = FakeWall(broadcast.epoch + timedelta(seconds=30 * 1.5 + 0.1))
    clock = ReplayClock(broadcast.epoch)
    source = ReplaySource(clock, broadcast=broadcast, health=_NoHealth(), sleep=wall.sleep, wall=wall)

    ticks = asyncio.run(take(source, 31))

    assert ticks[0].reset and not any(t.reset for t in ticks[1:])
    assert clock.now() == broadcast.replay(0).tick_time(30)
    assert wall.slept == [], "a seek must not wait between ticks"


def test_in_steady_state_one_tick_per_tick_of_wall_time():
    broadcast = broadcast_of_excerpt(speed=10)
    wall = FakeWall(broadcast.epoch)
    clock = ReplayClock(broadcast.epoch)
    source = ReplaySource(clock, broadcast=broadcast, health=_NoHealth(), sleep=wall.sleep, wall=wall)

    asyncio.run(take(source, 6))

    replay = broadcast.replay(0)
    assert clock.now() == replay.tick_time(5)
    assert wall.slept == [pytest.approx(replay.tick_s / 10)] * 5


def test_the_race_starts_over_with_a_reset():
    """One race looping: the second playing must not inherit the first's battles."""
    broadcast = broadcast_of_excerpt(speed=20)
    end = broadcast.epoch + timedelta(seconds=broadcast.duration_s - 0.01)
    wall = FakeWall(end)
    source = ReplaySource(ReplayClock(broadcast.epoch), broadcast=broadcast,
                          health=_NoHealth(), sleep=wall.sleep, wall=wall)

    # The first pass seeks to (about) the last tick; a few more passes cross
    # the end of the playlist.
    ticks = asyncio.run(take(source, broadcast.replay(0).tick_count + 3))

    assert ticks[0].reset
    assert any(t.reset for t in ticks[1:]), "the next pass through the playlist starts clean"


def test_the_container_path_shows_what_the_stateless_path_shows():
    """
    Both follow the same schedule: a container that has run since lights out
    and a function that rebuilt a window just now report the same battles.
    """
    broadcast = broadcast_of_excerpt()
    target = broadcast.epoch + timedelta(seconds=180 * 1.5 + 0.2)
    wall = FakeWall(target)
    pipeline = RacePipeline(ReplayClock(broadcast.epoch))
    source = ReplaySource(pipeline.clock, broadcast=broadcast, health=_NoHealth(),
                          sleep=wall.sleep, wall=wall)

    for tick in asyncio.run(take(source, 181)):
        pipeline.ingest(tick)
        pipeline.detect()

    rebuilt = broadcast.build_pipeline(target)
    assert pipeline.detected_at == rebuilt.detected_at
    assert _without_duration(pipeline.latest_battles()) == _without_duration(rebuilt.latest_battles())


def _without_duration(battles):
    return [b.model_dump(exclude={"duration_updates"}) for b in battles]


class _NoHealth:
    active_session = False

    def record_successful_position_poll(self):
        pass


# --------------------------------------------------------------------------
# What the cards show (1.6)
# --------------------------------------------------------------------------

def _state_of(driver, until_tick):
    replay = excerpt_replay()
    pipeline = play(RacePipeline(ReplayClock(replay.start)), replay, 0, until_tick)
    return pipeline.state.get_current_state(driver)


def test_tyres_come_from_the_stints():
    """Car 27 starts on softs and pits at the end of lap 5 for mediums."""
    replay = excerpt_replay()
    pit_at = parse_openf1_timestamp(excerpt_store().load(9928).rows("pit")[0]["date"])
    before = replay.tick_at(pit_at) - 30
    after = replay.tick_count - 1

    early = _state_of(27, before)
    late = _state_of(27, after)

    assert early.tire_compound == "SOFT"
    assert early.pit_stops_count == 0
    assert late.tire_compound == "MEDIUM"
    assert late.pit_stops_count == 1
    # Fitted new at the start of lap 6, so its age is the laps run since.
    completed = replay.cursor().tick(after).laps[27][-1]["lap_number"]
    assert late.tire_age_laps == completed + 1 - 6


def test_the_card_knows_the_cars_acronym_and_colour():
    state = _state_of(1, 50)

    assert state.name_acronym == "VER"
    assert state.team_colour == "4781D7"


def test_the_season_reaches_the_wording():
    """2025 has DRS; the explanation must say so."""
    replay = excerpt_replay()
    pipeline = play(RacePipeline(ReplayClock(replay.start)), replay)
    sub_second = [b for b in pipeline.detector.latest_battles if b.gap_now_s < 1.0]

    assert pipeline._season == 2025
    for battle in sub_second:
        assert "Within DRS range" in battle.explanation
