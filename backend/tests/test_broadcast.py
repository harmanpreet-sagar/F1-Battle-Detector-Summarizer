"""
Tests for the broadcast schedule and the stateless rebuild (1.4).

The schedule is arithmetic on the wall clock, so these mostly pin that the
arithmetic lands where it should: across race boundaries, at speed, and
through a compressed stoppage. The rest is the equivalence claim the
serverless path depends on - a pipeline rebuilt from the last
REPLAY_WINDOW_TICKS ticks shows what one that ran from lights out shows.
"""
from datetime import timedelta
import time

import pytest

from app.clock import ReplayClock
from app.config import config
from app.pipeline import RacePipeline
from app.replay import broadcast as broadcast_module
from app.replay.broadcast import Broadcast, playback_segments
from app.replay.engine import RaceReplay
from app.replay.store import SessionStore
from tests import race_rows
from tests.race_rows import at
from tests.replay_fixtures import excerpt_replay


def synthetic(tmp_path, session_key, rows=None):
    """The four-lap synthetic race, cached under its own session key."""
    store = SessionStore(tmp_path)
    store.save(race_rows.manifest(session_key=session_key),
               rows if rows is not None else race_rows.endpoints())
    return RaceReplay(store.load(session_key))


# --------------------------------------------------------------------------
# locate()
# --------------------------------------------------------------------------

def test_the_epoch_is_the_start_of_the_first_race(tmp_path):
    replay = synthetic(tmp_path, 1)
    broadcast = Broadcast([replay], speed=1, epoch=replay.start)

    position = broadcast.locate(broadcast.epoch)

    assert (position.index, position.cycle, position.tick) == (0, 0, 0)
    assert position.race_time == replay.start


def test_race_time_runs_at_the_configured_speed(tmp_path):
    replay = synthetic(tmp_path, 1)
    broadcast = Broadcast([replay], speed=10, epoch=replay.start)

    position = broadcast.locate(broadcast.epoch + timedelta(seconds=6))

    assert position.race_time == replay.start + timedelta(seconds=60)
    assert broadcast.duration_s == pytest.approx(replay.timeline.duration.total_seconds() / 10)


def test_the_playlist_moves_on_and_wraps(tmp_path):
    first, second = synthetic(tmp_path, 1), synthetic(tmp_path, 2)
    broadcast = Broadcast([first, second], speed=1, epoch=first.start)
    one = first.timeline.duration.total_seconds()
    two = second.timeline.duration.total_seconds()

    def where(seconds):
        position = broadcast.locate(broadcast.epoch + timedelta(seconds=seconds))
        return position.cycle, position.session_key

    assert where(one - 1) == (0, 1)
    assert where(one + 1) == (0, 2)
    assert where(one + two - 1) == (0, 2)
    assert where(one + two + 1) == (1, 1)
    # A whole number of loops later, the same moment of the same race.
    assert broadcast.locate(broadcast.epoch + timedelta(seconds=one + 10)).race_time == \
        broadcast.locate(broadcast.epoch + timedelta(seconds=3 * (one + two) + one + 10)).race_time


def test_before_the_epoch_is_still_somewhere(tmp_path):
    replay = synthetic(tmp_path, 1)
    broadcast = Broadcast([replay], speed=1, epoch=replay.start)

    position = broadcast.locate(broadcast.epoch - timedelta(seconds=30))

    assert position.cycle == -1
    assert replay.start <= position.race_time <= replay.timeline.end


def test_a_new_playing_of_a_race_is_a_new_run(tmp_path):
    replay = synthetic(tmp_path, 1)
    broadcast = Broadcast([replay], speed=1, epoch=replay.start)
    loop = broadcast.duration_s

    first = broadcast.locate(broadcast.epoch + timedelta(seconds=10))
    again = broadcast.locate(broadcast.epoch + timedelta(seconds=loop + 10))

    assert first.session_key == again.session_key
    assert first.run != again.run


