"""
One shared replay that every viewer watches, as a pure function of the wall.

There is no process on Vercel that keeps a replay running between requests;
the function is frozen the moment it responds. So the schedule is arithmetic
instead of state:

    playlist_duration = sum(race playback durations)
    offset            = (wall_now - REPLAY_EPOCH) mod playlist_duration
    (race, race_time) = locate(offset)

A request works out where the broadcast is, replays the preceding
REPLAY_WINDOW_TICKS ticks of that race into a throwaway RacePipeline, and
serves what falls out - the same trick app/demo.py plays for mock mode.
Detection, the stability filter, closing rate and eviction run exactly as they
do live, over a window rebuilt a millisecond ago. Every visitor sees the same
moment of the same race, and a cold start is indistinguishable from a warm one.

The container path (uvicorn, Docker) follows the same schedule through
ReplaySource, but keeps one long-running pipeline instead of rebuilding.

A race's playback duration is its race time divided by REPLAY_SPEED, except
across stoppages: any stretch of the stream with no events for longer than
REPLAY_QUIET_GAP_S (a red flag) plays back in REPLAY_QUIET_PLAYBACK_S wall
seconds rather than as a frozen screen.
"""
from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Sequence
import logging

from app.clock import ReplayClock, ensure_utc
from app.config import config
from app.models import SessionStatus
from app.pipeline import RacePipeline
from app.replay.engine import RaceReplay, play
from app.replay.store import SessionStore
from app.state import parse_openf1_timestamp

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Segment:
    """A stretch of playback mapping wall seconds linearly onto race seconds."""
    wall_start: float
    wall_end: float
    race_start: float
    race_end: float
    quiet: bool = False

    def race_at(self, wall_s: float) -> float:
        span = self.wall_end - self.wall_start
        if span <= 0:
            return self.race_end
        fraction = (wall_s - self.wall_start) / span
        return self.race_start + fraction * (self.race_end - self.race_start)


def playback_segments(
    replay: RaceReplay, speed: float, quiet_playback_s: float
) -> List[Segment]:
    """Wall-to-race mapping for one race, with stoppages compressed."""
    start = replay.start
    race_total = replay.timeline.duration.total_seconds()

    segments: List[Segment] = []
    wall = race = 0.0

    def run_to(race_end: float, quiet: bool, wall_length: float) -> None:
        nonlocal wall, race
        segments.append(Segment(wall, wall + wall_length, race, race_end, quiet))
        wall += wall_length
        race = race_end

    for quiet_start, quiet_end in replay.timeline.quiet_periods():
        a = (quiet_start - start).total_seconds()
        b = (quiet_end - start).total_seconds()
        # Only worth compressing if it would otherwise take longer.
        if (b - a) / speed <= quiet_playback_s:
            continue
        if a > race:
            run_to(a, False, (a - race) / speed)
        run_to(b, True, quiet_playback_s)

    if race_total > race or not segments:
        run_to(race_total, False, (race_total - race) / speed)
    return segments


class _Slot:
    """One race's place in the playlist."""

    def __init__(self, replay: RaceReplay, speed: float, quiet_playback_s: float):
        self.replay = replay
        self.segments = playback_segments(replay, speed, quiet_playback_s)
        self._wall_ends = [segment.wall_end for segment in self.segments]
        self.wall_duration = self.segments[-1].wall_end

    def locate(self, wall_s: float):
        index = min(bisect_right(self._wall_ends, wall_s), len(self.segments) - 1)
        segment = self.segments[index]
        return segment.race_at(wall_s), segment.quiet


@dataclass(frozen=True)
class BroadcastPosition:
    """Where the broadcast is at one wall-clock instant."""
    cycle: int              # completed passes through the playlist
    index: int              # race within the playlist
    session_key: int
    race_time: datetime
    tick: int
    progress: float         # 0..1 through this race's playback
    stoppage: bool          # inside a compressed quiet period

    @property
    def run(self):
        """Identifies one playing of one race; it changes when a race starts over."""
        return (self.cycle, self.index)


