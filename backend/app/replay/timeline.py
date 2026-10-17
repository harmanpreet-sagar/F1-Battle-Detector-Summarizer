"""
One cached race, merged into a single time-ordered event stream.

A cached session is ten separate endpoints, each with its own cadence and its
own idea of what a timestamp means. Replay needs one question answered
cheaply and exactly: *what had happened by race time t?* The timeline answers
it by flattening every endpoint into one sorted list of events, each stamped
with the instant the pipeline could first have known about it.

The interesting word there is "first". A lap row exists in the archive from
the moment the lap begins, but live polling could not see its `lap_duration`
until the lap finished - so a `LapCompletedEvent` is stamped at
`date_start + lap_duration`, not at `date_start` (fixes B6). Getting that
wrong is how a replay quietly gains information the live path never had, and
it is what the live-equivalence test in 1.3 exists to catch.

Events keep the raw OpenF1 row alongside the parsed fields. The replay source
hands those rows straight to the pipeline, which is what lets the pipeline be
unable to tell replay from live.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import median
from typing import ClassVar, Dict, List, Optional, Sequence, Set, Tuple
import logging

from app.config import config
from app.replay.store import SessionArchive
from app.state import parse_gap, parse_openf1_timestamp

logger = logging.getLogger(__name__)

TrackStatus = str  # one of: green | yellow | red | sc | vsc

# Order applied to events sharing a timestamp. Only determinism is at stake -
# a tick is every event up to t, so within one tick the order does not change
# what the pipeline sees - but a stable stream makes the tests readable and
# makes two builds of the same race compare equal.
_KIND_ORDER = {
    "track_status": 0,
    "retirement": 1,
    "stint": 2,
    "pit": 3,
    "lap": 4,
    "position": 5,
    "interval": 6,
}


@dataclass(frozen=True)
class Event:
    """Something the pipeline could know at race time `at`."""
    at: datetime
    kind: ClassVar[str] = "event"

    @property
    def sort_key(self) -> Tuple[datetime, int, int]:
        return (self.at, _KIND_ORDER.get(self.kind, 99), getattr(self, "driver_number", 0) or 0)


@dataclass(frozen=True)
class PositionEvent(Event):
    driver_number: int
    position: int
    row: Dict = field(default_factory=dict, repr=False)
    kind: ClassVar[str] = "position"


@dataclass(frozen=True)
class IntervalEvent(Event):
    driver_number: int
    interval_s: Optional[float]
    gap_to_leader_s: Optional[float]
    row: Dict = field(default_factory=dict, repr=False)
    kind: ClassVar[str] = "interval"


@dataclass(frozen=True)
class LapCompletedEvent(Event):
    driver_number: int
    lap_number: int
    lap_duration_s: float
    row: Dict = field(default_factory=dict, repr=False)
    kind: ClassVar[str] = "lap"


@dataclass(frozen=True)
class TrackStatusEvent(Event):
    status: TrackStatus
    message: str = ""
    kind: ClassVar[str] = "track_status"


@dataclass(frozen=True)
class StintEvent(Event):
    driver_number: int
    compound: Optional[str]
    lap_start: Optional[int]
    lap_end: Optional[int]
    tyre_age_at_start: Optional[int]
    row: Dict = field(default_factory=dict, repr=False)
    kind: ClassVar[str] = "stint"


@dataclass(frozen=True)
class PitEvent(Event):
    driver_number: int
    lap_number: Optional[int]
    pit_duration_s: Optional[float]
    row: Dict = field(default_factory=dict, repr=False)
    kind: ClassVar[str] = "pit"


@dataclass(frozen=True)
class RetirementEvent(Event):
    """
    A driver replay should stop showing.

    Their rows simply stop, and without this the last position and gap they
    ever reported sit in the running order for the rest of the race, inventing
    battles against a car that is in the garage.
    """
    driver_number: int
    reason: str = "no data"
    kind: ClassVar[str] = "retirement"


# --------------------------------------------------------------------------
# Track status
# --------------------------------------------------------------------------

# Nothing sector-scoped can pull the track out of a safety car. An SC period
# is full of sector yellows, and the incident that caused it gets a "CLEAR IN
# TRACK SECTOR n" while the car is still circulating - so reading that clear
# as a green would restart battle detection mid-neutralisation, which is B4
# in reverse. Only a track-wide green or clear ends sc/vsc/red.
_NEUTRALISED = ("sc", "vsc", "red")


def track_status_from(row: Dict) -> Optional[TrackStatus]:
    """
    Map one `race_control` row to a track status, or None if it says nothing.

    Race control is a message feed, not a status field, so the mapping is over
    `category`, `flag` and the message text together. "SAFETY CAR IN THIS LAP"
    and "VIRTUAL SAFETY CAR ENDING" announce an intention rather than a state:
    the track is still neutralised until the green flag that follows, so they
    map to None and leave the current status alone.
    """
    message = (row.get("message") or "").upper()
    category = (row.get("category") or "").strip()
    flag = (row.get("flag") or "").upper().strip()
    scope = (row.get("scope") or "").strip()

    if flag == "RED":
        return "red"

    if category == "SafetyCar" or "SAFETY CAR" in message:
        if "ENDING" in message or "IN THIS LAP" in message:
            return None
        if "VIRTUAL" in message or "VSC" in message:
            return "vsc"
        return "sc"

    if flag in ("GREEN", "CLEAR", "CHEQUERED"):
        return "green"

    if flag in ("YELLOW", "DOUBLE YELLOW"):
        # A driver-scoped yellow is about one car, not the track.
        return "yellow" if scope != "Driver" else None

    return None


def _apply_status(current: TrackStatus, incoming: TrackStatus,
                  scope: str = "Track") -> TrackStatus:
    """Fold one message into the running status, respecting its scope."""
    if current in _NEUTRALISED and (scope == "Sector" or incoming == "yellow"):
        return current
    return incoming


# --------------------------------------------------------------------------
# Timeline
# --------------------------------------------------------------------------

@dataclass
class Timeline:
    """Every event of one race, sorted, with the bounds replay runs between."""
    session_key: int
    start: datetime
    end: datetime
    events: List[Event]
    drivers: Set[int] = field(default_factory=set)
    initial_track_status: TrackStatus = "green"
    # Each driver's last position and gap row from before `start`. Position
    # emits only on change, so a car holding its grid slot has no position
    # event for a long time after lights out - 26 minutes for the last of
    # them in the 2025 Hungarian GP. A live poll at the start would still
    # have returned that grid row, so replay starts from it too.
    carried: List[Event] = field(default_factory=list)

    @property
    def duration(self) -> timedelta:
        return self.end - self.start

    def __len__(self) -> int:
        return len(self.events)

    def between(self, first: datetime, last: datetime) -> List[Event]:
        """Events in (`first`, `last`]: what one tick of replay reveals."""
        return [e for e in self.events if first < e.at <= last]

    def until(self, moment: datetime) -> List[Event]:
        """Everything known by race time `moment`."""
        return [e for e in self.events if e.at <= moment]

    def quiet_periods(
        self, threshold_s: Optional[float] = None
    ) -> List[Tuple[datetime, datetime]]:
        """
        Stretches of race time with no events at all, longer than `threshold_s`.

        A red flag empties the feed for anything from ten minutes to an hour.
        Played back at race speed that is a frozen screen, so 1.3 compresses
        each of these to a few seconds of playback and the UI says why.
        """
        threshold = config.REPLAY_QUIET_GAP_S if threshold_s is None else threshold_s
        gap = timedelta(seconds=threshold)

        periods: List[Tuple[datetime, datetime]] = []
        previous = self.start
        for event in self.events:
            if event.at - previous > gap:
                periods.append((previous, event.at))
            previous = max(previous, event.at)
        return periods


def build_timeline(
    archive: SessionArchive,
    quiet_gap_s: Optional[float] = None,
    retirement_laps: Optional[int] = None,
) -> Timeline:
    """Merge a cached session's endpoints into one time-ordered stream."""
    events: List[Event] = []
    drivers: Set[int] = set()

    events += _position_events(archive.rows("position"), drivers)
    events += _interval_events(archive.rows("intervals"), drivers)
    lap_events = _lap_events(archive.rows("laps"))
    events += lap_events
    events += _stint_events(archive.rows("stints"), lap_events)
    events += _pit_events(archive.rows("pit"))

    start = _race_start(archive, lap_events, events)
    status_events, initial_status = _track_status_events(
        archive.rows("race_control"), start
    )
    events += status_events

    events.sort(key=lambda event: event.sort_key)
    end = events[-1].at if events else start

    events += _retirement_events(
        archive, events, end,
        retirement_laps=(
            config.REPLAY_RETIREMENT_LAPS if retirement_laps is None else retirement_laps
        ),
    )
    events.sort(key=lambda event: event.sort_key)

    # Rows stamped before lights out (formation lap, grid) stay in the archive
    # but have no place in the stream: replaying them means several minutes of
    # a stationary grid before anything happens. The last of them per driver
    # is still what the race starts from, so it is carried rather than lost.
    carried = _carried_rows(event for event in events if event.at < start)
    events = [event for event in events if event.at >= start]

    timeline = Timeline(
        session_key=archive.session_key,
        start=start,
        end=max(end, start),
        events=events,
        drivers=drivers,
        initial_track_status=initial_status,
        carried=carried,
    )
    logger.info(
        "Timeline for %s: %d events over %.0f minutes, %d drivers, %d quiet period(s)",
        archive.session_key, len(events), timeline.duration.total_seconds() / 60,
        len(drivers), len(timeline.quiet_periods(quiet_gap_s)),
    )
    return timeline