def test_an_empty_playlist_is_an_error():
    with pytest.raises(ValueError, match="at least one"):
        Broadcast([])


def test_no_cached_races_says_how_to_get_one(tmp_path):
    with pytest.raises(FileNotFoundError, match="app.ingest"):
        Broadcast.from_store(SessionStore(tmp_path / "empty"), playlist=[])


# --------------------------------------------------------------------------
# Stoppages
# --------------------------------------------------------------------------

def _stopped_race(tmp_path):
    """The synthetic race with a 13-minute hole in the feed after two minutes."""
    first_s, last_s = 120.0, 900.0

    def outside(rows):
        return [r for r in rows
                if not at(first_s).isoformat() <= r["date"] <= at(last_s).isoformat()]

    rows = race_rows.endpoints(
        position=outside(race_rows.position_rows(laps=12)),
        intervals=outside(race_rows.interval_rows(laps=12)),
        laps=race_rows.all_lap_rows(laps=1),
        stints=[], pit=[], race_control=[], session_result=[],
    )
    return synthetic(tmp_path, 1, rows)


def test_a_stoppage_plays_back_in_a_few_seconds(tmp_path):
    replay = _stopped_race(tmp_path)
    (quiet_start, quiet_end), = replay.timeline.quiet_periods()

    segments = playback_segments(replay, speed=1, quiet_playback_s=5)

    quiet, = [s for s in segments if s.quiet]
    assert quiet.wall_end - quiet.wall_start == 5
    assert quiet.race_end - quiet.race_start == (quiet_end - quiet_start).total_seconds()
    total_race = replay.timeline.duration.total_seconds()
    assert segments[-1].wall_end == pytest.approx(total_race - (quiet.race_end - quiet.race_start) + 5)


def test_locate_reports_a_stoppage(tmp_path):
    replay = _stopped_race(tmp_path)
    broadcast = Broadcast([replay], speed=1, epoch=replay.start, quiet_playback_s=5)
    (quiet_start, _), = replay.timeline.quiet_periods()
    into_gap = (quiet_start - replay.start).total_seconds() + 2.5

    position = broadcast.locate(broadcast.epoch + timedelta(seconds=into_gap))

    assert position.stoppage
    assert position.race_time > quiet_start


def test_a_short_quiet_spell_at_high_speed_is_left_alone(tmp_path):
    """Compressing only makes sense if it would otherwise take longer."""
    replay = _stopped_race(tmp_path)

    segments = playback_segments(replay, speed=500, quiet_playback_s=5)

    assert not any(s.quiet for s in segments)


# --------------------------------------------------------------------------
# The stateless rebuild
# --------------------------------------------------------------------------

def excerpt_broadcast():
    replay = excerpt_replay()
    return Broadcast([replay], speed=1, epoch=replay.start)


def moment_of(broadcast, tick):
    return broadcast.epoch + timedelta(seconds=tick * broadcast.replay(0).tick_s + 0.3)


def test_the_window_holds_a_full_history():
    """The warm-up fills every driver's history before detection starts."""
    assert config.REPLAY_WINDOW_TICKS >= config.BATTLE_GAP_TREND_WINDOW * 3

    pipeline = excerpt_broadcast().build_pipeline(moment_of(excerpt_broadcast(), 200))

    assert len(pipeline.history(1)) == config.HISTORY_MAX_LEN


