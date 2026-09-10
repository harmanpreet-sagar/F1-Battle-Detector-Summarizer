"""
The TEST_MODE generator, as a DataSource.

Mock is the default mode so that running the app or CI never reaches the
network by accident. It produces finished DriverState objects rather than raw
OpenF1 rows, which is why TickData carries `states`.
"""
from typing import AsyncIterator
import asyncio
import logging

from app.clock import Clock
from app.config import config
from app.health import health_manager
from app.mock_data import mock_data_generator
from app.pipeline import TickData

logger = logging.getLogger(__name__)


class MockSource:
    """Generated race data on the live poll cadence."""

    name = "mock"

    def __init__(self, clock: Clock, sessions, generator=None, health=None):
        self.clock = clock
        self._sessions = sessions
        self._generator = generator or mock_data_generator
        self._health = health or health_manager

    async def ticks(self) -> AsyncIterator[TickData]:
        while True:
            session = self._sessions.get_current_session()

            self._health.record_successful_position_poll()
            self._health.active_session = True

            yield TickData(
                states=self._generator.generate_driver_states(),
                track_status=session.track_status if session else None,
            )

            await asyncio.sleep(config.POLL_POSITIONS_INTERVAL_S)