# --------------------------------------------------------------------------
# Per-endpoint builders
# --------------------------------------------------------------------------

def _carried_rows(pre_race) -> List[Event]:
    """The latest position and interval event per driver, from before the start."""
    latest: Dict[Tuple[str, int], Event] = {}
    for event in pre_race:
        if event.kind not in ("position", "interval"):
            continue
        key = (event.kind, event.driver_number)
        if key not in latest or event.at >= latest[key].at:
            latest[key] = event
    return sorted(latest.values(), key=lambda event: event.sort_key)


def _position_events(rows: Sequence[Dict], drivers: Set[int]) -> List[PositionEvent]:
    events = []
    for row in rows:
        at = parse_openf1_timestamp(row.get("date"))
        driver = row.get("driver_number")
        position = row.get("position")
        if at is None or driver is None or position is None:
            continue
        drivers.add(driver)
        events.append(PositionEvent(at=at, driver_number=driver, position=position, row=row))
    return events


def _interval_events(rows: Sequence[Dict], drivers: Set[int]) -> List[IntervalEvent]:
    """
    Gap rows, with "+1 LAP" and the leader's null interval left as None.

    `parse_gap` already does that, and it stays done here rather than in the
    pipeline: a lapped car has no gap in seconds, and inventing one would put
    it in a battle with the car it is a lap behind.
    """
    events = []
    for row in rows:
        at = parse_openf1_timestamp(row.get("date"))
        driver = row.get("driver_number")
        if at is None or driver is None:
            continue
        drivers.add(driver)
        events.append(IntervalEvent(
            at=at,
            driver_number=driver,
            interval_s=parse_gap(row.get("interval")),
            gap_to_leader_s=parse_gap(row.get("gap_to_leader")),
            row=row,
        ))
    return events


