"""
One cached race, stepped on the live poll grid.

Replay answers the same question live polling does - "what would OpenF1 have
returned if asked at race time t?" - for every t on a fixed grid of
POLL_POSITIONS_INTERVAL_S ticks from lights out. Tick k is at
`start + k * tick_s`, always, which is what lets a pipeline rebuilt from the
last few dozen ticks (1.4) line up with one that has run from the start: both
see exactly the same instants.

Each tick carries the latest position and interval row per driver as of t,
which is what `get_latest_positions()` / `get_latest_intervals()` return when
polled live. Laps follow live's slower cadence too: the live source refreshes
them every POLL_LAPS_INTERVAL_S and reuses the result in between, so replay
refreshes them on the same ticks rather than knowing a lap time up to ten
seconds before live could have.

Nothing here sleeps. This is fast mode: seek, the stateless rebuild and the
Phase 2 batch analyzer all run ticks as fast as they read. Pacing to the wall
clock belongs to the source that drives it (app/sources/replay.py).
"""
from bisect import bisect_right
from collections import deque
from datetime import datetime, timedelta
from typing import Deque, Dict, Iterator, List, Optional, Set, Tuple
import math

from app.clock import ensure_utc
from app.config import config
from app.pipeline import RacePipeline, TickData
from app.replay.store import SessionArchive
from app.replay.timeline import Event, Timeline, build_timeline


def laps_refresh_ticks(tick_s: float) -> int:
    """
    Ticks between lap refreshes: the first tick at least POLL_LAPS_INTERVAL_S
    after the last one, which is when LiveSource would fetch /laps again.
    """
    # The epsilon keeps an exact multiple (10s at a 2s tick) from rounding up.
    return max(1, math.ceil(config.POLL_LAPS_INTERVAL_S / tick_s - 1e-9))


