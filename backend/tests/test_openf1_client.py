"""
Integration tests for OpenF1 API client.
"""
import pytest
from app.openf1_client import OpenF1Client, OpenF1APIError


@pytest.fixture
def client(monkeypatch):
    """A client whose HTTP layer is replaced by canned endpoint payloads."""
    instance = OpenF1Client()
    instance.requests = []
    payloads = {}

    async def fake_request(endpoint, params=None):
        instance.requests.append((endpoint, params))
        return payloads.get(endpoint, [])

    monkeypatch.setattr(instance, "_request", fake_request)
    instance.payloads = payloads
    return instance


@pytest.mark.asyncio
async def test_intervals_hit_the_intervals_endpoint(client):
    """Gaps live on /intervals - /position does not carry them."""
    await client.get_intervals(9999)

    assert client.requests == [("intervals", {"session_key": 9999})]


@pytest.mark.asyncio
async def test_latest_intervals_keeps_newest_row_per_driver(client):
    client.payloads["intervals"] = [
        {"driver_number": 44, "interval": 0.9, "date": "2026-03-05T14:30:00Z"},
        {"driver_number": 1, "interval": None, "date": "2026-03-05T14:30:00Z"},
        {"driver_number": 44, "interval": 0.4, "date": "2026-03-05T14:30:04Z"},
        # Out of order on purpose - ordering must not be assumed
        {"driver_number": 44, "interval": 0.7, "date": "2026-03-05T14:30:02Z"},
    ]

    rows = await client.get_latest_intervals(9999)
    by_driver = {r["driver_number"]: r for r in rows}

    assert len(rows) == 2
    assert by_driver[44]["interval"] == 0.4


@pytest.mark.asyncio
async def test_latest_intervals_skips_rows_without_driver_number(client):
    client.payloads["intervals"] = [
        {"interval": 0.5, "date": "2026-03-05T14:30:00Z"},
        {"driver_number": 44, "interval": 0.5, "date": "2026-03-05T14:30:00Z"},
    ]

    assert len(await client.get_latest_intervals(9999)) == 1


@pytest.mark.asyncio
async def test_latest_laps_returns_last_completed_laps_oldest_first(client):
    client.payloads["laps"] = [
        {"driver_number": 44, "lap_number": 9, "lap_duration": 91.0},
        {"driver_number": 44, "lap_number": 10, "lap_duration": 90.5},
        {"driver_number": 44, "lap_number": 11, "lap_duration": 89.9},
        # The lap in progress has no duration yet
        {"driver_number": 44, "lap_number": 12, "lap_duration": None},
        {"driver_number": 1, "lap_number": 11, "lap_duration": 90.1},
    ]

    laps = await client.get_latest_laps(9999, count=2)

    assert [lap["lap_duration"] for lap in laps[44]] == [90.5, 89.9]
    assert [lap["lap_duration"] for lap in laps[1]] == [90.1]


@pytest.mark.asyncio
async def test_latest_positions_ignores_response_ordering(client):
    client.payloads["position"] = [
        {"driver_number": 44, "position": 3, "date": "2026-03-05T14:30:05Z"},
        {"driver_number": 44, "position": 2, "date": "2026-03-05T14:30:01Z"},
    ]

    positions = await client.get_latest_positions(9999)

    assert positions == [{"driver_number": 44, "position": 3, "date": "2026-03-05T14:30:05Z"}]


@pytest.mark.asyncio
async def test_missing_data_handling(client):
    """Empty payloads flow through as empty lists, not exceptions."""
    assert await client.get_latest_intervals(9999) == []
    assert await client.get_latest_laps(9999) == {}
    assert await client.get_latest_positions(9999) == []


@pytest.mark.asyncio
async def test_openf1_client_retry_logic(monkeypatch):
    """Test that client retries on failures."""
    import httpx

    instance = OpenF1Client()
    attempts = []

    async def failing_get(url, params=None):
        attempts.append(url)
        raise httpx.RequestError("connection reset")

    monkeypatch.setattr(instance.client, "get", failing_get)

    with pytest.raises(OpenF1APIError):
        await instance.get_intervals(9999)

    assert len(attempts) == instance.max_retries
