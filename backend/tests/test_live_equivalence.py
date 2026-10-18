"""
The live-equivalence test (1.3): replay sees what live polling would have seen.

This is the strongest claim the project makes, so it is tested end to end
rather than piece by piece. Eight laps of a real race go down two paths:

- Path A: a fake OpenF1 serves the rows over HTTP to the real OpenF1Client,
  revealing each row only once its own timestamp has passed. LiveSource polls
  it exactly as it polls the real API, on a fake clock its sleeps advance.
- Path B: the same archive goes through the timeline and the replay engine.

At every tick the two pipelines must report identical battles.

What Path A is told rather than finds out is the track status. Live mode has
no race-control feed wired yet - it reads `session.track_status` - so the
fake session reports the status the timeline derived. That keeps this test
about the rows; the race-control mapping has tests of its own.
"""
from datetime import timedelta
from typing import Dict, List
import asyncio
import json

import httpx

from app.clock import ReplayClock
from app.models import SessionStatus
from app.openf1_client import OpenF1Client
from app.pipeline import RacePipeline
from app.sources import LiveSource
from app.state import parse_openf1_timestamp
from tests.replay_fixtures import excerpt_replay, excerpt_store


class FakeOpenF1:
    """Serves an archive as OpenF1 would have mid-race at `clock.now()`."""

    def __init__(self, archive, clock: ReplayClock):
        self.clock = clock
        self.requests = 0
        self._rows: Dict[str, List[Dict]] = {
            name: archive.rows(name) for name in ("position", "intervals", "laps", "drivers")
        }

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        endpoint = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, content=json.dumps(self._visible(endpoint)).encode())

    def _visible(self, endpoint: str) -> List[Dict]:
        now = self.clock.now()
        rows = self._rows[endpoint]
        if endpoint == "drivers":
            return rows
        if endpoint == "laps":
            out = []
            for row in rows:
                started = parse_openf1_timestamp(row.get("date_start"))
                if started is None or started > now:
                    continue
                if row.get("lap_duration") is not None and \
                        started + timedelta(seconds=row["lap_duration"]) > now:
                    # In progress: the row exists, its time does not yet.
                    row = dict(row, lap_duration=None)
                out.append(row)
            return out
        return [row for row in rows if parse_openf1_timestamp(row["date"]) <= now]


class FakeSessions:
    def __init__(self, replay, clock):
        self._replay = replay
        self._clock = clock

    def is_session_active(self):
        return True

    def get_current_session(self):
        return SessionStatus(
            session_key=self._replay.session_key, session_name="Race", session_type="Race",
            session_status="started", circuit_short_name="Hungaroring",
            meeting_name="Hungarian Grand Prix", year=2025,
            track_status=self._replay.track_status_at(self._clock.now()),
            gmt_offset="+00:00", updated_at=self._clock.now(),
        )


class _NoHealth:
    active_session = True

    def record_successful_position_poll(self):
        pass

    def record_successful_lap_poll(self):
        pass

    def record_error(self):
        raise AssertionError("the fake OpenF1 never fails")


def run_live(replay, archive, ticks: int):
    clock = ReplayClock(replay.start)
    server = FakeOpenF1(archive, clock)
    client = OpenF1Client()
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(server.handle))

    async def fake_sleep(seconds):
        clock.advance(timedelta(seconds=seconds))

    source = LiveSource(clock, FakeSessions(replay, clock), client=client,
                        health=_NoHealth(), sleep=fake_sleep)
    pipeline = RacePipeline(clock, name="live")

    async def drive():
        out = []
        stream = source.ticks()
        try:
            for _ in range(ticks):
                tick = await stream.__anext__()
                pipeline.ingest(tick)
                out.append((clock.now(), pipeline.detect()))
        finally:
            await stream.aclose()
            await client.close()
        return out

    return asyncio.run(drive()), server


def run_replay(replay, ticks: int):
    pipeline = RacePipeline(ReplayClock(replay.start), name="replay")
    out = []
    for moment, tick in replay.ticks(0, ticks - 1):
        pipeline.clock.set(moment)
        pipeline.ingest(tick)
        out.append((moment, pipeline.detect()))
    return out


def test_replay_detects_exactly_what_live_polling_would_have():
    replay = excerpt_replay()
    archive = excerpt_store().load(replay.session_key)
    ticks = replay.tick_count

    live, server = run_live(replay, archive, ticks)
    replayed = run_replay(replay, ticks)

    assert server.requests >= 3 * ticks, "Path A must really go through HTTP"
    assert [moment for moment, _ in live] == [moment for moment, _ in replayed]

    shown = 0
    for (moment, a), (_, b) in zip(live, replayed):
        assert [x.model_dump() for x in a] == [x.model_dump() for x in b], f"diverged at {moment}"
        shown += len(a)

    assert shown > 0, "an excerpt with no battles proves nothing"


def test_the_excerpt_exercises_what_it_should():
    """A pit stop and position changes, or the equivalence above is cheap."""
    replay = excerpt_replay()
    kinds = {event.kind for event in replay.timeline.events}
    swaps = sum(1 for event in replay.timeline.events if event.kind == "position")

    assert "pit" in kinds
    assert swaps > 20
