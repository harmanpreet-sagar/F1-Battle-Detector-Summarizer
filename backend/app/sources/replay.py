"""
A cached race, played back on the broadcast schedule, as a DataSource.

This is the container path (uvicorn, Docker): one long-running pipeline that
accumulates state as it would live. It follows the same wall-clock schedule as
the stateless path (app/replay/broadcast.py), so a container and a Vercel
function started at different times still show the same moment of the same
race.

Each pass of the loop asks the broadcast where it should be, hands over every
tick between where it is and there, and sleeps one tick's worth of wall time
(`tick_s / REPLAY_SPEED`). In steady state that is one tick per pass. On
startup, or when a new race begins, it is a seek: the pipeline is reset and
the race is run in fast mode from lights out to the current instant, which for
a full race is a couple of seconds of work. Across a compressed stoppage it is
several ticks per pass, which is exactly what fast-forwarding through one
means.
"""
from typing import AsyncIterator, Callable, Optional
import asyncio
import logging

from app.clock import Clock, utcnow
from app.health import health_manager
from app.pipeline import TickData
from app.replay.broadcast import Broadcast, get_broadcast

logger = logging.getLogger(__name__)

# Ticks handed over between yields to the event loop during a seek, so a
# fast-forward through a whole race does not stall request handling.
_SEEK_YIELD_EVERY = 200


class ReplaySource:
    """Ticks of the broadcast's current race, paced to the wall."""

    name = "replay"

    def __init__(
        self,
        clock: Clock,
        broadcast: Optional[Broadcast] = None,
        health=None,
        sleep: Callable = asyncio.sleep,
        wall: Callable = utcnow,
    ):
        self.clock = clock
        self._broadcast = broadcast
        self._health = health or health_manager
        self._sleep = sleep
        self._wall = wall

    @property
    def broadcast(self) -> Broadcast:
        if self._broadcast is None:
            self._broadcast = get_broadcast()
        return self._broadcast

    async def ticks(self) -> AsyncIterator[TickData]:
        run = None
        cursor = None
        next_tick = 0

        while True:
            position = self.broadcast.locate(self._wall())
            replay = self.broadcast.replay(position.index)

            if position.run != run:
                if run is not None:
                    logger.info(f"Replay moving on to session {position.session_key}")
                run = position.run
                cursor = replay.cursor()
                next_tick = 0

            served = 0
            for tick in range(next_tick, position.tick + 1):
                self.clock.set(replay.tick_time(tick))
                yield cursor.tick(tick, reset=(tick == 0))
                served += 1
                if served % _SEEK_YIELD_EVERY == 0:
                    await self._sleep(0)
            next_tick = max(next_tick, position.tick + 1)

            if served:
                self._health.record_successful_position_poll()
                self._health.active_session = True

            await self._sleep(replay.tick_s / self.broadcast.speed)