def test_a_rebuilt_pipeline_matches_one_that_ran_from_lights_out():
    """
    The equivalence the serverless path depends on, at every 10th tick of the
    excerpt. The single permitted difference is `duration_updates`, which
    counts distinct gap samples inside the window rather than since the
    battle began - so the rebuild can only ever count fewer.
    """
    broadcast = excerpt_broadcast()
    replay = broadcast.replay(0)
    continuous = RacePipeline(ReplayClock(replay.start))
    cursor = replay.cursor()

    compared = with_battles = 0
    for tick in range(replay.tick_count):
        continuous.clock.set(replay.tick_time(tick))
        continuous.ingest(cursor.tick(tick, reset=(tick == 0)))
        continuous.detect()
        if tick % 10:
            continue

        rebuilt = broadcast.build_pipeline(moment_of(broadcast, tick))
        ours, theirs = rebuilt.latest_battles(), continuous.latest_battles()

        assert rebuilt.detected_at == continuous.detected_at
        assert [b.model_dump(exclude={"duration_updates"}) for b in ours] == \
            [b.model_dump(exclude={"duration_updates"}) for b in theirs], f"tick {tick}"
        assert all(a.duration_updates <= b.duration_updates for a, b in zip(ours, theirs))
        compared += 1
        with_battles += bool(ours)

    assert compared > 20
    assert with_battles >= 5, "the comparison is vacuous without battles in it"


def test_everyone_watching_sees_the_same_race():
    broadcast = excerpt_broadcast()
    moment = moment_of(broadcast, 220)

    one = broadcast.build_pipeline(moment).latest_battles()
    two = broadcast.build_pipeline(moment).latest_battles()

    assert [b.model_dump() for b in one] == [b.model_dump() for b in two]


def test_the_start_of_a_race_rebuilds_what_little_there_is():
    broadcast = excerpt_broadcast()

    pipeline = broadcast.build_pipeline(broadcast.epoch)

    assert len(pipeline.current_states()) == 20
    assert pipeline.detected_at == broadcast.replay(0).start


def test_rebuilding_is_cheap_enough_to_do_per_request():
    """
    This runs on every request, so its cost is the hosting bill. ~35ms on a
    laptop; the ceiling is generous enough not to flake on a loaded CI box.
    """
    broadcast = excerpt_broadcast()
    start = time.perf_counter()
    for i in range(5):
        broadcast.build_pipeline(moment_of(broadcast, 250 + i))
    per_call = (time.perf_counter() - start) / 5

    assert per_call < 0.25, f"{per_call * 1000:.0f}ms per request"


# --------------------------------------------------------------------------
# The session it reports
# --------------------------------------------------------------------------

def test_the_session_is_the_race_being_shown():
    broadcast = excerpt_broadcast()
    replay = broadcast.replay(0)
    moment = moment_of(broadcast, 300)

    session = broadcast.session_at(moment)

    assert session.session_key == 9928
    assert session.circuit_short_name == "Hungaroring"
    assert session.current_lap == replay.lap_at(replay.tick_time(300))
    assert session.track_status == "green"
    assert session.updated_at == broadcast.locate(moment).race_time


# --------------------------------------------------------------------------
# Wiring
# --------------------------------------------------------------------------

@pytest.fixture
def stateless_replay(monkeypatch):
    """The app in DATA_MODE=replay on the serverless path, playing the excerpt."""
    broadcast = excerpt_broadcast()
    monkeypatch.setattr(config, "DATA_MODE", "replay")
    monkeypatch.setattr(config, "DEMO_STATELESS", True)
    monkeypatch.setattr(broadcast_module, "_broadcast", broadcast)
    return broadcast


def test_endpoints_serve_the_replay_without_a_poll_loop(stateless_replay, monkeypatch):
    from fastapi.testclient import TestClient
    from app import main

    moment = moment_of(stateless_replay, 200)
    monkeypatch.setattr(main, "utcnow", lambda: moment)
    client = TestClient(main.app)

    battles = client.get("/battles/top?k=5&min_intensity=WATCH").json()
    position = stateless_replay.locate(moment)
    # Detection ran at the race time of the current tick, not at the wall.
    assert battles["detected_at"] == stateless_replay.replay(0).tick_time(position.tick).isoformat()
    assert battles["battles"], "no battles served at a moment the excerpt has them"
    assert battles["battles"][0]["chaser_name"]

    session = client.get("/session/current").json()
    assert session["session_key"] == 9928
    assert 1 <= session["current_lap"] <= session["total_laps"]

    assert client.get("/state/latest").json()["count"] == 20
