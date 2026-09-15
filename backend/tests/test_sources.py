"""
Tests for DATA_MODE resolution and the data sources.

These never touch the network: LiveSource is driven by a stub client, and the
point of DATA_MODE defaulting to mock is that nothing reaches OpenF1 by accident.
"""
from datetime import datetime, timezone
import asyncio

import pytest

from app.clock import ReplayClock, WallClock
from app.config import DATA_MODES, resolve_data_mode
from app.models import SessionStatus
from app.pipeline import RacePipeline, TickData
from app.sources import LiveSource, MockSource, build_source
from app.sources.base import DataSource

RACE_TIME = datetime(2025, 9, 7, 13, 5, 0, tzinfo=timezone.utc)


class StubSessions:
    """Stands in for SessionManager."""

    def __init__(self, session=None):
        self._session = session

    def get_current_session(self):
        return self._session

    def is_session_active(self):
        return self._session is not None


def a_session(track_status="green"):
    return SessionStatus(
        session_key=9999, session_name="Race", session_type="Race",
        session_status="started", circuit_short_name="Monza",
        meeting_name="Italian Grand Prix", track_status=track_status,
        gmt_offset="+00:00", updated_at=RACE_TIME,
    )


class StubClient:
    """Returns one fixed poll of OpenF1 rows, and counts calls."""

    def __init__(self):
        self.calls = 0

    async def get_latest_positions(self, session_key):
        self.calls += 1
        stamp = RACE_TIME.isoformat()
        return [
            {"driver_number": 1, "position": 1, "date": stamp},
            {"driver_number": 44, "position": 2, "date": stamp},
        ]

    async def get_latest_intervals(self, session_key):
        stamp = RACE_TIME.isoformat()
        return [
            {"driver_number": 1, "interval": None, "gap_to_leader": 0, "date": stamp},
            {"driver_number": 44, "interval": 0.4, "gap_to_leader": 0.4, "date": stamp},
        ]

    async def get_drivers(self, session_key):
        return [
            {"driver_number": 1, "full_name": "M. Verstappen", "team_name": "Red Bull Racing"},
            {"driver_number": 44, "full_name": "L. Hamilton", "team_name": "Ferrari"},
        ]

    async def get_latest_laps(self, session_key, count):
        return {1: [{"lap_duration": 90.5}], 44: [{"lap_duration": 90.0}]}


class StubHealth:
    def __init__(self):
        self.positions = 0
        self.laps = 0
        self.errors = 0
        self.active_session = False

    def record_successful_position_poll(self):
        self.positions += 1

    def record_successful_lap_poll(self):
        self.laps += 1

    def record_error(self):
        self.errors += 1


async def first_ticks(source, count):
    """Take the first `count` ticks, then stop. Sources run forever."""
    out = []
    ticks = source.ticks()
    try:
        for _ in range(count):
            out.append(await ticks.__anext__())
    finally:
        await ticks.aclose()
    return out


# --------------------------------------------------------------------------
# DATA_MODE resolution
# --------------------------------------------------------------------------

def test_data_mode_defaults_to_mock(monkeypatch):
    """Nothing set means no network. Live now needs a paid token."""
    monkeypatch.delenv("DATA_MODE", raising=False)
    monkeypatch.delenv("TEST_MODE", raising=False)

    assert resolve_data_mode() == "mock"


@pytest.mark.parametrize("mode", DATA_MODES)
def test_explicit_data_mode_wins(monkeypatch, mode):
    monkeypatch.setenv("DATA_MODE", mode.upper())
    monkeypatch.setenv("TEST_MODE", "true")

    assert resolve_data_mode() == mode


def test_unknown_data_mode_fails_loudly(monkeypatch):
    monkeypatch.setenv("DATA_MODE", "banana")

    with pytest.raises(ValueError, match="banana"):
        resolve_data_mode()


def test_legacy_test_mode_true_maps_to_mock(monkeypatch):
    monkeypatch.delenv("DATA_MODE", raising=False)
    monkeypatch.setenv("TEST_MODE", "true")

    with pytest.warns(DeprecationWarning):
        assert resolve_data_mode() == "mock"


