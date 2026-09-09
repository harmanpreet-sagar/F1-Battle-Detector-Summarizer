"""
Pydantic models for API request/response schemas.
"""
from pydantic import BaseModel, Field
from typing import Literal, Optional, List
from datetime import datetime


class DriverState(BaseModel):
    """Current state of a driver."""
    driver_number: int
    full_name: str
    team_name: str
    position: int
    last_lap_time_s: Optional[float] = None
    gap_to_leader_s: Optional[float] = None
    gap_to_ahead_s: Optional[float] = None
    tire_compound: Optional[str] = None
    tire_age_laps: Optional[int] = None
    pit_stops_count: int = 0
    updated_at: datetime
    gap_updated_at: Optional[datetime] = Field(
        default=None,
        description=(
            "Timestamp of the /intervals row the gaps came from. Lags updated_at "
            "because intervals refresh slower than positions; repeated across polls "
            "when no fresh interval has arrived."
        ),
    )
    data_confidence: Literal["high", "medium", "low"] = "high"


class BattleFlags(BaseModel):
    """Flags indicating special battle conditions."""
    pit_window_active: bool = False
    under_yellow: bool = False
    data_quality_warning: bool = False


class Battle(BaseModel):
    """A detected battle between two drivers."""
    battle_id: str = Field(..., description="Unique ID: {chaser}_{ahead}")
    chaser_driver_number: int
    ahead_driver_number: int
    chaser_position: int
    ahead_position: int
    gap_now_s: float
    closing_rate_s_per_s: Optional[float] = None
    pace_delta_s_per_lap: Optional[float] = None
    battle_score: float
    intensity: Literal["HOT", "WATCH", "NONE"]
    explanation: str
    trend_gap_s: List[float] = Field(default_factory=list, description="Last 6 updates")
    trend_timestamps: List[datetime] = Field(default_factory=list)
    duration_updates: int = 0
    flags: BattleFlags = Field(default_factory=BattleFlags)


class SessionStatus(BaseModel):
    """Current F1 session status."""
    session_key: int
    session_name: str
    session_type: str
    session_status: Literal["started", "finished", "aborted"]
    circuit_short_name: str
    meeting_name: str
    current_lap: Optional[int] = None
    total_laps: Optional[int] = None
    track_status: Optional[Literal["green", "yellow", "red", "sc", "vsc"]] = None
    gmt_offset: str
    updated_at: datetime


class HealthStatus(BaseModel):
    """Health status of the backend service."""
    status: Literal["healthy", "degraded", "unhealthy"]
    openf1_connected: bool
    last_successful_poll_positions: Optional[datetime] = None
    last_successful_poll_laps: Optional[datetime] = None
    data_delay_seconds: float = 0.0
    active_session: bool = False
    error_count_last_minute: int = 0
    message: Optional[str] = None
