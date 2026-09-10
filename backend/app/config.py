"""
Configuration management - loads from environment variables with sensible defaults.
"""
import os
import warnings
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

# Read backend/.env, which is what the README tells you to create. Without this
# the file is inert outside Docker (compose passes it via env_file), so the app
# silently runs in the wrong mode with DATA_MODE sitting in the file.
#
# override=False: a real environment variable always wins, so Docker, CI and a
# one-off `DATA_MODE=mock uvicorn ...` keep behaving as before.
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)


DATA_MODES = ("live", "replay", "mock")


def resolve_data_mode() -> str:
    """
    Where driver data comes from: live | replay | mock.

    Defaults to `mock` so that running the app, or CI, never reaches the
    network by accident. Live data now needs a paid OpenF1 sponsor token, so
    defaulting to `live` would mean the out-of-the-box experience is a stream
    of auth errors.

    TEST_MODE is the old switch and still works, mapped onto the new one. It is
    read only when DATA_MODE is unset, and TEST_MODE=false still selects live so
    that an existing deployment does not silently drop to mock data.
    """
    explicit = os.getenv("DATA_MODE")
    if explicit:
        mode = explicit.strip().lower()
        if mode not in DATA_MODES:
            raise ValueError(
                f"DATA_MODE={explicit!r} is not one of {', '.join(DATA_MODES)}"
            )
        return mode

    legacy = os.getenv("TEST_MODE")
    if legacy is not None:
        mode = "mock" if legacy.strip().lower() == "true" else "live"
        warnings.warn(
            f"TEST_MODE is deprecated and will be removed; use DATA_MODE={mode}",
            DeprecationWarning,
            stacklevel=2,
        )
        return mode

    return "mock"


class Config:
    """Application configuration."""

    # Where driver data comes from
    DATA_MODE: str = resolve_data_mode()

    # OpenF1 API
    OPENF1_BASE_URL: str = os.getenv("OPENF1_BASE_URL", "https://api.openf1.org/v1")
    OPENF1_TIMEOUT_S: float = float(os.getenv("OPENF1_TIMEOUT_S", "5.0"))
    OPENF1_MAX_RETRIES: int = int(os.getenv("OPENF1_MAX_RETRIES", "3"))
    # Live data is behind OpenF1's paid Sponsor tier. Historical data needs no
    # token, so replay and mock ignore this entirely.
    OPENF1_API_TOKEN: Optional[str] = os.getenv("OPENF1_API_TOKEN") or None

    # Polling intervals
    POLL_POSITIONS_INTERVAL_S: float = float(os.getenv("POLL_POSITIONS_INTERVAL_S", "1.5"))
    POLL_LAPS_INTERVAL_S: float = float(os.getenv("POLL_LAPS_INTERVAL_S", "10.0"))
    POLL_SESSION_INTERVAL_S: float = float(os.getenv("POLL_SESSION_INTERVAL_S", "30.0"))

    # How many states to retain per driver. Decoupled from the trend window:
    # detection reads a short recent slice, but /drivers/{n}/trend and the
    # sparkline want more history than detection does. 60 is ~90s at a 1.5s
    # poll, for 20 drivers - trivial memory.
    HISTORY_MAX_LEN: int = int(os.getenv("HISTORY_MAX_LEN", "60"))

    # Battle detection
    BATTLE_GAP_TREND_WINDOW: int = int(os.getenv("BATTLE_GAP_TREND_WINDOW", "6"))
    BATTLE_PACE_TREND_WINDOW: int = int(os.getenv("BATTLE_PACE_TREND_WINDOW", "3"))
    # Distinct gap readings a battle must survive before it is shown
    BATTLE_MIN_DURATION_UPDATES: int = int(os.getenv("BATTLE_MIN_DURATION_UPDATES", "3"))
    BATTLE_MAX_GAP_S: float = float(os.getenv("BATTLE_MAX_GAP_S", "3.0"))
    # How long a battle survives without being re-detected before it is dropped
    BATTLE_EVICTION_S: float = float(os.getenv("BATTLE_EVICTION_S", "30.0"))

    # Thresholds
    BATTLE_WATCH_SCORE: float = float(os.getenv("BATTLE_WATCH_SCORE", "0.55"))
    BATTLE_WATCH_GAP_S: float = float(os.getenv("BATTLE_WATCH_GAP_S", "1.8"))
    BATTLE_HOT_SCORE: float = float(os.getenv("BATTLE_HOT_SCORE", "0.70"))
    BATTLE_HOT_GAP_S: float = float(os.getenv("BATTLE_HOT_GAP_S", "1.2"))

    # Data quality
    DATA_STALE_THRESHOLD_S: float = float(os.getenv("DATA_STALE_THRESHOLD_S", "5.0"))
    DATA_CONFIDENCE_MEDIUM_S: float = float(os.getenv("DATA_CONFIDENCE_MEDIUM_S", "3.0"))
    # /intervals refreshes ~every 4s; allow ~3 missed refreshes before the gap is stale
    INTERVAL_STALE_THRESHOLD_S: float = float(os.getenv("INTERVAL_STALE_THRESHOLD_S", "12.0"))

    # CORS
    CORS_ORIGINS: List[str] = os.getenv(
        "CORS_ORIGINS",
        "http://localhost:3000,http://localhost:3001"
    ).split(",")

    # Storage (optional)
    SQLITE_DB_PATH: str = os.getenv("SQLITE_DB_PATH", "./data/f1_sessions.db")
    SNAPSHOT_INTERVAL_S: float = float(os.getenv("SNAPSHOT_INTERVAL_S", "30.0"))

    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    @property
    def TEST_MODE(self) -> bool:
        """Deprecated alias for `DATA_MODE == "mock"`. Prefer DATA_MODE."""
        return self.DATA_MODE == "mock"


config = Config()
