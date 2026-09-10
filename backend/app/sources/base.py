"""
Where ticks come from.

A DataSource is an async iterator of TickData plus the clock those ticks are
timed against. That pairing is the whole point: a live source hands over rows
as they arrive and reads the wall; a replay source hands over the rows that
*would* have arrived at race time t, having first moved its clock to t. The
pipeline consuming them cannot tell the difference, which is what makes the
Phase 1 equivalence test possible.
"""
from typing import AsyncIterator, Protocol, runtime_checkable

from app.clock import Clock
from app.pipeline import TickData


@runtime_checkable
class DataSource(Protocol):
    """One poll of race data at a time, timed by `clock`."""

    clock: Clock
    name: str

    def ticks(self) -> AsyncIterator[TickData]:
        """
        Yield ticks until cancelled.

        Implementations own their own pacing: live sleeps for the poll
        interval, replay sleeps for the scaled race interval, and a batch run
        does not sleep at all.
        """
        ...