def test_legacy_test_mode_false_still_means_live(monkeypatch):
    """
    An existing deployment that set TEST_MODE=false asked for real data. It
    must not silently drop to mock just because the default changed.
    """
    monkeypatch.delenv("DATA_MODE", raising=False)
    monkeypatch.setenv("TEST_MODE", "false")

    with pytest.warns(DeprecationWarning):
        assert resolve_data_mode() == "live"


# --------------------------------------------------------------------------
# build_source
# --------------------------------------------------------------------------

def test_build_source_returns_the_right_source():
    sessions = StubSessions()

    assert isinstance(build_source("mock", WallClock(), sessions), MockSource)
    assert isinstance(build_source("live", WallClock(), sessions), LiveSource)


def test_replay_mode_is_not_available_yet():
    with pytest.raises(NotImplementedError, match="Phase 1"):
        build_source("replay", WallClock(), StubSessions())


def test_sources_satisfy_the_protocol():
    sessions = StubSessions()

    assert isinstance(MockSource(WallClock(), sessions), DataSource)
    assert isinstance(LiveSource(WallClock(), sessions), DataSource)


# --------------------------------------------------------------------------
# MockSource
# --------------------------------------------------------------------------

def test_mock_source_yields_states_and_track_status():
    health = StubHealth()
    source = MockSource(ReplayClock(RACE_TIME), StubSessions(a_session("green")), health=health)

    ticks = asyncio.run(first_ticks(source, 2))

    assert len(ticks) == 2
    assert all(t.states and len(t.states) == 20 for t in ticks)
    assert all(t.track_status == "green" for t in ticks)
    assert health.positions == 2
    assert health.active_session is True


def test_mock_source_survives_having_no_session():
    """The session poll may not have run yet on the first tick."""
    source = MockSource(ReplayClock(RACE_TIME), StubSessions(None), health=StubHealth())

    tick, = asyncio.run(first_ticks(source, 1))

    assert tick.track_status is None
    assert tick.states


# --------------------------------------------------------------------------
# LiveSource
# --------------------------------------------------------------------------

def test_live_source_yields_raw_rows_from_the_client():
    client, health = StubClient(), StubHealth()
    source = LiveSource(
        ReplayClock(RACE_TIME), StubSessions(a_session("green")),
        client=client, health=health,
    )

    tick, = asyncio.run(first_ticks(source, 1))

    assert [row["driver_number"] for row in tick.positions] == [1, 44]
    assert tick.drivers_info[44]["team_name"] == "Ferrari"
    assert tick.laps[44] == [{"lap_duration": 90.0}]
    assert tick.track_status == "green"
    assert health.positions == 1
    assert health.laps == 1


def test_live_source_ticks_feed_the_pipeline():
    """End to end through the runner loop's two lines, without the network."""
    clock = ReplayClock(RACE_TIME)
    pipeline = RacePipeline(clock=clock, name="live")
    source = LiveSource(
        clock, StubSessions(a_session("green")),
        client=StubClient(), health=StubHealth(),
    )

    for tick in asyncio.run(first_ticks(source, 1)):
        pipeline.ingest(tick)
        pipeline.detect()

    chaser = pipeline.state.get_current_state(44)
    assert chaser.gap_to_ahead_s == 0.4
    # Scored at race time, so a 2025 row is fresh rather than a year stale
    assert chaser.data_confidence == "high"


def test_live_source_waits_when_no_session_is_active():
    """
    With no session there is nothing to poll, and crucially no request is made.
    """
    client = StubClient()
    source = LiveSource(
        ReplayClock(RACE_TIME), StubSessions(None), client=client, health=StubHealth()
    )

    async def take_nothing():
        ticks = source.ticks()
        try:
            await asyncio.wait_for(ticks.__anext__(), timeout=0.25)
        except asyncio.TimeoutError:
            return "waited"
        finally:
            await ticks.aclose()
        return "yielded"

    assert asyncio.run(take_nothing()) == "waited"
    assert client.calls == 0
