"""
Health check and status monitoring.
"""
from datetime import datetime
from collections import deque
import time

from app.models import HealthStatus


class HealthManager:
    """Manages health status and error tracking."""
    
    def __init__(self):
        self.last_successful_poll_positions = None
        self.last_successful_poll_laps = None
        self.error_timestamps = deque(maxlen=100)
        self.openf1_connected = False
        self.active_session = False
    
    def record_successful_position_poll(self):
        """Record a successful position poll."""
        self.last_successful_poll_positions = datetime.now()
        self.openf1_connected = True
    
    def record_successful_lap_poll(self):
        """Record a successful lap poll."""
        self.last_successful_poll_laps = datetime.now()
        self.openf1_connected = True
    
    def record_error(self):
        """Record an error occurrence."""
        self.error_timestamps.append(time.time())
        self.openf1_connected = False
    
    def get_error_count_last_minute(self) -> int:
        """Count errors in the last 60 seconds."""
        now = time.time()
        cutoff = now - 60
        return sum(1 for ts in self.error_timestamps if ts > cutoff)
    
    def get_data_delay(self) -> float:
        """Calculate data delay in seconds."""
        if self.last_successful_poll_positions:
            return (datetime.now() - self.last_successful_poll_positions).total_seconds()
        return 0.0
    
    def get_status(self) -> HealthStatus:
        """Get current health status."""
        error_count = self.get_error_count_last_minute()
        data_delay = self.get_data_delay()
        
        # Determine overall status
        if not self.openf1_connected:
            status = "unhealthy"
            message = "Cannot connect to OpenF1 API"
        elif error_count > 10:
            status = "degraded"
            message = f"High error rate: {error_count} errors/min"
        elif data_delay > 30:
            status = "degraded"
            message = f"Data is stale: {data_delay:.1f}s old"
        else:
            status = "healthy"
            message = None
        
        return HealthStatus(
            status=status,
            openf1_connected=self.openf1_connected,
            last_successful_poll_positions=self.last_successful_poll_positions,
            last_successful_poll_laps=self.last_successful_poll_laps,
            data_delay_seconds=data_delay,
            active_session=self.active_session,
            error_count_last_minute=error_count,
            message=message,
        )


# Global health manager instance
health_manager = HealthManager()


def get_health_status() -> dict:
    """Get health status as dict for API endpoint."""
    return health_manager.get_status().model_dump()
