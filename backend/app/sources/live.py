"""
Live OpenF1 polling, moved out of main.py's poll loop unchanged.

Live data sits behind OpenF1's paid Sponsor tier, so this path needs
OPENF1_API_TOKEN to return anything during a session. It is kept working
rather than deleted: the pipeline is supposed to be source-agnostic, and a
live path that has quietly rotted would make that claim untestable.
"""
from datetime import datetime
from typing import AsyncIterator, Callable, Optional
import asyncio
import logging

from app.clock import Clock
from app.config import config
from app.health import health_manager
from app.openf1_client import openf1_client, OpenF1APIError
from app.pipeline import TickData

logger = logging.getLogger(__name__)


class LiveSource:
    """Polls OpenF1 for the currently active session."""

    name = "live"

    def __init__(self, clock: Clock, sessions, client=None, health=None,
                 sleep: Callable = asyncio.sleep):
        self.clock = clock
        self._sessions = sessions
        self._client = client or openf1_client
        self._health = health or health_manager
        self._sleep = sleep

    async def ticks(self) -> AsyncIterator[TickData]:
        retry_delay = 1.0
        max_retry_delay = 30.0

        # Lap data changes once a lap, so it is refreshed on its own slower
        # cadence and reused across position polls.
        cached_laps: dict = {}
        laps_fetched_at: Optional[datetime] = None

        while True:
            try:
                if not self._sessions.is_session_active():
                    await self._sleep(5)
                    continue

                session = self._sessions.get_current_session()
                session_key = session.session_key

                # Gaps come from /intervals - /position carries position
                # numbers only.
                positions = await self._client.get_latest_positions(session_key)
                intervals = await self._client.get_latest_intervals(session_key)
                drivers_data = await self._client.get_drivers(session_key)

                # Timed by the source's clock, which is the wall when live.
                # Replay refreshes laps on the same cadence (replay/engine.py),
                # and the live-equivalence test drives this with a fake clock.
                now = self.clock.now()
                if laps_fetched_at is None or (now - laps_fetched_at).total_seconds() >= config.POLL_LAPS_INTERVAL_S:
                    cached_laps = await self._client.get_latest_laps(
                        session_key, count=config.BATTLE_PACE_TREND_WINDOW
                    )
                    laps_fetched_at = now
                    self._health.record_successful_lap_poll()
                    logger.debug(f"Refreshed lap data for {len(cached_laps)} drivers")

                drivers_info = {
                    d["driver_number"]: d for d in drivers_data if "driver_number" in d
                }

                if positions:
                    self._health.record_successful_position_poll()
                    logger.debug(
                        f"Polled positions for {len(positions)} drivers "
                        f"({len(intervals)} interval rows)"
                    )
                    yield TickData(
                        positions=positions,
                        intervals=intervals,
                        drivers_info=drivers_info,
                        laps=cached_laps,
                        track_status=session.track_status,
                    )

                retry_delay = config.POLL_POSITIONS_INTERVAL_S
                await self._sleep(config.POLL_POSITIONS_INTERVAL_S)

            except OpenF1APIError as e:
                logger.error(f"OpenF1 API error in position polling: {e}")
                self._health.record_error()
                await self._sleep(retry_delay)
                retry_delay = min(retry_delay * 2, max_retry_delay)

            except asyncio.CancelledError:
                raise

            except Exception as e:
                logger.exception(f"Unexpected error in position polling: {e}")
                self._health.record_error()
                await self._sleep(retry_delay)