class RaceReplay:
    """A cached race and its tick grid."""

    def __init__(
        self,
        archive: SessionArchive,
        timeline: Optional[Timeline] = None,
        tick_s: Optional[float] = None,
    ):
        self.archive = archive
        self.timeline = timeline or build_timeline(archive)
        self.tick_s = tick_s or config.POLL_POSITIONS_INTERVAL_S
        self.laps_every = laps_refresh_ticks(self.tick_s)
        self.tick_count = int(self.timeline.duration.total_seconds() // self.tick_s) + 1

        self.drivers_info: Dict[int, Dict] = {
            row["driver_number"]: row
            for row in archive.rows("drivers")
            if row.get("driver_number") is not None
        }
        self._times: List[datetime] = [event.at for event in self.timeline.events]

        # Running maximum of completed laps, for the lap counter.
        self._lap_times: List[datetime] = []
        self._lap_numbers: List[int] = []
        best = 0
        for event in self.timeline.events:
            if event.kind == "lap" and event.lap_number > best:
                best = event.lap_number
                self._lap_times.append(event.at)
                self._lap_numbers.append(best)

        self._status_times: List[datetime] = [self.start]
        self._statuses: List[str] = [self.timeline.initial_track_status]
        for event in self.timeline.events:
            if event.kind == "track_status":
                self._status_times.append(event.at)
                self._statuses.append(event.status)

        winner = min(
            (row for row in archive.rows("session_result") if row.get("position")),
            key=lambda row: row["position"],
            default=None,
        )
        self.total_laps: Optional[int] = (
            (winner or {}).get("number_of_laps") or (self._lap_numbers[-1] if self._lap_numbers else None)
        )

    @property
    def session_key(self) -> int:
        return self.archive.session_key

    @property
    def start(self) -> datetime:
        return self.timeline.start

    def tick_time(self, tick: int) -> datetime:
        return self.start + timedelta(seconds=tick * self.tick_s)

    def tick_at(self, race_time: datetime) -> int:
        """The last tick at or before `race_time`, clamped to the race."""
        elapsed = (ensure_utc(race_time) - self.start).total_seconds()
        return min(self.tick_count - 1, max(0, int(elapsed // self.tick_s)))

    def lap_at(self, race_time: datetime) -> Optional[int]:
        """
        The lap the leader is on: their completed laps plus one (fixes B7).

        The leader always has the most completed laps, so the running maximum
        over every driver is the leader's count without needing the order.
        """
        index = bisect_right(self._lap_times, ensure_utc(race_time))
        completed = self._lap_numbers[index - 1] if index else 0
        lap = completed + 1
        return min(lap, self.total_laps) if self.total_laps else lap

    def track_status_at(self, race_time: datetime) -> str:
        index = bisect_right(self._status_times, ensure_utc(race_time))
        return self._statuses[max(0, index - 1)]

    def cursor(self) -> "ReplayCursor":
        return ReplayCursor(self)

    def ticks(self, first: int = 0, last: Optional[int] = None) -> Iterator[Tuple[datetime, TickData]]:
        """Ticks `first`..`last` inclusive, the first of them marked `reset`."""
        last = self.tick_count - 1 if last is None else last
        cursor = self.cursor()
        for tick in range(first, last + 1):
            yield self.tick_time(tick), cursor.tick(tick, reset=(tick == first))


class ReplayCursor:
    """
    Walks one race's events forward, holding what is known as of the last tick.

    Ticks must be asked for in increasing order. The first can be anywhere:
    the cursor fast-forwards to it, which is a scan of plain dict updates and
    costs a few milliseconds even at the end of a race.
    """

    def __init__(self, replay: RaceReplay):
        self.replay = replay
        self._next = 0          # index of the first event not yet applied
        self._last_tick: Optional[int] = None
        self._positions: Dict[int, Event] = {}
        self._intervals: Dict[int, Event] = {}
        self._laps: Dict[int, Deque[Dict]] = {}
        self._retired: Set[int] = set()
        self._stints: Dict[int, Dict] = {}
        self._pit_stops: Dict[int, int] = {}
        self._track_status = replay.timeline.initial_track_status
        self._laps_snapshot: Dict[int, List[Dict]] = {}

        for event in replay.timeline.carried:
            self._apply(event)

    def tick(self, tick: int, reset: bool = False) -> TickData:
        if self._last_tick is not None and tick <= self._last_tick:
            raise ValueError(f"tick {tick} is not after {self._last_tick}; cursors only go forward")

        refresh = tick - tick % self.replay.laps_every
        if self._last_tick is None or refresh > self._last_tick:
            # The lap refresh this tick reuses happened at or after the last
            # tick served, so its snapshot has to be taken on the way past.
            self._advance_to(self.replay.tick_time(refresh))
            self._laps_snapshot = {
                driver: list(laps) for driver, laps in self._laps.items()
            }

        self._advance_to(self.replay.tick_time(tick))
        self._last_tick = tick

        retired = frozenset(self._retired)
        return TickData(
            positions=[e.row for d, e in self._positions.items() if d not in retired],
            intervals=[e.row for d, e in self._intervals.items() if d not in retired],
            drivers_info=self.replay.drivers_info,
            laps={d: laps for d, laps in self._laps_snapshot.items() if d not in retired},
            track_status=self._track_status,
            reset=reset,
            retired=retired,
            stints={d: row for d, row in self._stints.items() if d not in retired},
            pit_stops=dict(self._pit_stops),
            season=self.replay.archive.manifest.year,
        )

    @property
    def track_status(self) -> str:
        return self._track_status

    def _advance_to(self, moment: datetime) -> None:
        events = self.replay.timeline.events
        stop = bisect_right(self.replay._times, moment, lo=self._next)
        for index in range(self._next, stop):
            self._apply(events[index])
        self._next = max(self._next, stop)

    def _apply(self, event: Event) -> None:
        kind = event.kind
        if kind == "position":
            self._latest(self._positions, event)
        elif kind == "interval":
            self._latest(self._intervals, event)
        elif kind == "lap":
            laps = self._laps.get(event.driver_number)
            if laps is None:
                laps = self._laps[event.driver_number] = deque(maxlen=config.BATTLE_PACE_TREND_WINDOW)
            laps.append(event.row)
        elif kind == "track_status":
            self._track_status = event.status
        elif kind == "retirement":
            self._retired.add(event.driver_number)
        elif kind == "stint":
            self._stints[event.driver_number] = event.row
        elif kind == "pit":
            self._pit_stops[event.driver_number] = self._pit_stops.get(event.driver_number, 0) + 1

    @staticmethod
    def _latest(table: Dict[int, Event], event: Event) -> None:
        # Strictly later only: on a tie, live's _latest_by_driver keeps the
        # first row it saw, and so does this.
        current = table.get(event.driver_number)
        if current is None or event.at > current.at:
            table[event.driver_number] = event


def play(
    pipeline: RacePipeline,
    replay: RaceReplay,
    first: int = 0,
    last: Optional[int] = None,
    detect_from: Optional[int] = None,
) -> RacePipeline:
    """
    Fast mode: run ticks `first`..`last` through `pipeline` without sleeping.

    Ticks before `detect_from` are ingested but not detected: they only fill
    driver history. The pipeline's clock must be a ReplayClock; it is moved
    to each tick's race time before the tick is ingested, exactly as a paced
    source does.
    """
    detect_from = first if detect_from is None else detect_from
    for index, (moment, tick) in enumerate(replay.ticks(first, last), start=first):
        pipeline.clock.set(moment)
        pipeline.ingest(tick)
        if index >= detect_from:
            pipeline.detect()
    return pipeline
