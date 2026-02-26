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


class OpenF1Client:
    """Client for interacting with the OpenF1 API."""
    
    def __init__(self):
        self.base_url = config.OPENF1_BASE_URL
        self.timeout = config.OPENF1_TIMEOUT_S
        self.max_retries = config.OPENF1_MAX_RETRIES
        self.client = httpx.AsyncClient(timeout=self.timeout)
    
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
        
        # Group by driver_number and take the latest (last) entry for each
        latest_by_driver = {}
        for pos in positions:
            driver_num = pos.get("driver_number")
            if driver_num:
                latest_by_driver[driver_num] = pos
        
        return list(latest_by_driver.values())
    
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
    
    async def get_drivers(self, session_key: int) -> List[Dict]:
        """Get driver information for a session."""
        return await self._request("drivers", {"session_key": session_key})
    
    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()


# Global client instance
openf1_client = OpenF1Client()