class Broadcast:
    """The playlist, its speed and its epoch: everything needed to locate."""

    def __init__(
        self,
        replays: Sequence[RaceReplay],
        speed: Optional[float] = None,
        epoch: Optional[datetime] = None,
        quiet_playback_s: Optional[float] = None,
        window_ticks: Optional[int] = None,
    ):
        if not replays:
            raise ValueError("a broadcast needs at least one cached race")
        self.speed = speed or config.REPLAY_SPEED
        if self.speed <= 0:
            raise ValueError(f"REPLAY_SPEED must be positive, not {self.speed}")
        self.epoch = ensure_utc(epoch or parse_openf1_timestamp(config.REPLAY_EPOCH))
        self.window_ticks = window_ticks or config.REPLAY_WINDOW_TICKS
        quiet = config.REPLAY_QUIET_PLAYBACK_S if quiet_playback_s is None else quiet_playback_s

        self.slots = [_Slot(replay, self.speed, quiet) for replay in replays]
        self._slot_ends: List[float] = []
        total = 0.0
        for slot in self.slots:
            total += slot.wall_duration
            self._slot_ends.append(total)
        self.duration_s = total

    @classmethod
    def from_store(
        cls,
        store: Optional[SessionStore] = None,
        playlist: Optional[Sequence[int]] = None,
        **kwargs,
    ) -> "Broadcast":
        """The configured playlist, read from the cache."""
        store = store or SessionStore()
        keys = list(playlist if playlist is not None else config.REPLAY_PLAYLIST) or store.session_keys()
        if not keys:
            raise FileNotFoundError(
                f"no cached races under {store.root}; "
                "run `python -m app.ingest --year 2025 --round 14` first"
            )
        replays = [RaceReplay(store.load(key)) for key in keys]
        broadcast = cls(replays, **kwargs)
        logger.info(
            "Replay broadcast: %d race(s) %s at %gx, %.0f minutes a loop",
            len(replays), keys, broadcast.speed, broadcast.duration_s / 60,
        )
        return broadcast

    # -- where are we ---------------------------------------------------

    def locate(self, now: datetime) -> BroadcastPosition:
        elapsed = (ensure_utc(now) - self.epoch).total_seconds()
        cycle, offset = divmod(elapsed, self.duration_s)

        index = min(bisect_right(self._slot_ends, offset), len(self.slots) - 1)
        slot = self.slots[index]
        slot_start = self._slot_ends[index] - slot.wall_duration
        within = offset - slot_start

        race_s, stoppage = slot.locate(within)
        replay = slot.replay
        race_time = replay.start + timedelta(seconds=race_s)
        return BroadcastPosition(
            cycle=int(cycle),
            index=index,
            session_key=replay.session_key,
            race_time=race_time,
            tick=replay.tick_at(race_time),
            progress=min(1.0, within / slot.wall_duration) if slot.wall_duration else 1.0,
            stoppage=stoppage,
        )

    def replay(self, index: int) -> RaceReplay:
        return self.slots[index].replay

    def next_race(self, position: BroadcastPosition) -> RaceReplay:
        return self.slots[(position.index + 1) % len(self.slots)].replay

    # -- the stateless rebuild (1.4) --------------------------------------

    def build_pipeline(self, now: datetime) -> RacePipeline:
        """
        A pipeline holding the broadcast as of `now`, built from scratch.

        Two stretches of ticks. The last `window_ticks` are detected, which is
        what builds the battle tracker's memory: the stability filter needs a
        few distinct gap samples, and anything older than BATTLE_EVICTION_S
        has been evicted from a long-running tracker anyway. Before them,
        HISTORY_MAX_LEN ticks are ingested without detection, so that every
        detection in the window sees exactly the driver history a long-running
        pipeline would. Skip that warm-up and early detections run on short
        histories; pace delta reads the whole history, so they score pairs
        differently, and the tracker keeps battles a continuous run had
        already dropped.

        What can still differ is the tracker's memory from before the window:
        `duration_updates` counts distinct gap samples inside it rather than
        since the battle began.
        """
        position = self.locate(now)
        replay = self.replay(position.index)
        first = max(0, position.tick - self.window_ticks + 1)
        warm = max(0, first - config.HISTORY_MAX_LEN)

        pipeline = RacePipeline(clock=ReplayClock(replay.tick_time(warm)), name="replay")
        return play(pipeline, replay, warm, position.tick, detect_from=first)

    def session_at(self, now: datetime) -> SessionStatus:
        """The session the broadcast is showing, with its real lap count."""
        position = self.locate(now)
        replay = self.replay(position.index)
        manifest = replay.archive.manifest
        circuit = manifest.circuit or "Unknown"
        return SessionStatus(
            session_key=replay.session_key,
            session_name=manifest.session_name or "Race",
            session_type="Race",
            session_status="started",
            circuit_short_name=circuit,
            meeting_name=manifest.meeting_name or f"{manifest.year or ''} {circuit}".strip(),
            current_lap=replay.lap_at(position.race_time),
            total_laps=replay.total_laps,
            track_status=replay.track_status_at(position.race_time),
            gmt_offset=manifest.gmt_offset,
            updated_at=position.race_time,
        )


_broadcast: Optional[Broadcast] = None


def get_broadcast() -> Broadcast:
    """
    The configured broadcast, built once per process.

    Building reads and indexes every race in the playlist (~150 ms each), so
    a warm serverless instance must not repeat it per request.
    """
    global _broadcast
    if _broadcast is None:
        _broadcast = Broadcast.from_store()
    return _broadcast