def _lap_events(rows: Sequence[Dict]) -> List[LapCompletedEvent]:
    """
    A lap enters the stream when it *finishes*, at `date_start + lap_duration`.

    Two kinds of row produce no event:

    - No `lap_duration`. The lap is in progress, or it was ended by a red flag
      or a retirement. It never completed, so it never becomes knowable.
    - No `date_start`. Common on lap 1, where the timing feed starts at the
      lights rather than at the line. The previous lap's completion is the
      same instant this lap began, so it is used as the fallback; with no
      previous lap there is nothing to anchor to and the row is skipped.
    """
    by_driver: Dict[int, List[Dict]] = {}
    for row in rows:
        driver = row.get("driver_number")
        if driver is None:
            continue
        by_driver.setdefault(driver, []).append(row)

    events: List[LapCompletedEvent] = []
    for driver, driver_rows in by_driver.items():
        previous_end: Optional[datetime] = None
        for row in sorted(driver_rows, key=lambda r: r.get("lap_number") or 0):
            duration = row.get("lap_duration")
            start = parse_openf1_timestamp(row.get("date_start")) or previous_end

            if duration is None or start is None:
                if duration is None:
                    # The lap did not finish, so the next lap has no anchor
                    # either: its own date_start is all it can rely on.
                    previous_end = None
                continue

            end = start + timedelta(seconds=float(duration))
            events.append(LapCompletedEvent(
                at=end,
                driver_number=driver,
                lap_number=row.get("lap_number") or 0,
                lap_duration_s=float(duration),
                row=row,
            ))
            previous_end = end

    return events


def _stint_events(
    rows: Sequence[Dict], lap_events: Sequence[LapCompletedEvent]
) -> List[StintEvent]:
    """
    A stint begins when its first lap begins.

    `stints` carries lap numbers, not timestamps, so the start is taken from
    the completion of the lap before `lap_start`. Without one - a driver's
    opening stint, whose first lap has no predecessor - the stint is known
    from the first lap of that stint completing instead. Either way the
    compound is knowable no earlier than a lap boundary, which is when live
    timing would have shown it.
    """
    lap_end_at: Dict[Tuple[int, int], datetime] = {
        (event.driver_number, event.lap_number): event.at for event in lap_events
    }

    events = []
    for row in rows:
        driver = row.get("driver_number")
        lap_start = row.get("lap_start")
        if driver is None or lap_start is None:
            continue

        at = lap_end_at.get((driver, lap_start - 1)) or lap_end_at.get((driver, lap_start))
        if at is None:
            continue

        events.append(StintEvent(
            at=at,
            driver_number=driver,
            compound=row.get("compound"),
            lap_start=lap_start,
            lap_end=row.get("lap_end"),
            tyre_age_at_start=row.get("tyre_age_at_start"),
            row=row,
        ))
    return events


