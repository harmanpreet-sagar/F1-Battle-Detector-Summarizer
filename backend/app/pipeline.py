"""
The pipeline that turns one poll of race data into detected battles.

This is the seam that makes replay possible (B3). Detection used to live in
main.py's poll loops, reading module-level `state_manager` and
`battle_detector` singletons. That meant a replay, a batch analysis and the
live API could never run side by side: they would share one set of driver
states and one battle tracker, and the last writer would win.

A RacePipeline owns its own state and detector, and reads time from its own
clock. Two of them can run in the same process without touching each other.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional
import logging

from app.battle import BattleDetector
from app.clock import Clock
from app.models import Battle, DriverState
from app.state import StateManager

logger = logging.getLogger(__name__)


@dataclass
class TickData:
    """
    Exactly what one poll of the live API returns, as a plain value.

    A replay source builds the same object from cached rows, which is what lets
    the pipeline be unable to tell the two apart.

    `states` is the exception: the mock generator produces finished DriverState
    objects rather than OpenF1 rows, so it fills that field instead of
    positions/intervals. When it is set the raw rows are ignored.
    """
    positions: List[Dict] = field(default_factory=list)
    intervals: List[Dict] = field(default_factory=list)
    drivers_info: Dict[int, Dict] = field(default_factory=dict)
    laps: Dict[int, List[Dict]] = field(default_factory=dict)
    track_status: Optional[str] = None
    states: Optional[List[DriverState]] = None


class RacePipeline:
    """
    StateManager + BattleDetector for one race, driven by one clock.

    Named rather than global: main.py keeps a registry ("live", "replay") and
    endpoints read whichever is active, so adding a mode does not touch a
    single request handler.
    """

    def __init__(self, clock: Clock, name: str = "pipeline"):
        self.name = name
        self.clock = clock
        self.state = StateManager()
        self.detector = BattleDetector()
        self._track_status: Optional[str] = None

    # -- writing --------------------------------------------------------

    def ingest(self, tick: TickData) -> None:
        """Fold one poll's worth of data into driver state."""
        self._track_status = tick.track_status

        if tick.states is not None:
            for state in tick.states:
                self.state.update_driver_state(state)
            return

        if tick.positions:
            self.state.update_from_openf1_positions(
                tick.positions,
                tick.drivers_info,
                intervals=tick.intervals,
                laps=tick.laps,
                now=self.clock.now(),
            )

    def detect(self) -> List[Battle]:
        """
        Run detection over current state and cache the result.

        Called once per ingest, not once per HTTP request: the stability filter
        and the eviction clock only mean anything if they advance with incoming
        data rather than with client traffic.
        """
        driver_states = self.state.get_all_current_states()
        if not driver_states:
            return []

        driver_histories = {
            driver.driver_number: self.state.get_history(driver.driver_number)
            for driver in driver_states
        }

        battles = self.detector.detect(
            driver_states,
            driver_histories,
            self._track_status,
            now=self.clock.now(),
        )
        logger.debug(
            f"[{self.name}] {len(battles)} battles shown, "
            f"{self.detector.tracked_count} tracked"
        )
        return battles

    def reset(self) -> None:
        """
        Drop everything accumulated for the previous session.

        Driver states, gap history and battle IDs are all scoped to one
        session. Carried across a session change they produce battles between
        drivers whose gaps were measured in a different race.
        """
        self.state.clear()
        self.detector.reset()
        self._track_status = None

    # -- read-only views used by the API --------------------------------

    def current_states(self) -> List[DriverState]:
        return self.state.get_all_current_states()

    def latest_battles(self) -> List[Battle]:
        return self.detector.latest_battles

    @property
    def detected_at(self) -> Optional[datetime]:
        return self.detector.detected_at

    @property
    def track_status(self) -> Optional[str]:
        return self._track_status

    def history(self, driver_number: int) -> List[DriverState]:
        return self.state.get_history(driver_number)

    def driver_info(self, driver_number: int) -> Optional[Dict]:
        return self.state.get_driver_info(driver_number)
