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
from tests.replay_fixtures import excerpt_replay, excerpt_with_safety_car


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
    # Exactly on a boundary is the start of the next race, not the end of this one.
    assert where(one) == (0, 2)
    assert broadcast.locate(broadcast.epoch + timedelta(seconds=one)).tick == 0
    assert where(one + two) == (1, 1)
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


def compare_rebuilds(broadcast, every=10):
    """
    Rebuild at every `every`th tick and compare with a pipeline that has run
    continuously since lights out. The single permitted difference is
    `duration_updates`, which counts distinct gap samples inside the window
    rather than since the battle began - so the rebuild can only ever count
    fewer. Returns how many ticks were compared, and which of them had battles.
    """
    replay = broadcast.replay(0)
    continuous = RacePipeline(ReplayClock(replay.start))
    cursor = replay.cursor()

    compared, with_battles = 0, []
    for tick in range(replay.tick_count):
        continuous.clock.set(replay.tick_time(tick))
        continuous.ingest(cursor.tick(tick, reset=(tick == 0)))
        continuous.detect()
        if tick % every:
            continue

        rebuilt = broadcast.build_pipeline(moment_of(broadcast, tick))
        ours, theirs = rebuilt.latest_battles(), continuous.latest_battles()

        assert rebuilt.detected_at == continuous.detected_at
        assert [b.model_dump(exclude={"duration_updates"}) for b in ours] == \
            [b.model_dump(exclude={"duration_updates"}) for b in theirs], f"tick {tick}"
        assert all(a.duration_updates <= b.duration_updates for a, b in zip(ours, theirs))
        compared += 1
        if ours:
            with_battles.append(tick)

    return compared, with_battles


def test_a_rebuilt_pipeline_matches_one_that_ran_from_lights_out():
    """The equivalence the serverless path depends on, across the excerpt."""
    compared, with_battles = compare_rebuilds(excerpt_broadcast())

    assert compared > 20
    assert len(with_battles) >= 5, "the comparison is vacuous without battles in it"


def test_a_rebuilt_pipeline_matches_through_a_safety_car(tmp_path):
    """
    A restart is where a window could go wrong: the battles that come back
    are rebuilt from a window that is partly neutralised.
    """
    base = excerpt_replay()
    replay = excerpt_with_safety_car(tmp_path, base.tick_time(150), base.tick_time(250))
    broadcast = Broadcast([replay], speed=1, epoch=replay.start)

    compared, with_battles = compare_rebuilds(broadcast)

    assert compared > 20
    assert any(tick > 250 for tick in with_battles), "nothing compared after the restart"


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

    battles = client.get("/api/battles/top?k=5&min_intensity=WATCH").json()
    position = stateless_replay.locate(moment)
    # Detection ran at the race time of the current tick, not at the wall.
    assert battles["detected_at"] == stateless_replay.replay(0).tick_time(position.tick).isoformat()
    assert battles["battles"], "no battles served at a moment the excerpt has them"
    assert battles["battles"][0]["chaser_name"]

    session = client.get("/api/session/current").json()
    assert session["session_key"] == 9928
    assert 1 <= session["current_lap"] <= session["total_laps"]

    assert client.get("/api/state/latest").json()["count"] == 20


REPLAY_STATUS_FIELDS = {
    "mode", "session_key", "meeting_name", "circuit", "year", "race_time", "elapsed_s", "lap",
    "total_laps", "track_status", "stoppage", "speed", "progress_pct", "next_race",
}


def test_replay_status_says_where_the_broadcast_is(stateless_replay, monkeypatch):
    from fastapi.testclient import TestClient
    from app import main

    moment = moment_of(stateless_replay, 300)
    monkeypatch.setattr(main, "utcnow", lambda: moment)

    status = TestClient(main.app).get("/api/replay/status").json()
    position = stateless_replay.locate(moment)
    replay = stateless_replay.replay(0)

    assert set(status) == REPLAY_STATUS_FIELDS
    assert status["mode"] == "replay"
    assert status["session_key"] == 9928
    assert status["meeting_name"] == "Hungarian Grand Prix"
    assert status["circuit"] == "Hungaroring"
    assert status["year"] == 2025
    assert status["race_time"] == position.race_time.isoformat()
    assert status["lap"] == replay.lap_at(position.race_time)
    assert status["total_laps"] == replay.total_laps
    assert status["track_status"] == "green"
    assert status["speed"] == 1
    assert 0 < status["progress_pct"] < 100
    # One race in the playlist, so the next race is the same one again.
    assert status["next_race"]["session_key"] == 9928


def test_the_status_and_the_session_agree(stateless_replay, monkeypatch):
    from fastapi.testclient import TestClient
    from app import main

    monkeypatch.setattr(main, "utcnow", lambda: moment_of(stateless_replay, 300))
    client = TestClient(main.app)

    status = client.get("/api/replay/status").json()
    session = client.get("/api/session/current").json()

    assert session["current_lap"] == status["lap"]
    assert session["total_laps"] == status["total_laps"]
    assert session["track_status"] == status["track_status"]


