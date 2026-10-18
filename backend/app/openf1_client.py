"""
OpenF1 API client with retry logic and error handling.
"""
import asyncio
import httpx
import logging
from typing import Any, Callable, Dict, List, Optional
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


# A Retry-After longer than this is not worth honouring in-process: ingest
# would sit silent for minutes, and a live poll that late is useless anyway.
MAX_RETRY_AFTER_S = 120.0


class OpenF1Client:
    """Client for interacting with the OpenF1 API."""

    def __init__(self, sleep: Callable = asyncio.sleep):
        self.base_url = config.OPENF1_BASE_URL
        self.timeout = config.OPENF1_TIMEOUT_S
        self.max_retries = config.OPENF1_MAX_RETRIES
        self.retry_base_s = config.OPENF1_RETRY_BASE_S
        self._sleep = sleep
        # OpenF1 publishes no free-tier rate limit, so identify the caller
        # rather than arriving as an anonymous default-UA client. If ingest
        # ever needs throttling, this is the string they would throttle.
        headers = {"User-Agent": config.OPENF1_USER_AGENT}
        # Live data needs OpenF1's paid Sponsor tier. Historical data is free
        # and unauthenticated, so the header is simply absent without a token.
        if config.OPENF1_API_TOKEN:
            headers["Authorization"] = f"Bearer {config.OPENF1_API_TOKEN}"
        self._headers = headers
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def client(self) -> httpx.AsyncClient:
        """
        The HTTP client, built on first use.

        Building it loads a TLS context, ~0.3s at import on a cold serverless
        instance - and replay and mock never make a request at all.
        """
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                headers=self._headers,
                verify=config.OPENF1_CA_BUNDLE or True,
            )
        return self._client

    @client.setter
    def client(self, value: httpx.AsyncClient) -> None:
        self._client = value

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
                status = e.response.status_code
                logger.warning(f"HTTP error {status}: {e}")
                # A 4xx is the server saying the request itself is wrong, and
                # repeating it verbatim cannot change that - it just costs
                # OpenF1 two more requests. Rate limiting is the exception:
                # 429 is a "not yet", which is exactly what backoff is for.
                if (400 <= status < 500 and status != 429) or attempt == self.max_retries - 1:
                    raise OpenF1APIError(f"HTTP {status}") from e
                await self._sleep(self._retry_delay(attempt, e.response))

            except httpx.RequestError as e:
                logger.warning(f"Request error: {e}")
                if attempt == self.max_retries - 1:
                    raise OpenF1APIError(f"Request failed: {e}") from e
                await self._sleep(self._retry_delay(attempt))

            except Exception as e:
                logger.error(f"Unexpected error: {e}")
                if attempt == self.max_retries - 1:
                    raise OpenF1APIError(f"Unexpected error: {e}") from e
                await self._sleep(self._retry_delay(attempt))

        return []

    def _retry_delay(self, attempt: int, response: Optional[httpx.Response] = None) -> float:
        """
        How long to wait before the next attempt: doubling from retry_base_s,
        or what a 429's Retry-After asks for. Retrying instantly - which this
        client did until 1.7 - turns one rate-limit response into three.
        """
        if response is not None and response.status_code == 429:
            retry_after = response.headers.get("Retry-After", "")
            try:
                return min(MAX_RETRY_AFTER_S, max(0.0, float(retry_after)))
            except ValueError:
                pass  # absent, or an HTTP date; fall back to doubling
        return self.retry_base_s * (2 ** attempt)

    async def get_endpoint(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> List[Dict]:
        """
        Fetch any OpenF1 endpoint by name.

        The typed helpers below cover what live polling needs. Ingest (1.1)
        pulls eleven endpoints for one race, most of which the live path never
        touches, and wrapping each in its own method would add no meaning.
        """
        return await self._request(endpoint, params or {})

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
        """Close the HTTP client, if one was ever built."""
        if self._client is not None:
            await self._client.aclose()


# Global client instance
openf1_client = OpenF1Client()
