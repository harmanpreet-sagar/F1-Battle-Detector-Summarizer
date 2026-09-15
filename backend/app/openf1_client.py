"""
OpenF1 API client with retry logic and error handling.
"""
import httpx
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

from app.config import config

logger = logging.getLogger(__name__)


class OpenF1APIError(Exception):
    """Base exception for OpenF1 API errors."""
    pass


def _latest_by_driver(rows: List[Dict], key) -> List[Dict]:
    """Keep one row per driver_number - the one with the highest `key`."""
    latest: Dict[int, Dict] = {}
    for row in rows:
        driver_num = row.get("driver_number")
        if driver_num is None:
            continue
        current = latest.get(driver_num)
        if current is None or (key(row) or "") > (key(current) or ""):
            latest[driver_num] = row
    return list(latest.values())


class OpenF1Client:
    """Client for interacting with the OpenF1 API."""
    
    def __init__(self):
        self.base_url = config.OPENF1_BASE_URL
        self.timeout = config.OPENF1_TIMEOUT_S
        self.max_retries = config.OPENF1_MAX_RETRIES
        # Live data needs OpenF1's paid Sponsor tier. Historical data is free
        # and unauthenticated, so the header is simply absent without a token.
        headers = {}
        if config.OPENF1_API_TOKEN:
            headers["Authorization"] = f"Bearer {config.OPENF1_API_TOKEN}"
        self.client = httpx.AsyncClient(timeout=self.timeout, headers=headers)
    
    async def _request(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> List[Dict]:
        """Make a request to OpenF1 API with retry logic."""
        url = f"{self.base_url}/{endpoint}"
        
        for attempt in range(self.max_retries):
            try:
                logger.debug(f"Requesting {url} (attempt {attempt + 1}/{self.max_retries})")
                response = await self.client.get(url, params=params)
                response.raise_for_status()
                return response.json()
            
            except httpx.HTTPStatusError as e:
                logger.warning(f"HTTP error {e.response.status_code}: {e}")
                if attempt == self.max_retries - 1:
                    raise OpenF1APIError(f"HTTP {e.response.status_code}") from e
            
            except httpx.RequestError as e:
                logger.warning(f"Request error: {e}")
                if attempt == self.max_retries - 1:
                    raise OpenF1APIError(f"Request failed: {e}") from e
            
            except Exception as e:
                logger.error(f"Unexpected error: {e}")
                if attempt == self.max_retries - 1:
                    raise OpenF1APIError(f"Unexpected error: {e}") from e
        
        return []
    
    async def get_sessions(self, date: Optional[str] = None) -> List[Dict]:
        """Get sessions, optionally filtered by date."""
        params = {"date": date} if date else {}
        return await self._request("sessions", params)
    
    async def get_latest_session(self) -> Optional[Dict]:
        """Get the most recent session."""
        sessions = await self.get_sessions()
        if sessions:
            return sessions[-1]
        return None
    
    async def get_positions(self, session_key: int, driver_number: Optional[int] = None) -> List[Dict]:
        """
        Get position data for a session.
        
        Args:
            session_key: Session identifier
            driver_number: Optional driver number to filter by
        
        Returns:
            List of position data points
        """
        params = {"session_key": session_key}
        if driver_number:
            params["driver_number"] = driver_number
        return await self._request("position", params)
    
    async def get_latest_positions(self, session_key: int) -> List[Dict]:
        """
        Get the most recent position for each driver in a session.
        OpenF1 returns data sorted by date, so we get the last entry per driver.
        """
        positions = await self.get_positions(session_key)
        return _latest_by_driver(positions, key=lambda r: r.get("date"))
    
    async def get_intervals(self, session_key: int, driver_number: Optional[int] = None) -> List[Dict]:
        """
        Get interval data for a session.

        NOTE: the /position endpoint carries only position numbers. Gap-to-ahead
        (`interval`) and `gap_to_leader` live here, on /intervals.

        Args:
            session_key: Session identifier
            driver_number: Optional driver number to filter by
        """
        params = {"session_key": session_key}
        if driver_number:
            params["driver_number"] = driver_number
        return await self._request("intervals", params)

    async def get_latest_intervals(self, session_key: int) -> List[Dict]:
        """
        Get the most recent interval row for each driver in a session.

        /intervals refreshes roughly every 4s, so the same row is expected to be
        returned across several position polls. Callers use each row's own `date`
        to tell a fresh sample from a repeated one.
        """
        rows = await self.get_intervals(session_key)
        return _latest_by_driver(rows, key=lambda r: r.get("date"))

    async def get_laps(self, session_key: int, driver_number: Optional[int] = None) -> List[Dict]:
        """
        Get lap data for a session.

        Args:
            session_key: Session identifier
            driver_number: Optional driver number to filter by
        """
        params = {"session_key": session_key}
        if driver_number:
            params["driver_number"] = driver_number
        return await self._request("laps", params)

    async def get_latest_laps(self, session_key: int, count: int = 3) -> Dict[int, List[Dict]]:
        """
        Get each driver's last `count` completed laps, oldest first.

        Rows without a `lap_duration` are the lap currently in progress and are
        skipped — only completed laps carry a usable time.
        """
        rows = await self.get_laps(session_key)

        by_driver: Dict[int, List[Dict]] = {}
        for row in rows:
            driver_num = row.get("driver_number")
            if driver_num is None or row.get("lap_duration") is None:
                continue
            by_driver.setdefault(driver_num, []).append(row)

        return {
            driver_num: sorted(laps, key=lambda r: r.get("lap_number") or 0)[-count:]
            for driver_num, laps in by_driver.items()
        }
    
    async def get_drivers(self, session_key: int) -> List[Dict]:
        """Get driver information for a session."""
        return await self._request("drivers", {"session_key": session_key})
    
    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()


# Global client instance
openf1_client = OpenF1Client()