def _pit_events(rows: Sequence[Dict]) -> List[PitEvent]:
    events = []
    for row in rows:
        at = parse_openf1_timestamp(row.get("date"))
        driver = row.get("driver_number")
        if at is None or driver is None:
            continue
        duration = row.get("pit_duration") or row.get("lane_duration")
        events.append(PitEvent(
            at=at,
            driver_number=driver,
            lap_number=row.get("lap_number"),
            pit_duration_s=float(duration) if duration is not None else None,
            row=row,
        ))
    return events


def _track_status_events(
    rows: Sequence[Dict], start: datetime
) -> Tuple[List[TrackStatusEvent], TrackStatus]:
    """
    Race control messages, reduced to the moments the track status changed.

    Anything stamped before lights out sets the status the race starts under
    rather than producing an event of its own, so a replay beginning mid-SC
    knows it. Every stream opens with an explicit event at `start`: the
    pipeline then has a status from its very first tick, instead of an
    implicit green that only becomes real once something changes.
    """
    ordered = []
    for row in rows:
        at = parse_openf1_timestamp(row.get("date"))
        status = track_status_from(row)
        if at is None or status is None:
            continue
        ordered.append((at, status, (row.get("scope") or "Track").strip(),
                        row.get("message") or ""))
    ordered.sort(key=lambda item: item[0])

    initial: TrackStatus = "green"
    for at, status, scope, _ in ordered:
        if at > start:
            break
        initial = _apply_status(initial, status, scope)

    events = [TrackStatusEvent(at=start, status=initial, message="Session start")]
    current = initial
    for at, status, scope, message in ordered:
        if at <= start:
            continue
        resolved = _apply_status(current, status, scope)
        if resolved != current:
            events.append(TrackStatusEvent(at=at, status=resolved, message=message))
            current = resolved

    return events, initial


def _race_start(
    archive: SessionArchive,
    lap_events: Sequence[LapCompletedEvent],
    events: Sequence[Event],
) -> datetime:
    """
    Where playback begins: lights out, not the start of the timing feed.

    The first completed lap 1, minus its own duration, is the moment the race
    started. `session.date_start` is the other candidate and usually the
    earlier of the two, since the feed opens before the formation lap; the
    later one wins, so a replay never opens on several minutes of a
    stationary grid.
    """
    date_start = archive.manifest.date_start

    first_lap_one = min(
        (event for event in lap_events if event.lap_number == 1),
        key=lambda event: event.at,
        default=None,
    )
    if first_lap_one is not None:
        lights_out = first_lap_one.at - timedelta(seconds=first_lap_one.lap_duration_s)
        return max(lights_out, date_start) if date_start else lights_out

    if date_start is not None:
        return date_start
    if events:
        return min(event.at for event in events)
    raise ValueError(f"session {archive.session_key} has no events to replay")


def _retirement_events(
    archive: SessionArchive,
    events: Sequence[Event],
    end: datetime,
    retirement_laps: int,
) -> List[RetirementEvent]:
    """
    Drivers whose rows stop before the race does.

    Two signals, and either is enough. `session_result` names who did not
    finish, which is authoritative but says nothing about when. The rows
    themselves say when: a car that has reported no position or interval for
    `retirement_laps` laps is in the garage. A driver whose rows run to the
    end of the race finished, so the deadline falling past the end of the
    stream is what keeps a normal finish from reading as a retirement.
    """
    last_seen: Dict[int, datetime] = {}
    for event in events:
        if event.kind in ("position", "interval"):
            driver = event.driver_number
            if driver not in last_seen or event.at > last_seen[driver]:
                last_seen[driver] = event.at

    lap_times = [
        event.lap_duration_s for event in events if event.kind == "lap"
    ]
    typical_lap = median(lap_times) if lap_times else 90.0
    grace = timedelta(seconds=typical_lap * retirement_laps)

    classified = {
        row.get("driver_number"): row
        for row in archive.rows("session_result")
        if row.get("driver_number") is not None
    }

    retirements = []
    for driver, seen_at in sorted(last_seen.items()):
        result = classified.get(driver) or {}
        did_not_finish = bool(result.get("dnf") or result.get("dns") or result.get("dsq"))
        deadline = seen_at + grace

        if not did_not_finish and deadline >= end:
            continue

        retirements.append(RetirementEvent(
            at=min(deadline, end),
            driver_number=driver,
            reason="classified DNF" if did_not_finish else "no data",
        ))
    return retirements