def test_replay_sessions_lists_the_cache(stateless_replay, monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    from tests.replay_fixtures import EXCERPT_ROOT

    monkeypatch.setattr(config, "REPLAY_DATA_DIR", str(EXCERPT_ROOT))
    sessions = TestClient(main.app).get("/api/replay/sessions").json()["sessions"]

    race, = sessions
    assert race["session_key"] == 9928
    assert race["circuit"] == "Hungaroring"
    assert race["round"] == 14
    assert race["rows"] > 0 and race["bytes"] > 0
    assert race["in_playlist"] is True


def test_health_reports_the_replay_position(stateless_replay, monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    from app.replay.store import SCHEMA_VERSION

    moment = moment_of(stateless_replay, 300)
    monkeypatch.setattr(main, "utcnow", lambda: moment)

    health = TestClient(main.app).get("/api/health").json()

    assert health["status"] == "healthy"
    assert health["mode"] == "replay"
    assert health["replay"]["session_key"] == 9928
    assert health["replay"]["race_time"] == stateless_replay.locate(moment).race_time.isoformat()
    assert health["replay"]["schema_version"] == SCHEMA_VERSION
    assert health["replay"]["manifest_schema_version"] == SCHEMA_VERSION


def test_battle_cards_name_both_cars_and_their_tyres(stateless_replay, monkeypatch):
    from fastapi.testclient import TestClient
    from app import main

    monkeypatch.setattr(main, "utcnow", lambda: moment_of(stateless_replay, 200))
    battles = TestClient(main.app).get("/api/battles/top?k=5").json()["battles"]

    assert battles
    for battle in battles:
        for side in ("chaser", "ahead"):
            assert len(battle[f"{side}_acronym"]) == 3
            assert len(battle[f"{side}_team_colour"]) == 6
            assert battle[f"{side}_tyre"]["compound"] in ("SOFT", "MEDIUM", "HARD")
            assert battle[f"{side}_tyre"]["age_laps"] >= 0


# --------------------------------------------------------------------------
# What ships (1.7)
# --------------------------------------------------------------------------

def test_every_shipped_race_is_replayable():
    """
    The races under data/sessions are the deployed playlist. One that fails to
    load or index would take the whole broadcast down on the first request.
    """
    from app.replay.store import SCHEMA_VERSION

    manifests = SessionStore().manifests()
    assert len(manifests) >= 5, "the playlist should have a handful of races"

    for manifest in manifests:
        replay = RaceReplay(SessionStore().load(manifest.session_key))
        assert manifest.schema_version == SCHEMA_VERSION
        assert manifest.meeting_name, f"{manifest.session_key} has no meeting name"
        assert replay.total_laps and replay.total_laps > 40, manifest.session_key
        assert len(replay.timeline.drivers) >= 18, manifest.session_key
        # A neutralised track for most of a race is the steward-notice bug.
        neutral = sum(
            (b - a).total_seconds()
            for a, b in zip(replay._status_times, replay._status_times[1:] + [replay.timeline.end])
            if replay.track_status_at(a) in ("sc", "vsc", "red")
        )
        assert neutral < 0.5 * replay.timeline.duration.total_seconds(), manifest.session_key


def test_no_shipped_race_shows_a_battle_under_a_neutralised_track():
    """
    The Phase 1 acceptance check, on the races that ship rather than on a
    safety car written into a fixture: every tick of every race through the
    detector. The slowest test in the suite (~15s).

    It can only check the periods race control's messages were read as
    neutralised. A safety car the mapping misses reads as green and passes
    here, which is what the Belgian GP test below is for.
    """
    restarts = recovered = 0
    for manifest in SessionStore().manifests():
        replay = RaceReplay(SessionStore().load(manifest.session_key))
        pipeline = RacePipeline(ReplayClock(replay.start))
        restarted_at = None
        neutralised = False
        for moment, tick in replay.ticks():
            pipeline.clock.set(moment)
            pipeline.ingest(tick)
            shown = [b for b in pipeline.detect() if b.intensity in ("HOT", "WATCH")]
            if tick.track_status in ("sc", "vsc", "red"):
                assert not shown, f"{manifest.meeting_name} at {moment}: {shown[0].battle_id}"
                neutralised, restarted_at = True, None
                continue
            if neutralised:
                neutralised, restarted_at = False, moment
                restarts += 1
            if restarted_at and shown and moment - restarted_at < timedelta(minutes=5):
                recovered += 1
                restarted_at = None

    assert restarts >= 5, "the shipped races should include safety cars"
    # A restart a lap or two from the flag may never get going again.
    assert recovered >= restarts - 2, f"battles came back after {recovered} of {restarts} restarts"


def test_the_belgian_gp_starts_behind_the_safety_car():
    """
    The 2025 Belgian GP ran its first four laps behind the safety car and
    then had a rolling start. Race control has no SafetyCar message for any
    of it, and those laps were once shown as racing, battles and all.
    """
    replay = RaceReplay(SessionStore().load(9939))
    statuses = [e for e in replay.timeline.events if e.kind == "track_status"]

    assert statuses[0].status == "sc"
    released = statuses[1]
    assert released.status == "green"
    assert replay.lap_at(released.at) == 5


def test_the_shipped_playlist_loops():
    broadcast = Broadcast.from_store(SessionStore(), playlist=[])
    first = broadcast.locate(broadcast.epoch)
    later = broadcast.locate(broadcast.epoch + timedelta(seconds=broadcast.duration_s + 1))

    assert len(broadcast.slots) == len(SessionStore().manifests())
    assert later.cycle == first.cycle + 1
