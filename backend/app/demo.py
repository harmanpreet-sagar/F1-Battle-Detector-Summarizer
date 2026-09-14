"""
Mock mode without a background loop, for serverless hosting.

The normal mock path is a process that lives forever: MockSource yields a tick
every POLL_POSITIONS_INTERVAL_S and the pipeline accumulates driver history and
tracked battles in memory between polls. A serverless platform freezes the
process the moment a response is returned, so that loop runs for a few hundred
milliseconds per request and then stops. Every endpoint would report "no driver
data available", forever.

What makes the way out available is that the mock generator is a pure function
of its tick counter: tick N always produces the same grid. So the state the
long-running loop would have built up by now can be reconstructed on demand.
Each request works out which tick the wall clock is on, replays the preceding
DEMO_WINDOW_TICKS ticks into a throwaway RacePipeline on a ReplayClock, and
serves that. Detection, the stability filter, closing rate and eviction all run
exactly as they do live - they just run over a window that was rebuilt a
millisecond ago rather than one that was accumulated over the last minute.

Two consequences worth knowing:

- Cost is per request, not per second. A replay of 48 ticks is a few
  milliseconds; see tests/test_demo.py, which pins the ceiling.
- The race repeats. Tick numbering is absolute and unbounded, but the generator
  wraps its scripted behaviour every LOOP_TICKS, so the demo restarts the same
  30-lap race roughly every seven and a half minutes instead of degrading into
  a frozen grid.
"""
from datetime import datetime, timedelta, timezone

from app.clock import ReplayClock, ensure_utc
from app.config import config
from app.mock_data import MockDataGenerator
from app.models import SessionStatus
from app.pipeline import RacePipeline, TickData

# Tick 0 of the demo race. Any fixed instant works; what matters is that every
# request and every instance of the function agrees on it, so two concurrent
# viewers see the same lap of the same race rather than two private ones.
RACE_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


def tick_at(now: datetime) -> int:
    """Which tick of the demo race the given instant falls in."""
    elapsed = (ensure_utc(now) - RACE_EPOCH).total_seconds()
    return int(elapsed // config.POLL_POSITIONS_INTERVAL_S)


def tick_time(tick: int) -> datetime:
    """When a tick arrives. The inverse of tick_at, to within one interval."""
    return RACE_EPOCH + timedelta(seconds=tick * config.POLL_POSITIONS_INTERVAL_S)


def build_pipeline(now: datetime) -> RacePipeline:
    """
    A pipeline holding the mock race as of `now`, built from scratch.

    The window is the last DEMO_WINDOW_TICKS ticks rather than the whole race so
    far: everything downstream reads a bounded slice of history anyway
    (HISTORY_MAX_LEN caps the deques, the trend windows read six samples), and a
    window keeps per-request cost flat instead of growing through the race.

    The only visible difference from a long-running server is duration_updates,
    which counts distinct gap samples inside the window rather than since the
    battle began.
    """
    current = tick_at(now)
    first = max(1, current - config.DEMO_WINDOW_TICKS + 1)

    # Its own generator, not the module-level singleton: two requests may be in
    # flight at once, and a shared tick counter would have them fighting over it.
    generator = MockDataGenerator()
    generator.started_at = RACE_EPOCH

    clock = ReplayClock(tick_time(first))
    pipeline = RacePipeline(clock=clock, name="demo")

    for tick in range(first, current + 1):
        # generate_driver_states increments before reading, so seed one below.
        generator.tick = tick - 1
        at = tick_time(tick)
        clock.set(at)
        pipeline.ingest(
            TickData(
                states=generator.generate_driver_states(now=at),
                track_status="green",
            )
        )
        pipeline.detect()

    return pipeline


def build_session(now: datetime) -> SessionStatus:
    """The mock session, with the lap number the race has actually reached."""
    generator = MockDataGenerator()
    generator.tick = tick_at(now)
    return generator.generate_mock_session()
