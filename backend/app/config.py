"""
Configuration management - loads from environment variables with sensible defaults.
"""
import os
from typing import List


class Config:
    """Application configuration."""
    
    # OpenF1 API
    OPENF1_BASE_URL: str = os.getenv("OPENF1_BASE_URL", "https://api.openf1.org/v1")
    OPENF1_TIMEOUT_S: float = float(os.getenv("OPENF1_TIMEOUT_S", "5.0"))
    OPENF1_MAX_RETRIES: int = int(os.getenv("OPENF1_MAX_RETRIES", "3"))
    
    # Polling intervals
    POLL_POSITIONS_INTERVAL_S: float = float(os.getenv("POLL_POSITIONS_INTERVAL_S", "1.5"))
    POLL_LAPS_INTERVAL_S: float = float(os.getenv("POLL_LAPS_INTERVAL_S", "10.0"))
    POLL_SESSION_INTERVAL_S: float = float(os.getenv("POLL_SESSION_INTERVAL_S", "30.0"))
    
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
    
    # Testing
    TEST_MODE: bool = os.getenv("TEST_MODE", "false").lower() == "true"


config = Config()
