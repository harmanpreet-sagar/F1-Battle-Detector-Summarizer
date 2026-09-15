"""
Time injection.

Every part of the pipeline that needs to know "what time is it" asks a Clock
instead of calling datetime.now(). That indirection is what makes replay
possible: under replay the answer is race time, which may be a year in the past
and may advance far faster or slower than the wall.

Two rules hold everywhere downstream of this module:

1. Nothing in the pipeline calls datetime.now(). Wall time is only ever read
   here and in health.py, which reports on the server rather than on the race.
2. Every datetime is timezone-aware UTC. OpenF1 timestamps carry an offset and
   race time is UTC; a naive datetime mixed with either raises TypeError at the
   first subtraction, which under replay would surface as a crash mid-session
   rather than at import.
"""
from datetime import datetime, timedelta, timezone
from typing import Protocol, runtime_checkable


def utcnow() -> datetime:
    """Wall-clock now, timezone-aware UTC."""
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime) -> datetime:
    """
    Coerce a datetime to timezone-aware UTC.

    A naive value is assumed to already be UTC rather than local: every naive
    datetime reaching this system comes from an OpenF1 timestamp that omitted
    its offset, and those are UTC. Guessing local instead would shift race time
    by the host's offset, which is exactly the class of bug this module exists
    to remove.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@runtime_checkable
class Clock(Protocol):
    """Anything that can answer "what time is it" in timezone-aware UTC."""

    def now(self) -> datetime:
        ...


class WallClock:
    """Real time. Used by live mode and by anything reporting on the server."""

    def now(self) -> datetime:
        return utcnow()


class ReplayClock:
    """
    Race time, moved explicitly by whoever is driving the replay.

    The clock does not advance on its own. A replay source sets it to the race
    time of the tick it is about to hand over, so detection, eviction and
    confidence scoring all see the same instant the data came from - at 10x
    speed or in a batch run that processes a whole race in seconds.
    """

    def __init__(self, start: datetime):
        self._now = ensure_utc(start)

    def now(self) -> datetime:
        return self._now

    def set(self, value: datetime) -> None:
        """Jump to an absolute race time (used by seek)."""
        self._now = ensure_utc(value)

    def advance(self, delta: timedelta) -> None:
        """Move forward (or back, with a negative delta) by a duration."""
        self._now = self._now + delta
